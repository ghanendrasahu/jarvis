from __future__ import annotations

from typing import Any

from app.config import Settings
from app.core.agent import AutonomousAgent
from app.core.approvals import ApprovalManager
from app.models.schemas import Action, Plan, ToolExecutionResult


class FakePlanner:
    def __init__(self, plans: list[Plan]) -> None:
        self._queue = list(plans)

    def plan(
        self,
        command: str,
        catalog: Any,
        history: Any,
        agent_context: Any = None,
        tool_history: Any = None,
        known_facts: Any = None,
    ) -> Plan:
        return self._queue.pop(0) if self._queue else Plan(summary="done", actions=[])


class FakeExecutor:
    def __init__(self, outcomes: list[ToolExecutionResult]) -> None:
        self._queue = list(outcomes)
        self.calls: list[tuple[str, Action]] = []

    def execute(self, command: str, action: Action, auto_approve: bool = False) -> ToolExecutionResult:
        self.calls.append((command, action))
        if self._queue:
            return self._queue.pop(0)
        return ToolExecutionResult(tool=action.tool, status="completed", output={"ok": True})


class FakeRegistryAndMemory:
    def __init__(self, executor: FakeExecutor) -> None:
        self._executor = executor

    def catalog(self) -> list[dict[str, Any]]:
        return []

    def recent_conversation(self, limit: int = 10) -> list[dict[str, Any]]:
        return []

    def recent_tool_history(self, limit: int = 30) -> list[dict[str, Any]]:
        return []

    def get_state(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
        return dict(default or {})

    def log_tool_result(self, **_: Any) -> None:
        pass


def _agent(planner: FakePlanner, executor: FakeExecutor, **settings_overrides: Any) -> AutonomousAgent:
    settings = Settings(workspace_root=".", **settings_overrides)
    stub = FakeRegistryAndMemory(executor)
    return AutonomousAgent(
        settings=settings,
        registry=stub,
        planner=planner,
        executor=executor,
        approvals=ApprovalManager(),
        memory=stub,
    )


def test_agent_completes_when_planner_emits_agent_done():
    planner = FakePlanner(
        [Plan(summary="wrap up", actions=[Action(tool="agent_done", args={"summary": "All done"}, risk="low")])]
    )
    executor = FakeExecutor([])
    agent = _agent(planner, executor)
    result = agent.run("do the thing")
    assert result.status == "completed"
    assert result.stopped_reason == "task_complete"
    assert result.final_response == "All done"


def test_agent_hits_action_cap():
    planner = FakePlanner([])
    executor = FakeExecutor([])
    agent = _agent(planner, executor, agent_action_cap=3, agent_max_iterations=10)

    class AlwaysPlan:
        def plan(
        self,
        command: str,
        catalog: Any,
        history: Any,
        agent_context: Any = None,
        tool_history: Any = None,
        known_facts: Any = None,
    ) -> Plan:
            return Plan(
                summary="next",
                actions=[
                    Action(tool="get_current_time", args={}, risk="low"),
                    Action(tool="get_current_time", args={}, risk="low"),
                ],
            )

    agent.planner = AlwaysPlan()
    result = agent.run("loop")
    assert result.actions_total == 3
    assert result.status in {"partial", "completed"}
    assert result.stopped_reason == "reached action cap"


def test_agent_stops_after_tool_fails_twice():
    planner = FakePlanner([])
    outcomes = [
        ToolExecutionResult(tool="read_text_file", status="failed", error="boom"),
        ToolExecutionResult(tool="read_text_file", status="failed", error="boom again"),
    ]
    executor = FakeExecutor(outcomes)
    agent = _agent(planner, executor)

    class AlwaysFail:
        def plan(
        self,
        command: str,
        catalog: Any,
        history: Any,
        agent_context: Any = None,
        tool_history: Any = None,
        known_facts: Any = None,
    ) -> Plan:
            return Plan(
                summary="retry",
                actions=[
                    Action(tool="read_text_file", args={"path": "x"}, risk="low"),
                    Action(tool="read_text_file", args={"path": "x"}, risk="low"),
                ],
            )

    agent.planner = AlwaysFail()
    result = agent.run("read repeatedly")
    assert result.stopped_reason == "tool 'read_text_file' failed twice; stopping"
    assert result.status in {"failed", "partial"}


def test_agent_returns_failed_on_planner_error():
    class BrokenPlanner:
        def plan(
        self,
        command: str,
        catalog: Any,
        history: Any,
        agent_context: Any = None,
        tool_history: Any = None,
        known_facts: Any = None,
    ) -> Plan:
            raise RuntimeError("LLM provider unreachable")

    executor = FakeExecutor([])
    agent = _agent(FakePlanner([]), executor)
    agent.planner = BrokenPlanner()
    result = agent.run("research anything")
    assert result.status == "failed"
    assert result.stopped_reason == "planner_error"
    assert "Planning failed" in result.final_response


def test_agent_stops_when_approval_required():
    planner = FakePlanner([])
    outcome = ToolExecutionResult(
        tool="run_shell_command",
        status="pending",
        risk="high",
        output={"message": "needs approval"},
        pending_approval_id="abc",
    )
    executor = FakeExecutor([outcome])
    agent = _agent(planner, executor)

    class AlwaysPlan:
        def plan(
            self,
            command: str,
            catalog: Any,
            history: Any,
            agent_context: Any = None,
            tool_history: Any = None,
            known_facts: Any = None,
        ) -> Plan:
            return Plan(
                summary="run shell",
                actions=[Action(tool="run_shell_command", args={"command": "Get-Date"}, risk="high")],
            )

    agent.planner = AlwaysPlan()
    result = agent.run("run a command")
    assert result.status == "needs_approval"
    assert result.stopped_reason == "approval_required"


def test_agent_resumes_after_approval_continues_loop():
    planner = FakePlanner([])
    pending = ToolExecutionResult(
        tool="run_shell_command",
        status="pending",
        risk="high",
        output={"message": "needs approval"},
        pending_approval_id="abc",
    )
    executor = FakeExecutor([pending])
    agent = _agent(planner, executor)

    class TwoPhasePlan:
        def __init__(self) -> None:
            self.calls = 0

        def plan(
            self,
            command: str,
            catalog: Any,
            history: Any,
            agent_context: Any = None,
            tool_history: Any = None,
            known_facts: Any = None,
        ) -> Plan:
            self.calls += 1
            if self.calls == 1:
                return Plan(
                    summary="run shell",
                    actions=[Action(tool="run_shell_command", args={"command": "Get-Date"}, risk="high")],
                )
            return Plan(
                summary="wrap up",
                actions=[Action(tool="agent_done", args={"summary": "Finished after approval"}, risk="low")],
            )

    agent.planner = TwoPhasePlan()
    first = agent.run("run a command")
    assert first.status == "needs_approval"
    assert first.stopped_reason == "approval_required"

    executed = ToolExecutionResult(tool="run_shell_command", status="completed", risk="high", output={"result_text": "done"})
    resumed = agent.resume("abc", executed)
    assert resumed is not None
    assert resumed.status == "completed"
    assert resumed.stopped_reason == "task_complete"
    assert "Finished after approval" in resumed.final_response
    assert resumed.results[0].status == "completed"
    assert resumed.actions_total == 2


def test_agent_resume_returns_none_without_token():
    executor = FakeExecutor([])
    agent = _agent(FakePlanner([]), executor)
    assert agent.resume("nope", ToolExecutionResult(tool="x", status="completed")) is None
