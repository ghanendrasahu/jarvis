from __future__ import annotations

from app.config import Settings
from app.core.memory import MemoryStore
from app.tools.browser_tools import register_browser_tools
from app.tools.file_tools import register_file_tools
from app.tools.interaction_tools import register_interaction_tools
from app.tools.metrics_tools import register_metrics_tools
from app.tools.registry import ToolRegistry
from app.tools.shell_tools import register_shell_tools
from app.tools.system_tools import register_system_tools


def register_all_tools(registry: ToolRegistry, settings: Settings, memory: MemoryStore | None = None) -> None:
    register_system_tools(registry, settings)
    register_file_tools(registry, settings)
    register_browser_tools(registry, settings)
    register_interaction_tools(registry, settings)
    register_shell_tools(registry, settings)
    if memory is not None:
        register_metrics_tools(registry, settings, memory)
