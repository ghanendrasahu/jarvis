from __future__ import annotations

import base64
import io
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.llm.chat import (
    ChatMessage,
    ChatService,
    ChatStreamError,
    ProviderSpec,
    _image_to_data_url,
    build_history_from_memory,
)

# --------------------------------------------------------------------------- fakes


class FakeProvider:
    """Scriptable chat provider for tests.

    behavior:
      - "ok"                  -> streams "Hello", " ", "world"
      - ("deltas", [..])      -> streams the given deltas
      - ("fail", kind)        -> raises ChatStreamError(kind)
      - ("partial_fail", [deltas], kind) -> streams deltas then raises
      - ("slow", seconds, then) -> sleeps, then behaves according to `then`
      - "cancel_aware"        -> yields one delta, then honours cancellation
      - ("hold_first", event) -> blocks until `event` (or cancel) before first delta
      - ("stall_forever", started_event?) -> silent stalled read; never emits
      - "empty"               -> yields nothing
    """

    def __init__(self, spec: ProviderSpec, behavior: Any = "ok") -> None:
        self.spec = spec
        self.behavior = behavior
        self.calls = 0
        self.interrupted = False
        self.seen_messages: list[list[dict[str, Any]]] = []

    def interrupt(self) -> None:
        """Mirror OpenAICompatChatProvider.interrupt: unblock a stalled read."""
        self.interrupted = True

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
        if tag == "partial_fail":
            for delta in args[0]:
                yield {"delta": delta}
            raise ChatStreamError(str(args[1]) if len(args) > 1 else "http")
            yield  # pragma: no cover - generator formalities
        if tag == "hold_first":
            started: threading.Event = args[0]
            started.set()
            while not (cancel_event is not None and cancel_event.is_set()):
                time.sleep(0.01)
            raise ChatStreamError("cancelled")
            yield  # pragma: no cover - generator formalities
        if tag == "stall_forever":
            started_ev: threading.Event = args[0] if args else threading.Event()
            started_ev.set()
            time.sleep(600)  # simulate a silent, stalled HTTP read; interrupt() closes it
            yield {"delta": "never"}  # pragma: no cover - unreachable in tests
            yield {"done": True}  # pragma: no cover
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
        if tag == "deltas":
            for delta in args[0]:
                yield {"delta": delta}
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
        jarvis_chat_vision_enabled=settings_kwargs.pop("jarvis_chat_vision_enabled", False),
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


# --------------------------------------------------------------------------- B1: mid-stream fallback duplication


def test_midstream_failure_discards_partial_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """B1 regression: a failed attempt's partial tokens must never contaminate the fallback."""
    primary = FakeProvider(
        ProviderSpec("opencode", "m1"), ("partial_fail", ["Hello ", "partial"], "connection")
    )
    fallback = FakeProvider(ProviderSpec("groq", "m2"), ("deltas", ["Hello ", "world"]))
    service = make_service(
        monkeypatch,
        {"opencode": primary, "groq": fallback},
        jarvis_chat_fallback_1="groq:m2",
    )
    events = run(service)

    done = events[-1]
    assert done["type"] == "done"
    assert done["text"] == "Hello world"
    assert "partial" not in done["text"]
    assert "Hello Hello" not in done["text"]
    assert done["meta"]["provider"] == "groq"

    types = [e["type"] for e in events]
    assert types.count("stream_reset") == 1
    last_failed_delta = max(i for i, e in enumerate(events) if e.get("provider") == "opencode")
    reset_idx = types.index("stream_reset")
    first_fallback_delta = min(i for i, e in enumerate(events) if e.get("provider") == "groq")
    assert last_failed_delta < reset_idx < first_fallback_delta


def test_failure_before_first_token_preserves_existing_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    primary = FakeProvider(ProviderSpec("opencode", "m1"), ("fail", "timeout"))
    fallback = FakeProvider(ProviderSpec("groq", "m2"), ("deltas", ["Fallback ", "reply"]))
    service = make_service(
        monkeypatch,
        {"opencode": primary, "groq": fallback},
        jarvis_chat_fallback_1="groq:m2",
    )
    events = run(service)

    done = events[-1]
    assert done["type"] == "done"
    assert done["text"] == "Fallback reply"
    assert done["meta"]["fallback_used"] is True
    # nothing was streamed before the failure, so there is nothing to reset
    assert not any(e["type"] == "stream_reset" for e in events)


