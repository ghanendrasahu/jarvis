from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.config import Settings
from app.llm.chat import (
    ChatMessage,
    ChatService,
    ChatStreamError,
    ProviderSpec,
    build_history_from_memory,
)

# --------------------------------------------------------------------------- fakes


class FakeProvider:
    """Scriptable chat provider for tests.

    behavior:
      - "ok"                  -> streams "Hello", " ", "world"
      - ("deltas", [..])      -> streams the given deltas
      - ("fail", kind)        -> raises ChatStreamError(kind)
      - ("slow", seconds, then) -> sleeps, then behaves according to `then`
      - "cancel_aware"        -> yields one delta, then honours cancellation
      - "empty"               -> yields nothing
    """

    def __init__(self, spec: ProviderSpec, behavior: Any = "ok") -> None:
        self.spec = spec
        self.behavior = behavior
        self.calls = 0
        self.seen_messages: list[list[dict[str, Any]]] = []

    def _resolve(self, cancel_event: threading.Event | None) -> Iterator[dict[str, Any]]:
        behavior = self.behavior
        if isinstance(behavior, tuple):
            tag = behavior[0]
            args = behavior[1:]
        else:
            tag, args = behavior, ()

        if tag == "fail":
            raise ChatStreamError(str(args[0]) if args else "http")
            yield  # pragma: no cover - generator formalities
        if tag == "slow":
            time.sleep(float(args[0]))
            inner = FakeProvider(self.spec, args[1] if len(args) > 1 else "ok")._resolve(cancel_event)
            yield from inner
            return
        if tag == "cancel_aware":
            yield {"delta": "par"}
            if cancel_event is not None:
                for _ in range(400):
                    if cancel_event.is_set():
                        raise ChatStreamError("cancelled")
                    time.sleep(0.005)
            yield {"delta": "tial"}
            yield {"done": True}
            return
        if tag == "empty":
            yield {"done": True}
            return
        deltas = ["Hello", " ", "world"]
        for delta in deltas:
            yield {"delta": delta}
        yield {"done": True}

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_s: float = 30.0,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.calls += 1
        self.seen_messages.append(messages)
        return self._resolve(cancel_event)


def make_service(monkeypatch: pytest.MonkeyPatch, providers: dict[str, FakeProvider], **settings_kwargs: Any) -> ChatService:
    settings = Settings(
        llm_provider="groq",
        jarvis_chat_provider=settings_kwargs.pop("jarvis_chat_provider", "opencode"),
        jarvis_chat_model=settings_kwargs.pop("jarvis_chat_model", "test-primary"),
        jarvis_chat_fallback_1=settings_kwargs.pop("jarvis_chat_fallback_1", ""),
        jarvis_chat_fallback_2=settings_kwargs.pop("jarvis_chat_fallback_2", ""),
        jarvis_chat_fallback_3=settings_kwargs.pop("jarvis_chat_fallback_3", ""),
        jarvis_chat_fallback_providers=[],
        jarvis_chat_timeout_seconds=5,
        jarvis_chat_max_retries=settings_kwargs.pop("jarvis_chat_max_retries", 0),
        jarvis_chat_deadline_seconds=settings_kwargs.pop("jarvis_chat_deadline_seconds", 30),
        jarvis_chat_history_window=settings_kwargs.pop("jarvis_chat_history_window", 12),
    )
    assert not settings_kwargs, f"unused settings kwargs: {settings_kwargs}"

    def factory(spec: ProviderSpec, _settings: Settings) -> Any:
        return providers[spec.name]

    monkeypatch.setattr(ChatService, "_interruptible_backoff", staticmethod(lambda *_a, **_k: None))
    return ChatService(settings, provider_factory=factory)


def run(service: ChatService, text: str = "hi there", history: list[ChatMessage] | None = None, cancel: threading.Event | None = None) -> list[dict[str, Any]]:
    return list(service.stream_chat(text, history or [], cancel))


# --------------------------------------------------------------------------- tests


