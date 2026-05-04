from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    risk: str
    parameters: dict[str, Any]
    handler: ToolHandler


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
    ) -> None:
        key = name.strip()
        if not key:
            raise ValueError("Tool name must be non-empty.")
        self._tools[key] = ToolSpec(
            name=key,
            description=description.strip(),
            risk=risk.lower().strip(),
            parameters=parameters,
            handler=handler,
        )

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
            }
            for tool in self._tools.values()
        ]

