from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.core.approvals import ApprovalManager
from app.core.executor import ActionExecutor
from app.core.memory import MemoryStore
from app.core.orchestrator import JarvisOrchestrator
from app.core.router import DirectCommandRouter
from app.llm.planner import BasePlanner, create_planner
from app.tools import register_all_tools
from app.tools.registry import ToolRegistry
from app.voice.loop import VoiceController


@dataclass
class Runtime:
    settings: Settings
    registry: ToolRegistry
    approvals: ApprovalManager
    memory: MemoryStore
    planner: BasePlanner
    executor: ActionExecutor
    orchestrator: JarvisOrchestrator
    voice: VoiceController


def build_runtime() -> Runtime:
    settings = get_settings()
    memory = MemoryStore(settings.database_path)
    registry = ToolRegistry()
    register_all_tools(registry, settings, memory=memory)

    approvals = ApprovalManager()
    planner = create_planner(settings)
    router = DirectCommandRouter()
    executor = ActionExecutor(registry=registry, approvals=approvals, settings=settings)
    orchestrator = JarvisOrchestrator(
        settings=settings,
        registry=registry,
        router=router,
        planner=planner,
        executor=executor,
        approvals=approvals,
        memory=memory,
    )
    voice = VoiceController(settings=settings, orchestrator=orchestrator)

    return Runtime(
        settings=settings,
        registry=registry,
        approvals=approvals,
        memory=memory,
        planner=planner,
        executor=executor,
        orchestrator=orchestrator,
        voice=voice,
    )
