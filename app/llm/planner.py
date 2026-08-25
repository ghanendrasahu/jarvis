from __future__ import annotations

import importlib.util
import json
import random
import time
from typing import Any, Protocol

import requests

from app.config import Settings
from app.core.logging import get_logger
from app.core.prompts import JARVIS_IDENTITY_PROMPT, build_planner_prompt
from app.models.schemas import Action, Plan, RiskLevel

logger = get_logger("planner")


class BasePlanner(Protocol):
    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
        tool_history: list[dict] | None = None,
        known_facts: dict | None = None,
        rag_context: str = "",
    ) -> Plan:
        ...


def _extract_json(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            # Reasoning models may wrap the answer in extra prose after the JSON.
            for candidate_start in (text.find("[", start), text.find("{", start)):
                if candidate_start == -1:
                    continue
                candidate_end = text.rfind("}" if text[candidate_start] == "{" else "]")
                if candidate_end > candidate_start:
                    try:
                        return json.loads(text[candidate_start : candidate_end + 1])
                    except json.JSONDecodeError:
                        continue
            return {}


def _convert_tool_call_to_plan(failed_generation: str) -> dict[str, Any]:
    """Convert a Groq tool_call response to our plan JSON format."""
    try:
        tc = json.loads(failed_generation)
        tool_name = tc.get("name", "")
        args = tc.get("arguments", {})
        if tool_name == "run_tool":
            actual_tool = args.get("tool", "")
            actual_args = args.get("args", {})
            return {
                "summary": f"Execute {actual_tool}",
                "actions": [{"tool": actual_tool, "args": actual_args}],
            }
        return {
            "summary": f"Execute {tool_name}",
            "actions": [{"tool": tool_name, "args": args}],
        }
    except (json.JSONDecodeError, AttributeError):
        return {}


def _to_plan(payload: dict[str, Any]) -> Plan:
    def normalize_risk(raw_value: Any) -> RiskLevel:
        value = str(raw_value or "medium").lower().strip()
        if value == "high":
            return "high"
        if value == "low":
            return "low"
        return "medium"

    actions_data = payload.get("actions") or []
    actions: list[Action] = []
    for item in actions_data:
        if not isinstance(item, dict) or "tool" not in item:
            continue
        actions.append(
            Action(
                tool=str(item.get("tool", "")).strip(),
                args=item.get("args") or {},
                risk=normalize_risk(item.get("risk")),
                reason=str(item.get("reason", "")).strip(),
            )
        )
    return Plan(
        summary=str(payload.get("summary", "")).strip(),
        actions=actions,
        final_response=str(payload.get("final_response", "")).strip(),
    )


class NullPlanner:
    def __init__(self, reason: str = "No LLM provider configured.") -> None:
        self.reason = reason

    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
        tool_history: list[dict] | None = None,
        known_facts: dict | None = None,
        rag_context: str = "",
    ) -> Plan:
        return Plan(
            summary="No planner available",
            actions=[],
            final_response=(
                f"Planner unavailable: {self.reason} "
                "Use a direct command (open, close, run, screenshot, read file) or configure the LLM provider."
            ),
        )


class OpenAIPlanner:
    def __init__(self, settings: Settings, model: str | None = None) -> None:
        self.settings = settings
        self.model = model or settings.openai_model
        from openai import OpenAI

        # Explicit per-attempt timeout + capped retries: SDK defaults are 600s
        # with 2 retries, which can hang a command worker for ~30 minutes.
        self.client = OpenAI(api_key=settings.openai_api_key, timeout=60.0, max_retries=1)

    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
        tool_history: list[dict] | None = None,
        known_facts: dict | None = None,
        rag_context: str = "",
    ) -> Plan:
        user_prompt = build_planner_prompt(
            command=command,
            tool_catalog=tool_catalog,
            history=history,
            agent_context=agent_context,
            tool_history=tool_history,
            known_facts=known_facts,
            rag_context=rag_context,
        )
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": JARVIS_IDENTITY_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        content = completion.choices[0].message.content or "{}"
        payload = _extract_json(content)
        return _to_plan(payload)