def test_primary_success_streams_and_reports_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    primary = FakeProvider(ProviderSpec("opencode", "m1"))
    service = make_service(monkeypatch, {"opencode": primary}, jarvis_chat_model="m1")
    events = run(service)

    assert [e["type"] for e in events].count("delta") == 3
    done = events[-1]
    assert done["type"] == "done"
    assert done["text"] == "Hello world"
    assert done["stopped"] is False
    meta = done["meta"]
    assert meta["provider"] == "opencode"
    assert meta["model"] == "m1"
    assert meta["fallback_used"] is False
    assert meta["first_token_ms"] >= 0
    assert meta["total_ms"] >= 0
    assert meta["output_tokens"] >= 1


def test_history_passed_with_system_first_user_last(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider(ProviderSpec("opencode", "m1"))
    service = make_service(
        monkeypatch,
        {"opencode": provider},
        jarvis_chat_history_window=2,
    )
    history = [
        ChatMessage(role=role, content=content)
        for role, content in [
            ("user", "u1"), ("assistant", "a1"),
            ("user", "u2"), ("assistant", "a2"),
            ("user", "u3"), ("assistant", "a3"),
        ]
    ]
    run(service, "new question", history)

    messages = provider.seen_messages[0]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith("You are JARVIS")
    # window=2 turns -> at most 4 history messages + live user message
    assert len(messages) <= 1 + 4 + 1
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "new question"
    contents = [str(m.get("content")) for m in messages]
    assert "u1" not in contents and "a1" not in contents, "history must be trimmed to the window"


def test_first_delta_sets_first_token_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    slow = FakeProvider(ProviderSpec("opencode", "m1"), ("slow", 0.05, "ok"))
    service = make_service(monkeypatch, {"opencode": slow})
    events = run(service)
    assert events[-1]["meta"]["first_token_ms"] >= 40


def test_timeout_on_primary_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    primary = FakeProvider(ProviderSpec("opencode", "m1"), ("fail", "timeout"))
    fallback = FakeProvider(ProviderSpec("groq", "m2"))
    service = make_service(
        monkeypatch,
        {"opencode": primary, "groq": fallback},
        jarvis_chat_fallback_1="groq:m2",
    )
    events = run(service)

    assert primary.calls == 1, "retries are disabled in this configuration"
    assert any(e["type"] == "delta" for e in events)
    assert events[-1]["type"] == "done"
    assert events[-1]["meta"]["fallback_used"] is True
    assert events[-1]["meta"]["provider"] == "groq"


def test_rate_limit_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    primary = FakeProvider(ProviderSpec("opencode", "m1"), ("fail", "rate_limit"))
    fallback = FakeProvider(ProviderSpec("groq", "m2"))
    service = make_service(monkeypatch, {"opencode": primary, "groq": fallback}, jarvis_chat_fallback_1="groq:m2")
    events = run(service)
    assert events[-1]["type"] == "done"
    assert events[-1]["meta"]["provider"] == "groq"


def test_auth_failure_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    primary = FakeProvider(ProviderSpec("opencode", "m1"), ("fail", "auth"))
    fallback = FakeProvider(ProviderSpec("groq", "m2"))
    service = make_service(
        monkeypatch,
        {"opencode": primary, "groq": fallback},
        jarvis_chat_max_retries=3,
        jarvis_chat_fallback_1="groq:m2",
    )
    events = run(service)
    assert primary.calls == 1, "auth errors must skip straight to the next provider"
    assert events[-1]["type"] == "done"


def test_all_providers_fail_yields_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    a = FakeProvider(ProviderSpec("opencode", "m1"), ("fail", "timeout"))
    b = FakeProvider(ProviderSpec("groq", "m2"), ("fail", "rate_limit"))
    service = make_service(
        monkeypatch,
        {"opencode": a, "groq": b},
        jarvis_chat_model="m1",
        jarvis_chat_fallback_1="groq:m2",
    )

    assert len(service.chain) == 2
    events = run(service)
    last = events[-1]
    assert last["type"] == "error"
    assert "failed" in last["message"].lower()
    assert set(last.get("providers_tried", [])) == {"opencode:m1", "groq:m2"}
    assert not any(e["type"] == "done" for e in events)


def test_empty_response_treated_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    primary = FakeProvider(ProviderSpec("opencode", "m1"), "empty")
    fallback = FakeProvider(ProviderSpec("groq", "m2"))
    service = make_service(monkeypatch, {"opencode": primary, "groq": fallback}, jarvis_chat_fallback_1="groq:m2")
    events = run(service)
    assert events[-1]["type"] == "done"
    assert events[-1]["meta"]["provider"] == "groq"


def test_cancellation_returns_partial_text(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider(ProviderSpec("opencode", "m1"), "cancel_aware")
    service = make_service(monkeypatch, {"opencode": provider})
    cancel = threading.Event()
    events: list[dict[str, Any]] = []

    def canceller() -> None:
        deadline = time.monotonic() + 2.0
        while not cancel.is_set() and time.monotonic() < deadline:
            time.sleep(0.002)
        cancel.set()

    thread = threading.Thread(target=canceller)
    thread.start()
    try:
        for event in service.stream_chat("hello", [], cancel):
            events.append(event)
            if event["type"] == "done":
                break
    finally:
        thread.join()

    done = events[-1]
    assert done["type"] == "done"
    assert done["stopped"] is True
    assert done["text"] == "par"
    assert provider.calls == 1


def test_deadline_blocks_later_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    primary = FakeProvider(ProviderSpec("opencode", "m1"), ("slow", 1.2, ("fail", "timeout")))
    fallback = FakeProvider(ProviderSpec("groq", "m2"))
    service = make_service(
        monkeypatch,
        {"opencode": primary, "groq": fallback},
        jarvis_chat_deadline_seconds=1,
        jarvis_chat_fallback_1="groq:m2",
    )
    started = time.perf_counter()
    events = run(service)
    elapsed = time.perf_counter() - started

    assert events[-1]["type"] == "error"
    assert fallback.calls == 0, "deadline must prevent later providers from starting"
    assert elapsed < 4.0


def test_no_retry_storm_with_default_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    flaky = FakeProvider(ProviderSpec("opencode", "m1"), ("fail", "timeout"))
    service = make_service(monkeypatch, {"opencode": flaky}, jarvis_chat_max_retries=1)
    events = run(service)
    assert flaky.calls == 2, "max_retries=1 means exactly one retry, never more"
    assert events[-1]["type"] == "error"


def test_vision_gating_on_message_payload() -> None:
    msg = ChatMessage(role="user", content="look here")
    assert msg.to_api(vision=True)["content"] == "look here"

    data_msg = ChatMessage(role="user", content="look", images=["data:image/png;base64,AAA"])
    payload = data_msg.to_api(vision=True)
    assert isinstance(payload["content"], list)
    assert payload["content"][0] == {"type": "text", "text": "look"}
    assert payload["content"][1]["type"] == "image_url"

    stripped = data_msg.to_api(vision=False)
    assert stripped["content"] == "look"


def test_build_history_from_memory_shapes_pairs() -> None:
    class Mem:
        def recent_chat(self, limit: int = 12) -> list[dict[str, Any]]:
            # Mirrors MemoryStore.recent_chat: newest-first query, returned oldest-first.
            return [
                {"command": "q0", "response_text": "a0"},
                {"command": "q1", "response_text": ""},
                {"command": "q2", "response_text": "a2"},
            ]

    msgs = build_history_from_memory(Mem(), limit=12)
    roles = [(m.role, m.content) for m in msgs]
    # oldest first; a user turn with no assistant reply is still kept
    assert roles == [
        ("user", "q0"),
        ("assistant", "a0"),
        ("user", "q1"),
        ("user", "q2"),
        ("assistant", "a2"),
    ]
