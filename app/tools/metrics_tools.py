from __future__ import annotations

from typing import Any

from app.config import Settings
from app.core.memory import MemoryStore
from app.tools.registry import ToolRegistry


def register_metrics_tools(registry: ToolRegistry, settings: Settings, memory: MemoryStore) -> None:
    def get_runtime_metrics(args: dict[str, Any]) -> dict[str, Any]:
        limit = max(10, min(5000, int(args.get("limit", 200))))
        return {
            "summary": memory.metrics_summary(limit=limit),
            "recent": memory.recent_execution_metrics(limit=min(limit, 500)),
        }

    def get_runtime_context(_: dict[str, Any]) -> dict[str, Any]:
        return {"context": memory.get_state("runtime_context", default={})}

    registry.register(
        name="get_runtime_metrics",
        description="Return runtime command quality metrics and recent execution samples.",
        risk="low",
        parameters={"limit": "integer"},
        handler=get_runtime_metrics,
    )
    registry.register(
        name="get_runtime_context",
        description="Return current conversational runtime context state.",
        risk="low",
        parameters={},
        handler=get_runtime_context,
    )
