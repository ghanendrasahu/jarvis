"""LLM-based semantic understanding for imperfect voice transcripts.

Sits between Whisper (hearing) and the router (deciding). Given a suspicious
transcript plus a dynamically generated capability digest, it returns ONE
canonical JARVIS command string - or None when meaning is not confidently
determined. It never executes anything and never bypasses router validation;
the canonical text flows through the exact same orchestrator path as typed
commands.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from app.config import Settings
from app.core.logging import get_logger
from app.core.nlu import SURFACES, DomainLexicon, best_entity_match, build_lexicon

logger = get_logger("voice-semantic")

_DEFAULT_SEMANTIC_MODELS = {
    "groq": "llama-3.1-8b-instant",
    "cerebras": "llama-3.1-8b-instant",
}

_YOUTUBE_ACTIONS = {
    "pause": "pause",
    "resume": "resume",
    "play": "play",
    "next": "next",
    "next_video": "next",
    "previous": "previous",
    "previous_video": "previous",
    "mute": "mute",
    "unmute": "unmute",
    "fullscreen": "fullscreen",
    "captions": "captions",
    "subtitles": "captions",
    "speed_up": "speed up",
    "faster": "speed up",
    "speed_down": "slow down",
    "slower": "slow down",
    "start_over": "start",
    "beginning": "start",
    "end": "end",
}


def _extract_json(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            payload = json.loads(text[start : end + 1])
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}


def build_capability_digest(
    lexicon: DomainLexicon,
    tool_names: list[str] | tuple[str, ...] = (),
    frequent_apps: dict[str, int] | None = None,
) -> str:
    """Compact description of what JARVIS can do, generated from live registries."""
    lines = [
        f"Desktop applications: {', '.join(sorted(lexicon.apps))}",
        f"Websites/web apps: {', '.join(sorted(lexicon.websites))}",
        f"Controllable surfaces (media controls): {', '.join(SURFACES)}",
    ]
    if frequent_apps:
        top = sorted(frequent_apps.items(), key=lambda kv: kv[1], reverse=True)[:5]
        lines.append(f"User's frequently opened apps: {', '.join(name for name, _ in top)}")
    if tool_names:
        lines.append(f"Available tools (for reference only): {', '.join(sorted(tool_names))}")
    return "\n".join(lines)


_SYSTEM_PROMPT = (
    "You are the intent normalizer of a voice assistant named JARVIS. Speech-to-text often "
    "mishears words (accents, pronunciation), so transcripts may contain wrong words that are "
    "phonetically similar to something else.\n"
    "Your ONLY job: convert ONE spoken transcript into ONE canonical command from JARVIS's "
    "grammar, using the provided capabilities as ground truth. You decide MEANING, not tools - "
    "you can never execute anything and must never invent capabilities or targets.\n"
    "Rules:\n"
    "1. If the transcript mentions an app/website that phonetically resembles one in the "
    "capability list (e.g. 'hot tub' -> 'whatsapp' when opening an application), correct it.\n"
    "2. If the transcript is clearly a search/query request ('search for hot tub restaurants'), "
    "keep the query words VERBATIM - never replace words inside queries.\n"
    "3. Only use targets that exist in the capability list. Otherwise answer unknown.\n"
    "4. If you are not confident, answer unknown. Do not guess.\n"
    'Respond with strict JSON only: {"intent": "...", ...} or {"intent": "unknown"}.'
)


def _build_user_prompt(transcript: str, digest: str) -> str:
    return (
        f"{digest}\n"
        "\nCommand grammar:\n"
        "- open <app-or-site>\n"
        "- close <app>\n"
        "- youtube play <query> | youtube pause|resume|next|previous|mute|fullscreen\n"
        "- youtube skip <seconds> | youtube rewind <seconds>\n"
        "- search web <query>  (query verbatim)\n"
        "- send whatsapp to <contact>: <message>\n"
        "- system status | time | date and time\n"
        "\nIntent schema:\n"
        '{"intent": "open_app|close_app|youtube_play|youtube_control|search_web|send_whatsapp|system_info|unknown",\n'
        ' "target": "<app/site name>", "action": "<pause|resume|next|previous|skip_forward|skip_back|...>",\n'
        ' "amount": <seconds>, "contact": "...", "message": "...", "variant": "<time|date|status>",\n'
        ' "query": "<verbatim query>"}\n'
        "\nExamples:\n"
        'Transcript: "open hot tub" -> {"intent": "open_app", "target": "whatsapp"}\n'
        'Transcript: "hey jarvis could you launch wattsapp" -> {"intent": "open_app", "target": "whatsapp"}\n'
        'Transcript: "search web hot tub restaurants near me" -> '
        '{"intent": "search_web", "query": "hot tub restaurants near me"}\n'
        'Transcript: "pley some lofi beats on utube" -> {"intent": "youtube_play", "query": "lofi beats"}\n'
        'Transcript: "what time is it" -> {"intent": "system_info", "variant": "time"}\n'
        'Transcript: "tell me a joke" -> {"intent": "unknown"}\n'
        f"\nTranscript: \"{transcript}\"\nJSON:"
    )


def canonicalize_intent(
    payload: dict[str, Any],
    lexicon: DomainLexicon,
    target_score: int = 75,
    extra_apps: frozenset[str] | set[str] = frozenset(),
) -> str | None:
    """Validate a semantic-intent payload and map it onto JARVIS's command grammar.

    Returns None whenever the payload is invalid, ambiguous, or references
    unknown targets - callers then keep their existing fallback behavior.
    """
    intent = str(payload.get("intent") or "").strip().lower()
    if not intent or intent == "unknown":
        return None

    def _resolve(name: Any, choices: frozenset[str] | set[str]) -> str | None:
        return best_entity_match(str(name or ""), choices | set(extra_apps), target_score)

    if intent in {"open_app", "open_website", "open_application"}:
        resolved = _resolve(payload.get("target"), lexicon.entities)
        return f"open {resolved}" if resolved else None

    if intent in {"close_app", "close_application"}:
        resolved = _resolve(payload.get("target"), lexicon.apps | lexicon.surfaces)
        return f"close {resolved}" if resolved else None

    if intent == "youtube_play":
        query = str(payload.get("query") or payload.get("target") or "").strip()
        return f"youtube play {query}" if query else None

    if intent == "youtube_control":
        action = str(payload.get("action") or "").strip().lower()
        amount = payload.get("amount")
        try:
            amount_int = max(1, int(float(amount))) if amount is not None else None
        except (TypeError, ValueError):
            amount_int = None
        mapped = _YOUTUBE_ACTIONS.get(action)
        if mapped:
            return f"youtube {mapped}"
        if action in {"skip_forward", "forward", "skip"} and amount_int:
            return f"youtube skip {amount_int}"
        if action in {"skip_back", "back", "rewind"} and amount_int:
            return f"youtube rewind {amount_int}"
        return None

    if intent == "search_web":
        query = str(payload.get("query") or "").strip()
        return f"search web {query}" if query else None

    if intent == "send_whatsapp":
        contact = str(payload.get("contact") or payload.get("target") or "").strip()
        message = str(payload.get("message") or "").strip()
        if contact and message:
            return f"send whatsapp to {contact}: {message}"
        return None

    if intent == "system_info":
        variant = str(payload.get("variant") or "").strip().lower()
        if variant == "time":
            return "time"
        if variant == "date":
            return "date and time"
        if variant in {"status", "health"}:
            return "system status"
        return None

    return None


class SemanticInterpreter:
    """Free-tier LLM client that turns imperfect transcripts into canonical commands."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any = None
        self._provider: str = ""
        self._model: str = ""

    @property
    def provider(self) -> str:
        self._ensure_client()
        return self._provider

    def _provider_chain(self) -> list[str]:
        preferred = str(getattr(self.settings, "voice_semantic_provider", "") or "").strip().lower()
        if preferred:
            return [preferred]
        return ["groq", "cerebras", "openai", "openrouter"]

    def _resolve_provider(self) -> tuple[str, str] | None:
        """Return (provider, model) for the first usable free provider."""
        for provider in self._provider_chain():
            api_key = ""
            base_model = ""
            if provider == "groq":
                api_key = self.settings.groq_api_key or ""
                base_model = str(getattr(self.settings, "groq_model", "") or "")
            elif provider == "cerebras":
                api_key = getattr(self.settings, "cerebras_api_key", "") or ""
                base_model = str(getattr(self.settings, "cerebras_model", "") or "")
            elif provider == "openai":
                api_key = self.settings.openai_api_key or ""
                base_model = str(getattr(self.settings, "openai_model", "") or "")
            elif provider == "openrouter":
                api_key = getattr(self.settings, "openrouter_api_key", "") or ""
                base_model = str(getattr(self.settings, "openrouter_model", "") or "")
            elif provider == "ollama":
                return provider, self._model_for(provider, str(getattr(self.settings, "ollama_model", "") or ""))
            else:
                logger.debug("semantic provider %s not supported", provider)
                return None
            if api_key:
                return provider, self._model_for(provider, base_model)
        return None

    def _model_for(self, provider: str, fallback_model: str) -> str:
        configured = str(getattr(self.settings, "voice_semantic_model", "") or "").strip()
        if configured:
            return configured
        return _DEFAULT_SEMANTIC_MODELS.get(provider) or fallback_model

    def _ensure_client(self) -> Any:
        if self._provider:
            return self._client
        resolved = self._resolve_provider()
        if resolved is None:
            return None
        provider, model = resolved
        if provider == "ollama":
            self._client = None
        else:
            from openai import OpenAI

            base_urls = {
                "groq": self.settings.groq_base_url,
                "cerebras": getattr(self.settings, "cerebras_base_url", "https://api.cerebras.ai/v1"),
                "openai": None,
                "openrouter": getattr(self.settings, "openrouter_base_url", "https://openrouter.ai/api/v1"),
            }
            api_keys = {
                "groq": self.settings.groq_api_key,
                "cerebras": getattr(self.settings, "cerebras_api_key", ""),
                "openai": getattr(self.settings, "openai_api_key", ""),
                "openrouter": getattr(self.settings, "openrouter_api_key", ""),
            }
            self._client = OpenAI(api_key=api_keys[provider], base_url=base_urls[provider])
        self._provider = provider
        self._model = model
        logger.info("semantic interpreter ready: provider=%s model=%s", provider, model)
        return self._client

    def interpret(
        self,
        transcript: str,
        tool_names: list[str] | tuple[str, ...] = (),
        learned_facts: dict[str, Any] | None = None,
    ) -> str | None:
        """Return a canonical JARVIS command, or None when not confident.

        Pure understanding step: no execution, no tool calls, no state changes.
        """
        client = self._ensure_client()
        if client is None and self._provider != "ollama":
            return None

        lexicon = build_lexicon()
        frequent_apps = dict((learned_facts or {}).get("frequent_apps") or {})
        digest = build_capability_digest(lexicon, tool_names=tool_names, frequent_apps=frequent_apps)
        user_prompt = _build_user_prompt(transcript.strip(), digest)
        timeout = float(getattr(self.settings, "voice_semantic_timeout_seconds", 5))

        try:
            content = self._complete(user_prompt, timeout)
        except Exception as exc:
            logger.warning("semantic interpretation failed: %s", exc)
            return None
        if not content:
            return None

        payload = _extract_json(content)
        if not payload:
            return None
        canonical = canonicalize_intent(
            payload,
            lexicon,
            target_score=int(getattr(self.settings, "voice_fuzzy_target_score", 75)),
            extra_apps=frozenset(frequent_apps.keys()),
        )
        if canonical:
            logger.info(
                "semantic normalization: '%s' -> '%s' (%s/%s)",
                transcript,
                canonical,
                self._provider,
                payload.get("intent"),
            )
        else:
            logger.info("semantic interpretation not confident for: '%s'", transcript)
        return canonical

    def _complete(self, user_prompt: str, timeout: float) -> str:
        if self._provider == "ollama":
            response = requests.post(
                f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0},
                },
                timeout=timeout,
            )
            response.raise_for_status()
            message = response.json().get("message") or {}
            return str(message.get("content") or "")
        assert self._client is not None
        completion = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            timeout=timeout,
        )
        message = completion.choices[0].message
        content = (getattr(message, "content", None) or "").strip()
        if content:
            return content
        for attr in ("reasoning_content", "reasoning"):
            reasoning = getattr(message, attr, None)
            if isinstance(reasoning, str) and reasoning.strip():
                return reasoning.strip()
        return ""


__all__ = [
    "SemanticInterpreter",
    "build_capability_digest",
    "canonicalize_intent",
]
