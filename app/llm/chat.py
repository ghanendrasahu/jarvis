"""Dedicated AI-assistant chat layer: streaming providers + fallback service.

This module is intentionally independent from the planner/orchestrator stack.
Ordinary conversation flows: ChatService -> ChatProvider -> streamed deltas.
Commands/tools keep using the existing command pipeline (see app/core/router).
"""

from __future__ import annotations

import base64
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings
from app.core.logging import get_logger

logger = get_logger("chat")

CHAT_SYSTEM_PROMPT = (
    "You are JARVIS, an intelligent desktop AI assistant with a calm, precise personality.\n"
    "- Behave like a modern conversational assistant: fast, accurate, friendly, concise.\n"
    "- Use light Markdown (headings, lists, **bold**, code blocks) when it aids readability.\n"
    "- You naturally remember the recent conversation history provided to you.\n"
    "- You cannot execute tools, apps, or OS actions in this chat mode. If the user asks\n"
    "  for an action, briefly answer and suggest phrasing it as a command (e.g. 'open chrome').\n"
    "- Never claim you performed an action you did not perform.\n"
    "- If you are unsure or lack information, say so clearly instead of guessing."
)

DEFAULT_CHAT_MODELS: dict[str, str] = {
    "opencode": "nemotron-3-ultra-free",
    "zen": "nemotron-3-ultra-free",
    "groq": "llama-3.1-8b-instant",
    "cerebras": "gpt-oss-120b",
    "openrouter": "google/gemma-4-31b-it:free",
    "ollama": "llama3.1:8b",
}

# provider name -> (Settings attr for base_url, Settings attr for api key)
_PROVIDER_SETTINGS: dict[str, tuple[str, str | None]] = {
    "zen": ("opencode_base_url", "opencode_api_key"),
    "opencode": ("opencode_base_url", "opencode_api_key"),
    "groq": ("groq_base_url", "groq_api_key"),
    "cerebras": ("cerebras_base_url", "cerebras_api_key"),
    "openrouter": ("openrouter_base_url", "openrouter_api_key"),
    "ollama": ("ollama_base_url", None),
}

_RETRYABLE_KINDS = {"timeout", "rate_limit", "connection"}


# --------------------------------------------------------------------------- messages


@dataclass
class ChatMessage:
    """One conversation turn. Supports text, image, or text+image."""

    role: str  # "system" | "user" | "assistant"
    content: str = ""
    images: list[str] = field(default_factory=list)  # file paths or http/data URLs

    def to_api(self, vision: bool = False) -> dict[str, Any]:
        if not self.images or not vision:
            return {"role": self.role, "content": self.content}
        parts: list[dict[str, Any]] = []
        if self.content:
            parts.append({"type": "text", "text": self.content})
        for image in self.images:
            url = image if image.startswith(("http://", "https://", "data:")) else _image_to_data_url(image)
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
        if not parts:
            return {"role": self.role, "content": ""}
        return {"role": self.role, "content": parts}


def _image_to_data_url(path: str) -> str | None:
    try:
        from PIL import Image

        with Image.open(path) as img:
            img.load()
        suffix = Path(path).suffix.lower().lstrip(".") or "png"
        mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
        import io

        buffer = io.BytesIO()
        Image.open(path).convert("RGBA").save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/{mime};base64,{encoded}"
    except Exception:
        logger.warning("chat_image_unreadable path=%s", Path(path).name)
        return None


def build_history_from_memory(memory: Any, limit: int) -> list[ChatMessage]:
    """Recent chat turns from the existing memory DB (source='chat'), oldest first."""
    try:
        rows = memory.recent_chat(limit=limit)
    except Exception:
        return []
    messages: list[ChatMessage] = []
    for row in rows:
        prompt = str(row.get("command") or "").strip()
        response = str(row.get("response_text") or "").strip()
        if prompt:
            messages.append(ChatMessage(role="user", content=prompt))
        if response:
            messages.append(ChatMessage(role="assistant", content=response))
    return messages[-limit * 2 :]


