from __future__ import annotations

import time
from dataclasses import dataclass

from app.agents.code_agent import CodeAgent
from app.agents.job_agent import JobAgent, get_job_agent
from app.config import Settings, get_settings
from app.core.agent import AutonomousAgent
from app.core.approvals import ApprovalManager
from app.core.calendar import CalendarStore
from app.core.diagnoser import SystemDiagnoser
from app.core.events import EventHub
from app.core.executor import ActionExecutor
from app.core.logging import get_logger
from app.core.memory import MemoryStore
from app.core.monitor import ResourceMonitor
from app.core.orchestrator import JarvisOrchestrator
from app.core.rag import RagIndex
from app.core.retrieval_planner import wrap_planner_with_rag
from app.core.router import DirectCommandRouter
from app.core.scheduler import Scheduler
from app.core.task_engine import TaskEngine
from app.llm.planner import BasePlanner, create_planner
from app.tools import register_all_tools
from app.tools.registry import ToolRegistry
from app.voice.loop import VoiceController
from app.voice.speaker import Speaker

logger = get_logger("bootstrap")


@dataclass
class Runtime:
    settings: Settings
    registry: ToolRegistry
    approvals: ApprovalManager
    memory: MemoryStore
    planner: BasePlanner
    executor: ActionExecutor
    agent: AutonomousAgent
    code_agent: CodeAgent | None
    orchestrator: JarvisOrchestrator
    voice: VoiceController
    job_agent: JobAgent
    scheduler: Scheduler
    rag: RagIndex | None
    calendar: CalendarStore
    monitor: ResourceMonitor
    diagnoser: SystemDiagnoser


def build_runtime(events: EventHub | None = None) -> Runtime:
    started_at = time.perf_counter()
    timings: list[tuple[str, float]] = []

    def _step(label: str, start: float) -> None:
        timings.append((label, (time.perf_counter() - start) * 1000))

    step = time.perf_counter()
    settings = get_settings()
    memory = MemoryStore(settings.database_path)
    memory.prune(settings.db_retention_days)
    rag = RagIndex(settings) if settings.rag_enabled else None
    registry = ToolRegistry()
    _step("settings+memory+rag", step)

    step = time.perf_counter()
    approvals = ApprovalManager(memory=memory)
    planner = wrap_planner_with_rag(create_planner(settings), rag)
    reasoning_planner = wrap_planner_with_rag(create_planner(settings, for_agent=True), rag)
    router = DirectCommandRouter()
    _step("planners+router", step)

    step = time.perf_counter()
    executor = ActionExecutor(registry=registry, approvals=approvals, settings=settings)
    agent = AutonomousAgent(
        settings=settings,
        registry=registry,
        planner=reasoning_planner,
        executor=executor,
        approvals=approvals,
        memory=memory,
    )
    code_agent = CodeAgent(agent=agent, executor=executor, settings=settings, memory=memory)
    task_engine = TaskEngine(executor=executor, memory=memory, settings=settings, events=events)
    orchestrator = JarvisOrchestrator(
        settings=settings,
        registry=registry,
        router=router,
        planner=planner,
        executor=executor,
        approvals=approvals,
        memory=memory,
        agent=agent,
        code_agent=code_agent,
        events=events,
        task_engine=task_engine,
    )
    _step("executor+agents+orchestrator", step)

    step = time.perf_counter()
    voice = VoiceController(settings=settings, orchestrator=orchestrator)
    job_agent = get_job_agent(settings=settings, memory=memory)
    scheduler = Scheduler(settings=settings, memory=memory, orchestrator=orchestrator)
    calendar = CalendarStore(settings.calendar_path)
    _step("voice+job_agent+scheduler+calendar", step)

    step = time.perf_counter()
    # Resource monitor for system intelligence (Phase L)
    monitor = ResourceMonitor(history_size=60, interval=1.0)
    monitor.start()
    diagnoser = SystemDiagnoser(monitor, settings)
    register_all_tools(
        registry,
        settings,
        memory=memory,
        rag=rag,
        scheduler=scheduler,
        calendar=calendar,
        task_engine=task_engine,
        monitor=monitor,
        orchestrator=orchestrator,
    )
    _step("monitor+tool registration", step)

    reminder_speaker = Speaker(enabled=settings.tts_enabled)

    def _announce_scheduled_run(job: dict, status: str, text: str) -> None:
        try:
            if status == "success" and job.get("command", "").startswith("announce "):
                reminder_speaker.speak(text)
        except Exception:
            logger.warning("reminder speech failed", exc_info=True)

    scheduler.on_run = _announce_scheduled_run

    step = time.perf_counter()
    if rag is not None:
        try:
            indexed = rag.index_notes_dir()
            if indexed:
                logger.info("indexed %d file(s) from notes dir", indexed)
        except Exception:
            logger.warning("notes dir indexing failed", exc_info=True)
    _step("notes indexing", step)

    total_ms = (time.perf_counter() - started_at) * 1000
    breakdown = ", ".join(f"{label}={ms:.1f}ms" for label, ms in timings)
    logger.info("runtime build complete in %.1fms | %s", total_ms, breakdown)

    return Runtime(
        settings=settings,
        registry=registry,
        approvals=approvals,
        memory=memory,
        planner=planner,
        executor=executor,
        agent=agent,
        code_agent=code_agent,
        orchestrator=orchestrator,
        voice=voice,
        job_agent=job_agent,
        scheduler=scheduler,
        rag=rag,
        calendar=calendar,
        monitor=monitor,
        diagnoser=diagnoser,
    )
