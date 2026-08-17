from __future__ import annotations

from app.config import Settings
from app.core.memory import MemoryStore
from app.core.rag import RagIndex
from app.tools.agent_tools import register_agent_tools
from app.tools.browser_action import register_browser_action
from app.tools.browser_tools import register_browser_tools
from app.tools.file_tools import register_file_tools
from app.tools.interaction_tools import register_interaction_tools
from app.tools.job_tools import register_job_tools
from app.tools.metrics_tools import register_metrics_tools
from app.tools.rag_tools import register_rag_tools
from app.tools.registry import ToolRegistry
from app.tools.shell_tools import register_shell_tools
from app.tools.system_tools import register_system_tools


def register_all_tools(
    registry: ToolRegistry,
    settings: Settings,
    memory: MemoryStore | None = None,
    rag: RagIndex | None = None,
) -> None:
    register_system_tools(registry, settings)
    register_file_tools(registry, settings)
    register_browser_tools(registry, settings)
    register_browser_action(registry, settings)
    register_interaction_tools(registry, settings)
    register_shell_tools(registry, settings)
    register_agent_tools(registry, settings)
    if rag is not None:
        register_rag_tools(registry, settings, rag)
    if memory is not None:
        register_metrics_tools(registry, settings, memory)
        register_job_tools(registry, settings, memory)