def test_multiple_partial_failures_isolate_each_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    a = FakeProvider(ProviderSpec("opencode", "m1"), ("partial_fail", ["AAA "], "timeout"))
    b = FakeProvider(ProviderSpec("groq", "m2"), ("partial_fail", ["BBB "], "rate_limit"))
    c = FakeProvider(ProviderSpec("cerebras", "m3"), ("deltas", ["Final answer"]))
    service = make_service(
        monkeypatch,
        {"opencode": a, "groq": b, "cerebras": c},
        jarvis_chat_model="m1",
        jarvis_chat_fallback_1="groq:m2",
        jarvis_chat_fallback_2="cerebras:m3",
    )
    events = run(service)

    done = events[-1]
    assert done["type"] == "done"
    assert done["text"] == "Final answer"
    assert "AAA" not in done["text"]
    assert "BBB" not in done["text"]
    assert done["meta"]["provider"] == "cerebras"
    assert sum(1 for e in events if e["type"] == "stream_reset") == 2


def test_all_providers_fail_after_partial_output_keeps_error_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    a = FakeProvider(ProviderSpec("opencode", "m1"), ("partial_fail", ["AAA "], "timeout"))
    b = FakeProvider(ProviderSpec("groq", "m2"), ("partial_fail", ["BBB "], "rate_limit"))
    service = make_service(
        monkeypatch,
        {"opencode": a, "groq": b},
        jarvis_chat_model="m1",
        jarvis_chat_fallback_1="groq:m2",
    )
    events = run(service)

    last = events[-1]
    assert last["type"] == "error"
    assert "failed" in last["message"].lower()
    assert set(last.get("providers_tried", [])) == {"opencode:m1", "groq:m2"}
    assert not any(e["type"] == "done" for e in events), "failed partial text must never be returned as a successful response"
    # a reset may fire between two partially-streamed failures, but none dangles after the last one
    assert sum(1 for e in events if e["type"] == "stream_reset") == 1


def test_retry_same_provider_discards_first_attempt_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    class FlakyThenOk(FakeProvider):
        def _resolve(self, cancel_event: threading.Event | None) -> Iterator[dict[str, Any]]:
            if self.calls == 1:
                yield {"delta": "Hello "}
                yield {"delta": "partial"}
                raise ChatStreamError("timeout")
            yield from super()._resolve(cancel_event)

    flaky = FlakyThenOk(ProviderSpec("opencode", "m1"))
    service = make_service(monkeypatch, {"opencode": flaky}, jarvis_chat_max_retries=1)
    events = run(service)

    done = events[-1]
    assert done["type"] == "done"
    assert done["text"] == "Hello world"
    assert "partial" not in done["text"]
    assert flaky.calls == 2
    assert sum(1 for e in events if e["type"] == "stream_reset") == 1


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


# --------------------------------------------------------------------------- B2: STOP latency during stalled reads


