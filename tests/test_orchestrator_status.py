"""B5 regression tests: planner total failures must be recorded as failed executions."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.config import Settings
from app.core.memory import MemoryStore
from app.core.orchestrator import JarvisOrchestrator
from app.llm.planner import ALL_PROVIDERS_FAILED_SUMMARY
from app.models.schemas import Action, Plan, ToolExecutionResult
from app.tools.registry import ToolRegistry


class RecordingPlanner:
    def __init__(self, plans: list[Plan]) -> None:
        self.plans = list(plans)
        self.calls = 0

    def plan(self, *args: Any, **kwargs: Any) -> Plan:
        self.calls += 1
        if not self.plans:
            raise AssertionError("unexpected extra planner call")
        return self.plans.pop(0)


def make_orchestrator(
    tmp_path: Any,
    plans: list[Plan],
    direct_actions: list[Action] | None = None,
) -> tuple[JarvisOrchestrator, RecordingPlanner, MemoryStore]:
    settings = Settings(llm_provider="groq", llm_cache_enabled=False)
    memory = MemoryStore(str(tmp_path / "orch.db"))
    registry = ToolRegistry()
    registry.register(
        "get_current_time",
        "current time tool",
        risk="low",
        parameters={},
        handler=lambda _args: {"ok": True},
    )
    planner = RecordingPlanner(plans)
    executor = SimpleNamespace(
        execute=lambda command, action, auto_approve=False: ToolExecutionResult(
            tool=action.tool,
            status="completed",
            output={"echo": action.tool},
        )
    )
    router = SimpleNamespace(route=lambda command, context: list(direct_actions or []))
    orchestrator = JarvisOrchestrator(
        settings=settings,
        registry=registry,
        router=router,  # type: ignore[arg-type]
        planner=planner,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        approvals=SimpleNamespace(),
        memory=memory,
    )
    return orchestrator, planner, memory


def failure_plan() -> Plan:
    return Plan(
        summary=ALL_PROVIDERS_FAILED_SUMMARY,
        actions=[],
        final_response="Rate limited by Groq. Try again in a few seconds.",
    )


# --------------------------------------------------------------------------- metrics


def test_all_providers_failure_is_recorded_as_failed(tmp_path: Any) -> None:
    orchestrator, _planner, memory = make_orchestrator(tmp_path, [failure_plan()])

    response = orchestrator.execute_command("plan my trip", source="test")

    assert response.status == "failed"
    assert "Rate limited by Groq" in response.response_text
    rows = memory.recent_execution_metrics(limit=10)
    assert len(rows) == 1
    assert rows[0]["status"] == "failed", "a total planner failure must not inflate success metrics"


def test_failure_metric_shape_matches_execution_status_model(tmp_path: Any) -> None:
    orchestrator, _planner, memory = make_orchestrator(tmp_path, [failure_plan()])
    orchestrator.execute_command("plan my trip", source="test")

    row = dict(memory.recent_execution_metrics(limit=1)[0])
    assert row["command"] == "plan my trip"
    assert row["source"] == "test"
    assert row["actions_total"] == 0
    assert row["actions_completed"] == 0
    assert row["actions_failed"] == 0
    assert row["actions_pending"] == 0
    for field in ("duration_ms", "created_at"):
        assert field in row


def test_successful_planner_execution_still_counted_as_completed(tmp_path: Any) -> None:
    success = Plan(
        summary="time plan",
        actions=[Action(tool="get_current_time", args={}, risk="low")],
        final_response="",
    )
    orchestrator, planner, memory = make_orchestrator(tmp_path, [success])

    response = orchestrator.execute_command("what time is it", source="test")

    assert planner.calls == 1
    assert response.status == "completed"
    rows = memory.recent_execution_metrics(limit=10)
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["actions_total"] == 1
    assert rows[0]["actions_completed"] == 1


def test_direct_router_instant_success_path_unchanged(tmp_path: Any) -> None:
    action = Action(tool="get_current_time", args={}, risk="low")
    orchestrator, planner, memory = make_orchestrator(tmp_path, [], direct_actions=[action])

    response = orchestrator.execute_command("get current time", source="test")

    assert planner.calls == 0, "direct routes must not consult the LLM planner"
    assert response.status == "completed"
    rows = memory.recent_execution_metrics(limit=10)
    assert rows[0]["status"] == "completed"
    assert rows[0]["actions_total"] == 1


def test_conversational_zero_action_plan_still_completed(tmp_path: Any) -> None:
    """Only the sentinel failure plan is failed; normal chat-style plans stay successful."""
    chat = Plan(summary="chat", actions=[], final_response="Hello Boss.")
    orchestrator, _planner, memory = make_orchestrator(tmp_path, [chat])

    response = orchestrator.execute_command("tell me a joke about clouds", source="test")

    assert response.status == "completed"
    assert response.response_text == "Hello Boss."
    assert memory.recent_execution_metrics(limit=10)[0]["status"] == "completed"


def test_planner_exception_path_still_failed(tmp_path: Any) -> None:
    """The pre-existing raised-exception path keeps its failed status (regression guard)."""
    settings = Settings(llm_provider="groq", llm_cache_enabled=False)
    memory = MemoryStore(str(tmp_path / "orch2.db"))
    registry = ToolRegistry()
    planner = RecordingPlanner([])

    def boom(*_args: Any, **_kwargs: Any) -> Plan:
        raise RuntimeError("network down")

    planner.plan = boom  # type: ignore[method-assign]
    executor = SimpleNamespace()
    orchestrator = JarvisOrchestrator(
        settings=settings,
        registry=registry,
        router=SimpleNamespace(route=lambda command, context: []),  # type: ignore[arg-type]
        planner=planner,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        approvals=SimpleNamespace(),
        memory=memory,
    )

    response = orchestrator.execute_command("anything", source="test")

    assert response.status == "failed"
    assert "Planning failed" in response.response_text
    assert memory.recent_execution_metrics(limit=5)[0]["status"] == "failed"