def _message_content(message: Any) -> str:
    """Return the final answer from a chat response, handling reasoning-model layouts."""
    content = (getattr(message, "content", None) or "").strip()
    if content:
        return content
    for attr in ("reasoning_content", "reasoning"):
        reasoning = getattr(message, attr, None)
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning.strip()
    return "{}"


class GroqPlanner:
    def __init__(self, settings: Settings, model: str | None = None) -> None:
        self.settings = settings
        self.model = model or settings.groq_model
        from openai import OpenAI

        self.client = OpenAI(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
            timeout=60.0,
            max_retries=1,
        )

    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
        tool_history: list[dict] | None = None,
        known_facts: dict | None = None,
        rag_context: str = "",
    ) -> Plan:
        user_prompt = build_planner_prompt(
            command=command,
            tool_catalog=tool_catalog,
            history=history,
            agent_context=agent_context,
            tool_history=tool_history,
            known_facts=known_facts,
            rag_context=rag_context,
        )
        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": JARVIS_IDENTITY_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            message = completion.choices[0].message
            content = _message_content(message)
            payload = _extract_json(content)
            return _to_plan(payload)
        except Exception as exc:
            # Groq sometimes returns 400 with tool_use_failed when the model
            # generates a tool call despite no tools being defined.
            # Extract the failed_generation from the response body and convert it.
            body = getattr(exc, "body", None)
            if isinstance(body, dict):
                error_info = body.get("error", {})
                if error_info.get("code") == "tool_use_failed":
                    failed_gen = error_info.get("failed_generation", "")
                    if failed_gen:
                        payload = _convert_tool_call_to_plan(failed_gen)
                        if payload:
                            return _to_plan(payload)
            raise


class CerebrasPlanner:
    """Cerebras inference — free tier with 60K TPM (7.5x more than Groq)."""

    def __init__(self, settings: Settings, model: str | None = None) -> None:
        self.settings = settings
        self.model = model or settings.cerebras_model
        from openai import OpenAI

        self.client = OpenAI(
            api_key=settings.cerebras_api_key,
            base_url=settings.cerebras_base_url,
            timeout=60.0,
            max_retries=1,
        )

    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
        tool_history: list[dict] | None = None,
        known_facts: dict | None = None,
        rag_context: str = "",
    ) -> Plan:
        user_prompt = build_planner_prompt(
            command=command,
            tool_catalog=tool_catalog,
            history=history,
            agent_context=agent_context,
            tool_history=tool_history,
            known_facts=known_facts,
            rag_context=rag_context,
        )
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": JARVIS_IDENTITY_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        message = completion.choices[0].message
        content = _message_content(message)
        payload = _extract_json(content)
        return _to_plan(payload)


class OpenRouterPlanner:
    """OpenRouter — 20+ free models, single API key, auto-failover."""

    def __init__(self, settings: Settings, model: str | None = None) -> None:
        self.settings = settings
        self.model = model or settings.openrouter_model
        from openai import OpenAI

        self.client = OpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            timeout=60.0,
            max_retries=1,
        )

    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
        tool_history: list[dict] | None = None,
        known_facts: dict | None = None,
        rag_context: str = "",
    ) -> Plan:
        user_prompt = build_planner_prompt(
            command=command,
            tool_catalog=tool_catalog,
            history=history,
            agent_context=agent_context,
            tool_history=tool_history,
            known_facts=known_facts,
            rag_context=rag_context,
        )
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": JARVIS_IDENTITY_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        message = completion.choices[0].message
        content = _message_content(message)
        payload = _extract_json(content)
        return _to_plan(payload)