def wait_until(predicate: Any, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def collect(service: ChatService, cancel: threading.Event | None, done: threading.Event) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def consume() -> None:
        try:
            for event in service.stream_chat("hi there", [], cancel):
                events.append(event)
        finally:
            done.set()

    threading.Thread(target=consume, daemon=True, name="test-consumer").start()
    return events


def test_cancel_during_silent_stall_is_responsive(monkeypatch: pytest.MonkeyPatch) -> None:
    """B2 core regression: STOP must land even when the provider emits no chunks."""
    started = threading.Event()
    primary = FakeProvider(ProviderSpec("opencode", "m1"), ("stall_forever", started))
    fallback = FakeProvider(ProviderSpec("groq", "m2"))
    service = make_service(
        monkeypatch,
        {"opencode": primary, "groq": fallback},
        jarvis_chat_fallback_1="groq:m2",
    )
    cancel = threading.Event()
    finished = threading.Event()
    events = collect(service, cancel, finished)

    assert started.wait(5.0), "provider never began its stalled read"
    t0 = time.perf_counter()
    cancel.set()
    assert finished.wait(5.0), "STOP did not take effect while the read was stalled"
    elapsed = time.perf_counter() - t0

    assert elapsed < 4.0, "cancellation must not wait for the provider read timeout"
    done = events[-1]
    assert done["type"] == "done" and done["stopped"] is True
    assert primary.calls == 1
    assert fallback.calls == 0, "cancelled attempt must never reach the fallback provider"
    assert primary.interrupted, "stalled HTTP response must be closed so the pump exits"


def test_cancel_before_first_token_returns_empty_stopped_response(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    primary = FakeProvider(ProviderSpec("opencode", "m1"), ("hold_first", entered))
    fallback = FakeProvider(ProviderSpec("groq", "m2"))
    service = make_service(
        monkeypatch,
        {"opencode": primary, "groq": fallback},
        jarvis_chat_fallback_1="groq:m2",
    )
    cancel = threading.Event()
    finished = threading.Event()
    events = collect(service, cancel, finished)

    assert entered.wait(5.0)
    cancel.set()
    assert finished.wait(5.0)

    assert len(events) == 1
    assert events[0]["type"] == "done"
    assert events[0]["stopped"] is True
    assert events[0]["text"] == ""
    assert primary.calls == 1 and fallback.calls == 0


def test_cancel_does_not_retry_or_advance_to_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    primary = FakeProvider(ProviderSpec("opencode", "m1"), ("stall_forever", started))
    fallback = FakeProvider(ProviderSpec("groq", "m2"))
    service = make_service(
        monkeypatch,
        {"opencode": primary, "groq": fallback},
        jarvis_chat_max_retries=2,
        jarvis_chat_fallback_1="groq:m2",
    )
    cancel = threading.Event()
    finished = threading.Event()
    events = collect(service, cancel, finished)

    assert started.wait(5.0)
    cancel.set()
    assert finished.wait(5.0)

    assert primary.calls == 1, "cancellation must not consume retry attempts"
    assert fallback.calls == 0
    assert events[-1]["stopped"] is True


def test_deadline_terminates_silent_stall_without_user_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    primary = FakeProvider(ProviderSpec("opencode", "m1"), ("stall_forever", started))
    fallback = FakeProvider(ProviderSpec("groq", "m2"))
    service = make_service(
        monkeypatch,
        {"opencode": primary, "groq": fallback},
        jarvis_chat_deadline_seconds=1,
        jarvis_chat_fallback_1="groq:m2",
    )
    t0 = time.perf_counter()
    events = run(service)
    elapsed = time.perf_counter() - t0

    assert events[-1]["type"] == "error", "deadline stays authoritative for a stalled request"
    assert "failed" in events[-1]["message"].lower()
    assert elapsed < 6.0, f"stalled request must be bounded by deadline, took {elapsed:.1f}s"
    assert primary.interrupted, "stalled response closed on deadline cleanup"
    assert fallback.calls == 0, "deadline must prevent later providers from starting"


def test_openai_provider_interrupt_closes_active_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """interrupt() closes the live HTTP response; the stalled read then fails fast."""
    from types import SimpleNamespace

    from app.llm.chat import OpenAICompatChatProvider

    class _Delta:
        content = "Hi "

    class _Choice:
        delta = _Delta()

    class _Chunk:
        choices = [_Choice()]

    class _StubResponse:
        def __init__(self) -> None:
            self.closed = False
            self._emitted = False

        def __iter__(self):
            return self

        def __next__(self):
            if self.closed:
                raise RuntimeError("connection aborted")
            if self._emitted:
                time.sleep(300)  # stalled read; only close() ends it
            self._emitted = True
            return _Chunk()

        def close(self) -> None:
            self.closed = True

    stub = _StubResponse()
    spec = ProviderSpec("opencode", "m1")
    provider = OpenAICompatChatProvider(spec, "http://localhost/v1", "k")
    monkeypatch.setattr(
        provider,
        "_client",
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kw: stub))),
    )

    gen = provider.stream([{"role": "user", "content": "hi"}], timeout_s=30, cancel_event=None)
    assert next(gen) == {"delta": "Hi "}
    assert provider._active_response is stub

    provider.interrupt()
    assert stub.closed, "interrupt must close the underlying response"

    with pytest.raises(ChatStreamError) as excinfo:
        next(gen)
    assert excinfo.value.kind in {"connection", "timeout", "http"}
    assert provider._active_response is None, "finally must clear the active response"


def test_repeated_cancel_calls_are_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    primary = FakeProvider(ProviderSpec("opencode", "m1"), ("hold_first", entered))
    service = make_service(monkeypatch, {"opencode": primary})
    cancel = threading.Event()
    finished = threading.Event()
    events = collect(service, cancel, finished)

    assert entered.wait(5.0)
    cancel.set()
    cancel.set()
    cancel.set()
    assert finished.wait(5.0)

    done_events = [e for e in events if e["type"] == "done"]
    assert len(done_events) == 1, "repeated STOP must produce exactly one stopped response"
    assert not any(e["type"] == "error" for e in events), "cancellation must never become an error"


# --------------------------------------------------------------------------- B3: image MIME/payload consistency

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _write_image(tmp_path: Path, fmt: str) -> Path:
    from PIL import Image

    path = tmp_path / f"img.{fmt.lower()}"
    pil_format = {"jpg": "JPEG", "jpeg": "JPEG"}.get(fmt.lower(), fmt.upper())
    if pil_format == "JPEG":
        Image.new("RGB", (8, 8), (200, 30, 30)).save(path, format="JPEG")
    else:
        Image.new("RGBA", (8, 8), (200, 30, 30, 255)).save(path, format=pil_format)
    return path