# --------------------------------------------------------------------------- errors


class ChatStreamError(RuntimeError):
    """Provider-level failure with a machine-readable kind."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


def _classify_provider_exception(exc: Exception) -> str:
    text = str(exc).lower()
    try:
        import openai as _openai
    except Exception:
        pass
    else:
        if isinstance(exc, getattr(_openai, "APITimeoutError", ())):
            return "timeout"
        if isinstance(exc, getattr(_openai, "RateLimitError", ())):
            return "rate_limit"
        if isinstance(exc, getattr(_openai, "AuthenticationError", ())):
            return "auth"
        if isinstance(exc, getattr(_openai, "APIConnectionError", ())):
            return "connection"
        status = getattr(getattr(_openai, "APIStatusError", ()), "__instancecheck__", None)
        if status and isinstance(exc, _openai.APIStatusError):
            code = getattr(exc, "status_code", 0) or 0
            return "rate_limit" if code == 429 else "http"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "429" in text or "rate limit" in text:
        return "rate_limit"
    if "401" in text or "unauthorized" in text or "invalid api key" in text:
        return "auth"
    if "connection" in text or "connect" in text:
        return "connection"
    return "http"


# --------------------------------------------------------------------------- providers


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    model: str


class BaseChatProvider(Protocol):
    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None,
        max_tokens: int | None,
        timeout_s: float,
        cancel_event: threading.Event | None,
    ) -> Iterator[dict[str, Any]]: ...


class OpenAICompatChatProvider:
    """Streams from any OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, spec: ProviderSpec, base_url: str, api_key: str | None) -> None:
        self.spec = spec
        from openai import OpenAI

        self._client = OpenAI(
            api_key=api_key or "EMPTY",
            base_url=base_url,
            timeout=60.0,
            max_retries=0,  # retries are owned by ChatService, never the SDK
        )

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_s: float = 30.0,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "model": self.spec.model,
            "messages": messages,
            "stream": True,
            "timeout": timeout_s,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise ChatStreamError(_classify_provider_exception(exc), str(exc)) from exc

        try:
            for chunk in response:
                if cancel_event is not None and cancel_event.is_set():
                    raise ChatStreamError("cancelled")
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                text = getattr(delta, "content", None) if delta is not None else None
                if text:
                    yield {"delta": text}
            yield {"done": True}
        except ChatStreamError:
            raise
        except Exception as exc:
            raise ChatStreamError(_classify_provider_exception(exc), str(exc)) from exc
        finally:
            try:
                response.close()  # type: ignore[attr-defined]
            except Exception:
                pass


# --------------------------------------------------------------------------- service


def _parse_entry(entry: str) -> ProviderSpec | None:
    text = (entry or "").strip()
    if not text:
        return None
    raw = re.split(r"[=:]", text, maxsplit=1)
    name = raw[0].strip().lower()
    model = raw[1].strip() if len(raw) > 1 else ""
    if not name:
        return None
    return ProviderSpec(name=name, model=model or DEFAULT_CHAT_MODELS.get(name, ""))


def _resolve_spec(settings: Settings, name: str) -> tuple[str, str]:
    key = name.lower()
    if key not in _PROVIDER_SETTINGS:
        raise ValueError(f"Unknown chat provider '{name}'. Known: {sorted(set(_PROVIDER_SETTINGS))}")
    base_url_attr, api_key_attr = _PROVIDER_SETTINGS[key]
    base_url = str(getattr(settings, base_url_attr))
    api_key = str(getattr(settings, api_key_attr) or "") if api_key_attr else ""
    return base_url, api_key


def _optional_float(raw: str | None) -> float | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        logger.warning("chat_config_invalid value=%r ignored", raw)
        return None


def _optional_int(raw: str | None) -> int | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        return int(float(str(raw).strip()))
    except ValueError:
        logger.warning("chat_config_invalid value=%r ignored", raw)
        return None