class OllamaPlanner:
    def __init__(self, settings: Settings, model: str | None = None) -> None:
        self.settings = settings
        self.model = model or settings.ollama_model

    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
        tool_history: list[dict] | None = None,
        known_facts: dict | None = None,
        rag_context: str = "",
    ) -> Plan:
        user_prompt = build_planner_prompt(
            command=command,
            tool_catalog=tool_catalog,
            history=history,
            agent_context=agent_context,
            tool_history=tool_history,
            known_facts=known_facts,
            rag_context=rag_context,
        )
        response = requests.post(
            f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": JARVIS_IDENTITY_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "format": "json",
                "stream": False,
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        content = (data.get("message") or {}).get("content", "{}")
        payload = _extract_json(content)
        return _to_plan(payload)


class LiteLLMPlanner:
    """OpenAI-compatible planning through LiteLLM (multi-provider routing/fallback).

    Requires the optional extra: ``pip install -e .[litellm]``.
    Model names are provider-prefixed, e.g. ``groq/llama-3.3-70b-versatile``.
    """

    def __init__(self, settings: Settings, model: str | None = None) -> None:
        self.settings = settings
        self.model = model or settings.litellm_model
        try:
            import litellm  # type: ignore[import-not-found]  # optional extra: pip install -e .[litellm]
        except ImportError as exc:
            raise ImportError("litellm extra not installed; run: pip install -e .[litellm]") from exc
        self._litellm = litellm

    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
        tool_history: list[dict] | None = None,
        known_facts: dict | None = None,
        rag_context: str = "",
    ) -> Plan:
        user_prompt = build_planner_prompt(
            command=command,
            tool_catalog=tool_catalog,
            history=history,
            agent_context=agent_context,
            tool_history=tool_history,
            known_facts=known_facts,
            rag_context=rag_context,
        )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": JARVIS_IDENTITY_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "drop_params": True,
        }
        if self.settings.litellm_api_base:
            kwargs["api_base"] = self.settings.litellm_api_base
        if self.settings.litellm_api_key:
            kwargs["api_key"] = self.settings.litellm_api_key
        response = self._litellm.completion(**kwargs)
        message = response.choices[0].message
        content = _message_content(message)
        payload = _extract_json(content)
        return _to_plan(payload)


# ---------------------------------------------------------------------------
# Error classification helpers
# ---------------------------------------------------------------------------

# Errors that mean "try a different provider" (not worth retrying same provider).
_PROVIDER_SWITCH_ERRORS = {"413", "400"}
# Errors that mean "retry with backoff" (transient).
_RETRYABLE_ERRORS = {"429", "500", "502", "503", "timeout", "connection"}


def _classify_error(exc: Exception) -> str:
    """Return a short error code for classification."""
    exc_str = str(exc).lower()
    if "413" in exc_str or "payload too large" in exc_str or "tpm" in exc_str:
        return "413"
    if "429" in exc_str or "rate limit" in exc_str or "too many requests" in exc_str:
        return "429"
    if "400" in exc_str or "bad request" in exc_str:
        return "400"
    if "500" in exc_str or "502" in exc_str or "503" in exc_str or "internal server" in exc_str:
        return "500"
    if "timeout" in exc_str:
        return "timeout"
    if "connection" in exc_str or "connect" in exc_str:
        return "connection"
    return "unknown"


def _user_friendly_error(code: str, provider: str) -> str:
    """Return a human-readable error message."""
    if code == "413":
        return f"Prompt too large for {provider}. Try a shorter command."
    if code == "429":
        return f"Rate limited by {provider}. Try again in a few seconds."
    if code == "400":
        return f"Invalid request to {provider}. Try a different command."
    if code in {"500", "timeout", "connection"}:
        return f"{provider} is temporarily unavailable. Trying next provider..."
    return f"{provider} returned an error. Trying next provider..."


# ---------------------------------------------------------------------------
# FallbackPlanner — tries primary provider, then fallbacks in order
# ---------------------------------------------------------------------------

# Summary marker on the total-failure Plan so callers can distinguish a real
# plan from "every provider failed" without string-guessing downstream.
ALL_PROVIDERS_FAILED_SUMMARY = "All LLM providers failed"