def _split_data_url(url: str) -> tuple[str, bytes]:
    assert url.startswith("data:")
    header, payload_b64 = url.split(",", 1)
    mime = header[len("data:") :].split(";", 1)[0]
    return mime, base64.b64decode(payload_b64)


@pytest.mark.parametrize("fmt", ["jpg", "jpeg", "png"])
def test_image_payload_signature_matches_mime(tmp_path: Path, fmt: str) -> None:
    """The advertised MIME must match the decoded magic bytes of the payload."""
    url = _image_to_data_url(str(_write_image(tmp_path, fmt)))
    assert url is not None

    mime, payload = _split_data_url(url)
    assert mime == "image/png", "normalized encoding must be labeled PNG"
    assert payload.startswith(PNG_SIGNATURE), ".jpg/.jpeg inputs must no longer ship PNG bytes under image/jpeg"

    from PIL import Image

    decoded = Image.open(io.BytesIO(payload))
    assert decoded.format == "PNG", "payload must be decodable as the labeled format"
    assert decoded.size == (8, 8)


def test_png_input_stays_png_labeled(tmp_path: Path) -> None:
    url = _image_to_data_url(str(_write_image(tmp_path, "png")))
    assert url is not None
    mime, payload = _split_data_url(url)
    assert mime == "image/png" and payload.startswith(PNG_SIGNATURE)


def test_vision_request_parts_carry_matching_payloads(tmp_path: Path) -> None:
    jpg = _write_image(tmp_path, "jpg")
    png = _write_image(tmp_path, "png")
    msg = ChatMessage(role="user", content="what is this?", images=[str(jpg), str(png)])

    parts = msg.to_api(vision=True)["content"]
    assert parts[0] == {"type": "text", "text": "what is this?"}
    assert [p["type"] for p in parts[1:]] == ["image_url", "image_url"]

    for part in parts[1:]:
        mime, payload = _split_data_url(part["image_url"]["url"])
        assert mime == "image/png"
        assert payload.startswith(PNG_SIGNATURE)
        assert part["image_url"]["url"].startswith("data:image/png;base64,")


def test_unreadable_image_fails_cleanly_and_is_dropped(tmp_path: Path) -> None:
    garbage = tmp_path / "broken.jpg"
    garbage.write_bytes(b"this is not an image at all")
    missing = tmp_path / "missing.png"

    assert _image_to_data_url(str(garbage)) is None
    assert _image_to_data_url(str(missing)) is None

    msg = ChatMessage(role="user", content="look", images=[str(garbage), str(missing)])
    content = msg.to_api(vision=True)["content"]
    assert content == [{"type": "text", "text": "look"}], "unreadable images are dropped, text remains"


def test_remote_and_data_urls_pass_through_untouched() -> None:
    remote = ChatMessage(role="user", content="", images=["https://example.com/cat.jpg"])
    parts = remote.to_api(vision=True)["content"]
    assert parts == [{"type": "image_url", "image_url": {"url": "https://example.com/cat.jpg"}}]

    data = ChatMessage(role="user", content="", images=["data:image/jpeg;base64,QUJD"])
    parts = data.to_api(vision=True)["content"]
    assert parts == [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}]


def test_vision_disabled_keeps_text_only_content(tmp_path: Path) -> None:
    img = _write_image(tmp_path, "jpg")
    msg = ChatMessage(role="user", content="describe this", images=[str(img)])
    assert msg.to_api(vision=False) == {"role": "user", "content": "describe this"}


def test_provider_request_structure_compatible_with_openai(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    jpg = _write_image(tmp_path, "jpg")
    primary = FakeProvider(ProviderSpec("opencode", "m1"))
    service = make_service(
        monkeypatch,
        {"opencode": primary},
        jarvis_chat_history_window=4,
        jarvis_chat_vision_enabled=True,
    )
    # Reuse the internal message builder through stream_chat's history plumbing.
    history = [
        ChatMessage(role="user", content="see attachment", images=[str(jpg)]),
        ChatMessage(role="assistant", content="ok"),
    ]
    run(service, "and this one?", history)

    sent = primary.seen_messages[-1]
    user_turns = [m for m in sent if m.get("role") == "user"]
    assert len(user_turns) >= 2
    history_turn, live_turn = user_turns[0], user_turns[-1]

    content = history_turn["content"]
    assert isinstance(content, list)
    text_parts = [p for p in content if p["type"] == "text"]
    image_parts = [p for p in content if p["type"] == "image_url"]
    assert text_parts and text_parts[0]["text"] == "see attachment"
    assert len(image_parts) == 1
    url = image_parts[0]["image_url"]["url"]
    mime, payload = _split_data_url(url)
    assert mime == "image/png"
    assert payload.startswith(PNG_SIGNATURE)

    assert live_turn["content"] == "and this one?", "text-only turns stay plain strings"


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