class ChatService:
    """Streaming conversational service with a bounded provider fallback chain."""

    def __init__(self, settings: Settings, provider_factory: Callable[[ProviderSpec, Settings], BaseChatProvider] | None = None) -> None:
        self.settings = settings
        self._factory = provider_factory or self._default_provider
        self.chain: list[ProviderSpec] = self._build_chain(settings)

    # -- chain construction -------------------------------------------------

    @staticmethod
    def _build_chain(settings: Settings) -> list[ProviderSpec]:
        primary_name = (settings.jarvis_chat_provider or "opencode").strip().lower()
        primary_model = (settings.jarvis_chat_model or "").strip() or DEFAULT_CHAT_MODELS.get(primary_name, "")
        specs: list[ProviderSpec] = []
        primary = _parse_entry(f"{primary_name}:{primary_model}") if primary_model else ProviderSpec(primary_name, "")
        if primary is not None:
            specs.append(primary)

        numbered = [settings.jarvis_chat_fallback_1, settings.jarvis_chat_fallback_2, settings.jarvis_chat_fallback_3]
        entries = [e for e in numbered if e and e.strip()] + list(settings.jarvis_chat_fallback_providers or [])
        seen = {specs[0].name}
        for entry in entries:
            spec = _parse_entry(entry)
            if spec is None or spec.name in seen:
                continue
            seen.add(spec.name)
            specs.append(spec)
        return specs

    @staticmethod
    def _default_provider(spec: ProviderSpec, settings: Settings) -> BaseChatProvider:
        base_url, api_key = _resolve_spec(settings, spec.name)
        return OpenAICompatChatProvider(spec, base_url, api_key)

    # -- public streaming API -------------------------------------------------

    def stream_chat(
        self,
        user_text: str,
        history: list[ChatMessage],
        cancel_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield events: {delta}, {stream_reset}, {done(text, meta)} or {error(message)}.

        ``stream_reset`` tells consumers to discard partial text from an attempt
        that failed mid-stream, so a fallback/retry attempt renders on its own.
        It is only emitted when a later attempt actually produces deltas.
        """
        started = time.perf_counter()
        window = max(2, int(self.settings.jarvis_chat_history_window))
        trimmed = history[-window * 2 :]
        vision = bool(self.settings.jarvis_chat_vision_enabled)

        api_messages: list[dict[str, Any]] = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
        api_messages.extend(m.to_api(vision=vision) for m in trimmed)
        api_messages.append(ChatMessage(role="user", content=user_text).to_api(vision=vision))

        timeout_s = float(max(5, int(self.settings.jarvis_chat_timeout_seconds)))
        max_retries = max(0, int(self.settings.jarvis_chat_max_retries))
        deadline = started + float(max(1.0, int(self.settings.jarvis_chat_deadline_seconds)))
        temperature = _optional_float(self.settings.jarvis_chat_temperature)
        max_tokens = _optional_int(self.settings.jarvis_chat_max_tokens)

        first_token_ms: float | None = None
        aggregated: list[str] = []
        attempted: list[str] = []
        pending_reset = False  # a failed attempt left partial tokens on screen

        def elapsed_ms() -> int:
            return int((time.perf_counter() - started) * 1000)

        for index, spec in enumerate(self.chain):
            if cancel_event is not None and cancel_event.is_set():
                break
            remaining = deadline - time.perf_counter()
            if remaining <= 0.05:
                logger.warning("chat_deadline_reached elapsed_ms=%d", elapsed_ms())
                break

            provider = self._factory(spec, self.settings)
            attempted.append(f"{spec.name}:{spec.model}")
            logger.info(
                "chat_request_started provider=%s model=%s msgs=%d fallback_slot=%d",
                spec.name,
                spec.model,
                len(api_messages),
                index,
            )
            attempts = 1 + max_retries
            for attempt in range(attempts):
                if cancel_event is not None and cancel_event.is_set():
                    break
                if time.perf_counter() >= deadline:
                    break
                aggregated.clear()  # every retry/provider attempt owns isolated response state
                chunks = provider.stream(
                    api_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout_s=min(timeout_s, max(5.0, deadline - time.perf_counter())),
                    cancel_event=cancel_event,
                )
                try:
                    for event in chunks:
                        if "delta" in event:
                            if pending_reset:
                                pending_reset = False
                                yield {"type": "stream_reset"}
                            if first_token_ms is None:
                                first_token_ms = elapsed_ms()
                            aggregated.append(event["delta"])
                            yield {"type": "delta", "text": event["delta"], "provider": spec.name, "model": spec.model}
                        elif event.get("done"):
                            break
                except ChatStreamError as exc:
                    if exc.kind == "cancelled":
                        aggregated_text = "".join(aggregated)
                        yield {
                            "type": "done",
                            "text": aggregated_text,
                            "stopped": True,
                            "meta": self._meta(spec, first_token_ms, elapsed_ms(), index > 0, aggregated_text, api_messages),
                        }
                        return
                    logger.warning(
                        "chat_provider_failed provider=%s model=%s kind=%s detail=%.200s",
                        spec.name,
                        spec.model,
                        exc.kind,
                        exc.detail,
                    )
                    pending_reset = pending_reset or bool(aggregated)
                    aggregated.clear()
                    if exc.kind in _RETRYABLE_KINDS and attempt < attempts - 1:
                        self._interruptible_backoff(min(0.8 * (2**attempt), 2.0), deadline, cancel_event)
                        continue
                    break  # next provider
                else:
                    text = "".join(aggregated).strip()
                    if not text:
                        logger.warning(
                            "chat_provider_failed provider=%s model=%s kind=empty detail=empty response",
                            spec.name,
                            spec.model,
                        )
                        break  # treat as provider failure -> next provider
                    total = elapsed_ms()
                    meta = self._meta(spec, first_token_ms, total, index > 0, text, api_messages)
                    logger.info(
                        "chat provider=%s model=%s first_token_ms=%d total_ms=%d fallback=%s in_tok=%d out_tok=%d",
                        spec.name,
                        spec.model,
                        meta["first_token_ms"],
                        meta["total_ms"],
                        str(meta["fallback_used"]).lower(),
                        meta["input_tokens"],
                        meta["output_tokens"],
                    )
                    yield {"type": "done", "text": text, "stopped": False, "meta": meta}
                    return

        message = (
            "All chat providers failed"
            if attempted
            else "No chat provider available - configure OPENCODE_API_KEY or another chat provider."
        )
        logger.error("chat_all_providers_failed providers=%s elapsed_ms=%d", ",".join(attempted) or "-", elapsed_ms())
        yield {
            "type": "error",
            "message": f"{message}. Check .env keys / network, then press ASK again.",
            "providers_tried": attempted,
        }

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _meta(spec: ProviderSpec, first_token_ms: float | None, total_ms: int, used_fallback: bool, text: str, api_messages: list[dict[str, Any]]) -> dict[str, Any]:
        input_tokens = sum(len(str(m.get("content") or "")) for m in api_messages) // 4
        return {
            "provider": spec.name,
            "model": spec.model,
            "first_token_ms": int(first_token_ms) if first_token_ms is not None else -1,
            "total_ms": int(total_ms),
            "fallback_used": used_fallback,
            "input_tokens": input_tokens,
            "output_tokens": max(1, len(text) // 4),
            "tokens_estimated": True,
        }

    @staticmethod
    def _interruptible_backoff(delay: float, deadline: float, cancel_event: threading.Event | None) -> None:
        end = min(time.perf_counter() + delay, deadline)
        while time.perf_counter() < end:
            if cancel_event is not None and cancel_event.is_set():
                return
            time.sleep(min(0.1, max(0.0, end - time.perf_counter())))


__all__ = [
    "CHAT_SYSTEM_PROMPT",
    "BaseChatProvider",
    "ChatMessage",
    "ChatService",
    "ChatStreamError",
    "OpenAICompatChatProvider",
    "ProviderSpec",
    "build_history_from_memory",
]
