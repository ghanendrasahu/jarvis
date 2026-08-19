from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
ReadWriteKind = Literal["read", "write", "execute"]


@dataclass
class ToolSpec:
    name: str
    description: str
    risk: str
    parameters: dict[str, Any]
    handler: ToolHandler
    # Unified contract metadata (Phase B). Defaults keep legacy registrations valid.
    category: str = "general"
    read_write: ReadWriteKind = "execute"
    external_action: bool = False
    timeout_seconds: float | None = None
    cancellation: str = "abort"
    error_behavior: str = "raise"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        description: str,
        risk: str,
        parameters: dict[str, Any],
        handler: ToolHandler,
        category: str = "general",
        read_write: ReadWriteKind = "execute",
        external_action: bool = False,
        timeout_seconds: float | None = None,
        cancellation: str = "abort",
        error_behavior: str = "raise",
    ) -> None:
        key = name.strip()
        if not key:
            raise ValueError("Tool name must be non-empty.")
        if key in self._tools:
            raise ValueError(f"Duplicate tool registration: {key}")
        if risk.lower().strip() not in {"low", "medium", "high"}:
            raise ValueError(f"Invalid risk '{risk}' for tool {key}; use low|medium|high.")
        self._tools[key] = ToolSpec(
            name=key,
            description=description.strip(),
            risk=risk.lower().strip(),
            parameters=parameters,
            handler=handler,
            category=category.strip() or "general",
            read_write=read_write,
            external_action=bool(external_action),
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
            error_behavior=error_behavior,
        )

    def set_metadata(
        self,
        name: str,
        *,
        category: str | None = None,
        read_write: ReadWriteKind | None = None,
        external_action: bool | None = None,
        timeout_seconds: float | None = None,
        cancellation: str | None = None,
        error_behavior: str | None = None,
    ) -> bool:
        """Enrich an already-registered tool's contract metadata. Returns False if unknown."""
        tool = self._tools.get(name)
        if tool is None:
            return False
        if category is not None:
            tool.category = category.strip() or "general"
        if read_write is not None:
            tool.read_write = read_write
        if external_action is not None:
            tool.external_action = bool(external_action)
        if timeout_seconds is not None:
            tool.timeout_seconds = timeout_seconds
        if cancellation is not None:
            tool.cancellation = cancellation
        if error_behavior is not None:
            tool.error_behavior = error_behavior
        return True

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = self.get(name)
        if not tool:
            raise KeyError(f"Tool not found: {name}")
        return tool.handler(args)

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "risk": tool.risk,
                "parameters": tool.parameters,
                "category": tool.category,
                "read_write": tool.read_write,
                "external_action": tool.external_action,
                "timeout_seconds": tool.timeout_seconds,
            }
            for tool in self._tools.values()
        ]

