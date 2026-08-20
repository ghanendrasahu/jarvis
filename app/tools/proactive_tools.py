from __future__ import annotations

from typing import Any

from app.config import Settings
from app.core.idle import IdleDetector
from app.core.memory import MemoryStore
from app.core.suggest import SuggestionEngine
from app.tools.registry import ToolRegistry


def register_proactive_tools(
    registry: ToolRegistry,
    settings: Settings,
    memory: MemoryStore,
) -> None:
    idle = IdleDetector(memory=memory)
    suggestions = SuggestionEngine()

    def _last_command(memory: MemoryStore) -> str:
        activity = memory.get_state("activity", default={}) or {}
        return str(activity.get("last_command") or "")

    def suggest_next(args: dict[str, Any]) -> dict[str, Any]:
        last = str(args.get("last_command", "")).strip() or _last_command(memory)
        items = suggestions.suggest(last)
        return {"count": len(items), "suggestions": items, "result_text": " - ".join(items)}

    def idle_time(_: dict[str, Any]) -> dict[str, Any]:
        seconds = idle.idle_seconds()
        return {
            "idle_seconds": seconds,
            "result_text": f"Idle for {seconds} second(s).",
        }

    def idle_status(args: dict[str, Any]) -> dict[str, Any]:
        threshold = int(args.get("threshold_seconds", 300))
        idle_now = idle.is_idle(threshold)
        return {"idle": idle_now, "idle_seconds": idle.idle_seconds(), "threshold_seconds": threshold}

    registry.register(
        name="suggest_next",
        description="Predict useful next commands based on the last activity.",
        risk="low",
        parameters={"limit": "integer", "last_command": "string"},
        handler=suggest_next,
    )
    registry.register(
        name="idle_time",
        description="Report how long the user has been idle (no input).",
        risk="low",
        parameters={},
        handler=idle_time,
    )
    registry.register(
        name="idle_status",
        description="Report whether the user has been idle beyond a threshold (seconds).",
        risk="low",
        parameters={"threshold_seconds": "integer"},
        handler=idle_status,
    )
