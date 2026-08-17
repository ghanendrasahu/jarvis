from __future__ import annotations

import json
from typing import Any, Protocol

import requests

from app.config import Settings
from app.core.prompts import JARVIS_IDENTITY_PROMPT, build_planner_prompt
from app.models.schemas import Action, Plan, RiskLevel


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

        self.client = OpenAI(api_key=settings.openai_api_key)

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


def _agent_provider(settings: Settings) -> str:
    agent_provider = (settings.agent_llm_provider or "").strip().lower()
    return agent_provider or settings.llm_provider.lower().strip()


def create_planner(settings: Settings, for_agent: bool = False) -> BasePlanner:
    provider = _agent_provider(settings) if for_agent else settings.llm_provider.lower().strip()

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

    if provider == "litellm":
        model = settings.litellm_model if not for_agent else (settings.agent_litellm_model or settings.litellm_model)
        try:
            return LiteLLMPlanner(settings, model=model)
        except ImportError as exc:
            return NullPlanner(str(exc))
        except Exception as exc:
            return NullPlanner(f"LiteLLM init failed: {exc}")

    return NullPlanner(f"Unsupported llm_provider: {provider}")
