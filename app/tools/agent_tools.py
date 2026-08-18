from __future__ import annotations

from typing import Any

from app.config import Settings
from app.tools.registry import ToolRegistry


def register_agent_tools(registry: ToolRegistry, settings: Settings) -> None:
    def agent_done(args: dict[str, Any]) -> dict[str, Any]:
        summary = str(args.get("summary", "")).strip()
        return {"done": True, "summary": summary or "Task complete."}

    registry.register(
        name="agent_done",
        description="Signal that an autonomous task is complete. Use as the final action.",
        risk="low",
        parameters={"summary": "string"},
        handler=agent_done,
    )