class FallbackPlanner:
    """Wraps a primary planner with automatic fallback to other providers.

    When the primary planner fails with a retryable or provider-switch error,
    the FallbackPlanner tries the next provider in the fallback chain. Each
    provider gets ``max_retries`` attempts with exponential backoff for transient
    errors (429, 500, timeout). Permanent errors (413, 400) immediately skip to
    the next provider.
    """

    def __init__(
        self,
        primary: BasePlanner,
        fallbacks: list[BasePlanner],
        max_retries: int = 2,
        retry_base_delay: float = 1.0,
        deadline_seconds: float = 180.0,
    ) -> None:
        self._primary = primary
        self._fallbacks = fallbacks
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        # Hard wall-clock budget across all providers/retries so a chain of
        # slow providers can never block a caller indefinitely.
        self._deadline_seconds = max(1.0, float(deadline_seconds))
        self._last_provider: str = ""
        self._last_error: str = ""
        # Per-provider cooldown: maps provider name -> timestamp when cooldown expires.
        # Prevents repeatedly hammering a known-failing provider across calls.
        self._provider_cooldown: dict[str, float] = {}
        self._cooldown_max_seconds: float = 300.0

    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
        tool_history: list[dict] | None = None,
        known_facts: dict | None = None,
        rag_context: str = "",
    ) -> Plan:
        started = time.monotonic()
        providers = [self._primary] + self._fallbacks
        last_exc: Exception | None = None

        for provider_idx, planner in enumerate(providers):
            if time.monotonic() - started >= self._deadline_seconds:
                logger.warning("planner deadline (%.0fs) reached before %s", self._deadline_seconds, type(planner).__name__)
                break
            provider_name = type(planner).__name__

            # Skip providers that are in cooldown from a recent failure.
            cooldown_until = self._provider_cooldown.get(provider_name, 0.0)
            if cooldown_until > time.monotonic():
                remaining_cd = cooldown_until - time.monotonic()
                logger.info("skipping %s (cooldown %.0fs remaining)", provider_name, remaining_cd)
                continue

            # Uniform retry policy: every provider (including the primary) gets
            # max_retries + 1 attempts. The single wall-clock deadline bounds
            # the whole chain regardless of how many attempts are allowed.
            attempts = self._max_retries + 1

            for attempt in range(attempts):
                try:
                    result = planner.plan(
                        command=command,
                        tool_catalog=tool_catalog,
                        history=history,
                        agent_context=agent_context,
                        tool_history=tool_history,
                        known_facts=known_facts,
                        rag_context=rag_context,
                    )
                    if result.actions or result.final_response:
                        self._last_provider = provider_name
                        self._last_error = ""
                        if provider_idx > 0:
                            logger.info("fallback succeeded: %s (attempt %d)", provider_name, attempt + 1)
                        return result
                    # Empty result — try next provider
                    logger.warning("empty plan from %s, trying next provider", provider_name)
                    break
                except Exception as exc:
                    last_exc = exc
                    code = _classify_error(exc)
                    logger.warning(
                        "planner %s failed (attempt %d/%d, code=%s): %s",
                        provider_name, attempt + 1, attempts, code, exc,
                    )

                    if code in _PROVIDER_SWITCH_ERRORS:
                        # Don't retry — skip to next provider immediately.
                        # Set cooldown so repeated calls don't hammer this provider.
                        cooldown = min(self._cooldown_max_seconds, self._retry_base_delay * (2 ** attempts))
                        self._provider_cooldown[provider_name] = time.monotonic() + cooldown
                        logger.info("provider %s entering cooldown for %.0fs (switch error %s)", provider_name, cooldown, code)
                        break

                    if code in _RETRYABLE_ERRORS and attempt < attempts - 1:
                        delay = self._retry_base_delay * (2 ** attempt)
                        # Jitter: ±30% randomization to avoid thundering-herd
                        delay *= 1.0 + random.uniform(-0.3, 0.3)
                        remaining = self._deadline_seconds - (time.monotonic() - started)
                        if delay >= remaining:
                            break
                        logger.info("retrying %s in %.1fs...", provider_name, delay)
                        time.sleep(delay)
                        continue

                    # Non-retryable or exhausted retries — try next provider.
                    # Set cooldown so repeated calls don't immediately retry this provider.
                    cooldown = min(self._cooldown_max_seconds, self._retry_base_delay * (2 ** (attempts)))
                    self._provider_cooldown[provider_name] = time.monotonic() + cooldown
                    logger.info("provider %s entering cooldown for %.0fs", provider_name, cooldown)
                    break

        # All providers failed
        error_msg = _user_friendly_error(
            _classify_error(last_exc) if last_exc else "unknown",
            self._last_provider or "all providers",
        )
        self._last_error = error_msg
        logger.error("all planners failed: %s", last_exc)
        return Plan(
            summary=ALL_PROVIDERS_FAILED_SUMMARY,
            actions=[],
            final_response=error_msg,
        )

    @property
    def last_provider(self) -> str:
        return self._last_provider

    @property
    def last_error(self) -> str:
        return self._last_error


