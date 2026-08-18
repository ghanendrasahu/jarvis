from __future__ import annotations

import json
from typing import Any, Protocol

import requests

from app.config import Settings
from app.core.prompts import JARVIS_IDENTITY_PROMPT, build_planner_prompt
from app.models.schemas import Action, Plan


class BasePlanner(Protocol):
    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
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
    def normalize_risk(raw_value: Any) -> str:
        value = str(raw_value or "medium").lower().strip()
        return value if value in {"low", "medium", "high"} else "medium"

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
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        from openai import OpenAI

        self.client = OpenAI(api_key=settings.openai_api_key)

    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
    ) -> Plan:
        user_prompt = build_planner_prompt(
            command=command, tool_catalog=tool_catalog, history=history, agent_context=agent_context
        )
        completion = self.client.chat.completions.create(
            model=self.settings.openai_model,
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
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
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
    ) -> Plan:
        user_prompt = build_planner_prompt(
            command=command, tool_catalog=tool_catalog, history=history, agent_context=agent_context
        )
        completion = self.client.chat.completions.create(
            model=self.settings.groq_model,
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
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
    ) -> Plan:
        user_prompt = build_planner_prompt(
            command=command, tool_catalog=tool_catalog, history=history, agent_context=agent_context
        )
        response = requests.post(
            f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
            json={
                "model": self.settings.ollama_model,
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


def create_planner(settings: Settings) -> BasePlanner:
    provider = settings.llm_provider.lower().strip()

    if provider == "openai":
        if not settings.openai_api_key:
            return NullPlanner("OPENAI_API_KEY is not set.")
        try:
            return OpenAIPlanner(settings)
        except ImportError:
            return NullPlanner("openai package is not installed.")
        except Exception as exc:
            return NullPlanner(f"OpenAI init failed: {exc}")

    if provider == "ollama":
        return OllamaPlanner(settings)

    if provider == "groq":
        if not settings.groq_api_key:
            return NullPlanner("GROQ_API_KEY is not set.")
        try:
            return GroqPlanner(settings)
        except ImportError:
            return NullPlanner("openai package is not installed.")
        except Exception as exc:
            return NullPlanner(f"Groq init failed: {exc}")

    return NullPlanner(f"Unsupported llm_provider: {settings.llm_provider}")
