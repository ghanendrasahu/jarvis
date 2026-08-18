from __future__ import annotations

from dataclasses import dataclass

from app.agents.job_agent import JobAgent, get_job_agent
from app.config import Settings, get_settings
from app.core.agent import AutonomousAgent
from app.core.approvals import ApprovalManager
from app.core.events import EventHub
from app.core.executor import ActionExecutor
from app.core.memory import MemoryStore
from app.core.orchestrator import JarvisOrchestrator
from app.core.router import DirectCommandRouter
from app.core.scheduler import Scheduler
from app.llm.planner import BasePlanner, create_planner
from app.tools import register_all_tools
from app.tools.registry import ToolRegistry
from app.tools.scheduler_tools import register_scheduler_tools
from app.voice.loop import VoiceController


@dataclass
class Runtime:
    settings: Settings
    registry: ToolRegistry
    approvals: ApprovalManager
    memory: MemoryStore
    planner: BasePlanner
    executor: ActionExecutor
    agent: AutonomousAgent
    orchestrator: JarvisOrchestrator
    voice: VoiceController
    job_agent: JobAgent
    scheduler: Scheduler


def build_runtime(events: EventHub | None = None) -> Runtime:
    settings = get_settings()
    memory = MemoryStore(settings.database_path)
    memory.prune(settings.db_retention_days)
    registry = ToolRegistry()
    register_all_tools(registry, settings, memory=memory)

    approvals = ApprovalManager()
    planner = create_planner(settings)
    router = DirectCommandRouter()
    executor = ActionExecutor(registry=registry, approvals=approvals, settings=settings)
    agent = AutonomousAgent(
        settings=settings,
        registry=registry,
        planner=planner,
        executor=executor,
        approvals=approvals,
        memory=memory,
    )
    orchestrator = JarvisOrchestrator(
        settings=settings,
        registry=registry,
        router=router,
        planner=planner,
        executor=executor,
        approvals=approvals,
        memory=memory,
        agent=agent,
        events=events,
    )
    voice = VoiceController(settings=settings, orchestrator=orchestrator)
    job_agent = get_job_agent(settings=settings, memory=memory)
    scheduler = Scheduler(settings=settings, memory=memory, orchestrator=orchestrator)
    register_scheduler_tools(registry, scheduler)

    return Runtime(
        settings=settings,
        registry=registry,
        approvals=approvals,
        memory=memory,
        planner=planner,
        executor=executor,
        agent=agent,
        orchestrator=orchestrator,
        voice=voice,
        job_agent=job_agent,
        scheduler=scheduler,
    )