def _agent_provider(settings: Settings) -> str:
    agent_provider = (settings.agent_llm_provider or "").strip().lower()
    return agent_provider or settings.llm_provider.lower().strip()


def _create_single_planner(settings: Settings, provider: str, for_agent: bool = False) -> BasePlanner:
    """Create a single planner for the given provider name. Returns NullPlanner on failure."""
    if provider == "openai":
        if not settings.openai_api_key:
            return NullPlanner("OPENAI_API_KEY is not set.")
        model = settings.agent_openai_model if for_agent else settings.openai_model
        try:
            return OpenAIPlanner(settings, model=model)
        except ImportError:
            return NullPlanner("openai package is not installed.")
        except Exception as exc:
            return NullPlanner(f"OpenAI init failed: {exc}")

    if provider == "ollama":
        model = settings.agent_ollama_model if for_agent else settings.ollama_model
        return OllamaPlanner(settings, model=model)

    if provider == "groq":
        if not settings.groq_api_key:
            return NullPlanner("GROQ_API_KEY is not set.")
        model = settings.agent_groq_model if for_agent else settings.groq_model
        try:
            return GroqPlanner(settings, model=model)
        except ImportError:
            return NullPlanner("openai package is not installed.")
        except Exception as exc:
            return NullPlanner(f"Groq init failed: {exc}")

    if provider == "cerebras":
        if not settings.cerebras_api_key:
            return NullPlanner("CEREBRAS_API_KEY is not set.")
        model = settings.agent_cerebras_model if for_agent else settings.cerebras_model
        try:
            return CerebrasPlanner(settings, model=model)
        except ImportError:
            return NullPlanner("openai package is not installed.")
        except Exception as exc:
            return NullPlanner(f"Cerebras init failed: {exc}")

    if provider == "openrouter":
        if not settings.openrouter_api_key:
            return NullPlanner("OPENROUTER_API_KEY is not set.")
        model = settings.agent_openrouter_model if for_agent else settings.openrouter_model
        try:
            return OpenRouterPlanner(settings, model=model)
        except ImportError:
            return NullPlanner("openai package is not installed.")
        except Exception as exc:
            return NullPlanner(f"OpenRouter init failed: {exc}")

    if provider == "litellm":
        if not importlib.util.find_spec("litellm"):
            return NullPlanner(
                "litellm extra not installed; run: pip install -e .[litellm]"
            )
        model = settings.litellm_model if not for_agent else (settings.agent_litellm_model or settings.litellm_model)
        try:
            return LiteLLMPlanner(settings, model=model)
        except ImportError as exc:
            return NullPlanner(str(exc))
        except Exception as exc:
            return NullPlanner(f"LiteLLM init failed: {exc}")

    return NullPlanner(f"Unsupported provider: {provider}")


def create_planner(settings: Settings, for_agent: bool = False) -> BasePlanner:
    provider = _agent_provider(settings) if for_agent else settings.llm_provider.lower().strip()

    primary = _create_single_planner(settings, provider, for_agent=for_agent)

    # Build fallback chain for both main planner and agent planner
    fallback_names = settings.llm_fallback_providers
    if not fallback_names:
        return primary

    fallbacks: list[BasePlanner] = []
    for fb_name in fallback_names:
        fb_name = fb_name.strip().lower()
        if fb_name == provider:
            continue  # skip duplicate
        fb = _create_single_planner(settings, fb_name, for_agent=False)
        if isinstance(fb, NullPlanner):
            logger.debug("fallback %s skipped: %s", fb_name, fb.reason)
            continue
        fallbacks.append(fb)

    if not fallbacks:
        return primary

    logger.info(
        "planner chain: primary=%s, fallbacks=%s",
        type(primary).__name__,
        [type(f).__name__ for f in fallbacks],
    )
    return FallbackPlanner(
        primary=primary,
        fallbacks=fallbacks,
        max_retries=settings.llm_max_retries,
        retry_base_delay=settings.llm_retry_base_delay,
        deadline_seconds=float(getattr(settings, "llm_fallback_deadline_seconds", 180)),
    )
