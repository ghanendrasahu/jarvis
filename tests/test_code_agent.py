from __future__ import annotations

from app.agents.code_agent import CodeAgent
from app.config import Settings
from app.core.agent import AgentRunResult
from app.core.approvals import ApprovalManager
from app.core.executor import ActionExecutor
from app.core.memory import MemoryStore
from app.core.orchestrator import _is_code_worthy
from app.models.schemas import ToolExecutionResult
from app.tools.code_tools import register_code_tools
from app.tools.registry import ToolRegistry


def _harness(tmp_path, test_source: str, rounds: int = 2):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "test_demo.py").write_text(test_source, encoding="utf-8")
    settings = Settings(
        workspace_root=str(project),
        database_path=str(tmp_path / "j.db"),
        code_max_fix_rounds=rounds,
    )
    memory = MemoryStore(settings.database_path)
    registry = ToolRegistry()
    register_code_tools(registry, settings)
    approvals = ApprovalManager()
    executor = ActionExecutor(registry=registry, approvals=approvals, settings=settings)

    class StubAgent:
        def __init__(self) -> None:
            self.results: list[AgentRunResult] = []
            self.resume_results: list[AgentRunResult] = []

        def run(self, command, source="agent", auto_approve=False, on_step=None, role="", **_: object):
            result = self.results.pop(0) if self.results else AgentRunResult(
                command=command, status="completed", final_response="edited", iterations=1, actions_total=1,
                results=[ToolExecutionResult(tool="edit_file", status="completed", output={"path": "x.py"})],
            )
            return result

        def resume(self, approval_id, executed_outcome, on_step=None, **_: object):
            return self.resume_results.pop(0) if self.resume_results else None

        def forget(self, approval_id: str) -> None:
            pass

    agent = CodeAgent(StubAgent(), executor, settings, memory)  # type: ignore[arg-type]
    return agent, settings, memory


def _edit_result(command: str) -> AgentRunResult:
    return AgentRunResult(
        command=command,
        status="completed",
        final_response="edited",
        iterations=1,
        actions_total=1,
        results=[ToolExecutionResult(tool="edit_file", status="completed", output={"path": "x.py", "replaced": 1})],
    )


def test_code_agent_verifies_and_reports_success(tmp_path):
    agent, _, _ = _harness(tmp_path, "def test_ok():\n    assert True\n", rounds=1)
    result = agent.run("fix a tiny thing")
    assert result.status == "completed"
    assert result.stopped_reason == "verified"
    assert "verified" in result.final_response
    tools = {item.tool for item in result.results}
    assert "run_tests" in tools and "lint_code" in tools


def test_code_agent_reports_failed_verification(tmp_path):
    agent, _, _ = _harness(tmp_path, "def test_bad():\n    assert False\n", rounds=1)
    result = agent.run("fix a tiny thing")
    assert result.status == "partial"
    assert result.stopped_reason == "verification_failed"
    assert "verification failed" in result.final_response.lower()


def test_code_agent_skips_verification_when_no_code_touched(tmp_path):
    agent, _, _ = _harness(tmp_path, "def test_ok():\n    assert True\n", rounds=1)
    agent.agent.results = [
        AgentRunResult(command="c", status="completed", final_response="nothing to change", iterations=1, actions_total=0, results=[])
    ]
    result = agent.run("fix nothing")
    assert result.status == "completed"
    assert all(item.tool != "run_tests" for item in result.results)


def test_code_agent_resume_path(tmp_path):
    agent, _, _ = _harness(tmp_path, "def test_ok():\n    assert True\n", rounds=1)
    agent._rounds["abc"] = {
        "command": "fix x",
        "source": "cli",
        "auto_approve": False,
        "max_fix_rounds": 1,
        "round_index": 1,
        "combined": [],
        "last_summary": "",
    }
    outcome = ToolExecutionResult(tool="edit_file", status="completed", output={"replaced": 1})
    agent.agent.resume_results = [_edit_result("fix x")]
    result = agent.resume("abc", outcome)
    assert result is not None
    assert result.stopped_reason == "verified"
    assert agent._rounds == {}


def test_code_agent_resume_unknown_returns_none(tmp_path):
    agent, _, _ = _harness(tmp_path, "def test_ok():\n    assert True\n")
    assert agent.resume("missing", ToolExecutionResult(tool="x", status="completed")) is None


def test_is_code_worthy_detects_code_tasks():
    assert _is_code_worthy("fix the bug in scheduler")
    assert _is_code_worthy("debug the failing test")
    assert _is_code_worthy("refactor file_tools")
    assert _is_code_worthy("write a function to parse dates")
    assert not _is_code_worthy("open chrome")
    assert not _is_code_worthy("system status")
