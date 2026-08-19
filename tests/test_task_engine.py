from __future__ import annotations

import threading
import time
from typing import Any

from app.config import Settings
from app.core.approvals import ApprovalManager
from app.core.events import EventHub
from app.core.executor import ActionExecutor
from app.core.memory import MemoryStore
from app.core.task_engine import TaskEngine
from app.models.schemas import Action
from app.tools import register_all_tools
from app.tools.registry import ToolRegistry


def _engine(tmp_path) -> tuple[TaskEngine, ActionExecutor, ToolRegistry, ApprovalManager]:
    registry = ToolRegistry()
    settings = Settings(workspace_root=".", database_path=str(tmp_path / "exec.db"))
    approvals = ApprovalManager()
    executor = ActionExecutor(registry, approvals, settings)
    memory = MemoryStore(str(tmp_path / "mem.db"))
    engine = TaskEngine(executor=executor, memory=memory, settings=settings)
    return engine, executor, registry, approvals


def test_create_builds_steps_and_tracks_index(tmp_path):
    engine, executor, registry, approvals = _engine(tmp_path)

    def handler(args: dict[str, Any]) -> dict[str, Any]:
        return {"result_text": "ok"}

    registry.register("t_echo", "echo", "low", {}, handler)
    actions = [Action(tool="t_echo", args={"msg": "a"}), Action(tool="t_echo", args={"msg": "b"})]
    task = engine.create("test task", actions, source="tool")
    assert task.status == "pending"
    assert len(task.steps) == 2
    assert task.steps[0].status == "pending"
    assert [t.id for t in engine.list()] == [task.id]
    assert engine.get(task.id) is not None


def test_run_executes_steps_in_order(tmp_path):
    engine, executor, registry, approvals = _engine(tmp_path)
    order: list[str] = []

    def handler(args: dict[str, Any]) -> dict[str, Any]:
        order.append(str(args["msg"]))
        return {"result_text": args["msg"]}

    registry.register("t_echo", "echo", "low", {}, handler)
    actions = [Action(tool="t_echo", args={"msg": "a"}), Action(tool="t_echo", args={"msg": "b"})]
    task = engine.create("test task", actions)
    finished = engine.run(task.id)
    assert finished is not None
    assert finished.status == "completed"
    assert order == ["a", "b"]
    assert finished.progress["percent"] == 100.0
    assert finished.steps[0].status == "completed"


def test_run_marks_failure(tmp_path):
    engine, executor, registry, approvals = _engine(tmp_path)

    def failing(_: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")

    registry.register("t_fail", "fails", "low", {}, failing)
    task = engine.create("fail task", [Action(tool="t_fail")])
    finished = engine.run(task.id)
    assert finished is not None
    assert finished.status == "failed"
    assert finished.steps[0].status == "failed"
    assert "boom" in (finished.steps[0].error or "")


def test_approval_pending_then_resume_after_approval(tmp_path):
    engine, executor, registry, approvals = _engine(tmp_path)

    def handler(_: dict[str, Any]) -> dict[str, Any]:
        return {"result_text": "done"}

    registry.register("t_medium", "needs approval", "medium", {}, handler)
    task = engine.create("approve task", [Action(tool="t_medium", risk="medium")])
    finished = engine.run(task.id)
    assert finished is not None
    assert finished.status == "needs_approval"
    step = finished.steps[0]
    assert step.status == "pending"
    assert step.pending_approval_id is not None

    pending = approvals.approve(step.pending_approval_id)
    assert pending is not None
    outcome = executor.execute(pending.command, pending.action, auto_approve=True)
    assert engine.resume_after_approval(step.pending_approval_id, outcome) is True
    task = engine.get(task.id)
    assert task is not None
    assert task.status == "completed"
    assert task.steps[0].status == "completed"


def test_cancel_marks_remaining_steps(tmp_path):
    engine, executor, registry, approvals = _engine(tmp_path)
    gate = threading.Event()

    def slow(_: dict[str, Any]) -> dict[str, Any]:
        gate.wait(2)
        return {"result_text": "done"}

    registry.register("t_slow", "slow", "low", {}, slow)
    task = engine.create("cancel task", [Action(tool="t_slow")] * 3)
    results: list[Any] = []

    def run_in_thread() -> None:
        results.append(engine.run(task.id))

    thread = threading.Thread(target=run_in_thread)
    thread.start()
    time.sleep(0.2)
    assert engine.cancel(task.id) is True
    gate.set()
    thread.join(timeout=5)
    task = engine.get(task.id)
    assert task is not None
    assert task.status == "canceled"
    assert task.steps[0].status == "completed"
    assert task.steps[1].status == "canceled"
    assert task.steps[2].status == "canceled"


def test_resume_reruns_failed_steps(tmp_path):
    engine, executor, registry, approvals = _engine(tmp_path)
    attempts = {"count": 0}
    broken = {"flag": True}

    def flaky(_: dict[str, Any]) -> dict[str, Any]:
        attempts["count"] += 1
        if broken["flag"]:
            raise RuntimeError("still broken")
        return {"result_text": "recovered"}

    registry.register("t_flaky", "flaky", "low", {}, flaky)
    task = engine.create("retry task", [Action(tool="t_flaky")])
    first = engine.run(task.id)
    assert first is not None
    assert first.status == "failed"
    assert attempts["count"] == 2
    broken["flag"] = False
    resumed = engine.resume(task.id)
    assert resumed is not None
    assert resumed.status == "completed"
    assert attempts["count"] == 3


def test_timeout_cancels_remaining_steps(tmp_path):
    engine, executor, registry, approvals = _engine(tmp_path)

    def slow(_: dict[str, Any]) -> dict[str, Any]:
        time.sleep(0.4)
        return {"result_text": "done"}

    registry.register("t_slow", "slow", "low", {}, slow)
    task = engine.create("timeout task", [Action(tool="t_slow")] * 3)
    finished = engine.run(task.id, timeout_seconds=0.1)
    assert finished is not None
    assert finished.status in {"partial", "canceled"}
    assert "timed out" in (finished.error or "")
    assert finished.steps[1].status == "canceled"


def test_delete_removes_task(tmp_path):
    engine, executor, registry, approvals = _engine(tmp_path)
    task = engine.create("delete me", [])
    assert engine.delete(task.id) is True
    assert engine.get(task.id) is None
    assert engine.list() == []
    assert engine.delete(task.id) is False


def test_events_published(tmp_path):
    hub = EventHub()
    engine, executor, registry, approvals = _engine(tmp_path)
    engine.events = hub
    topics: list[str] = []
    unsub = hub.subscribe(lambda topic, payload: topics.append(topic))

    def handler(_: dict[str, Any]) -> dict[str, Any]:
        return {"result_text": "ok"}

    registry.register("t_echo", "echo", "low", {}, handler)
    task = engine.create("event task", [Action(tool="t_echo")])
    engine.run(task.id)
    unsub()
    assert "task_start" in topics
    assert "task_step" in topics
    assert "task_done" in topics


def test_task_tools_registered_and_callable(tmp_path):
    registry = ToolRegistry()
    settings = Settings(workspace_root=".", database_path=str(tmp_path / "tools.db"))
    approvals = ApprovalManager()
    executor = ActionExecutor(registry, approvals, settings)
    memory = MemoryStore(str(tmp_path / "mem.db"))
    engine = TaskEngine(executor, memory, settings)
    register_all_tools(registry, settings, memory=memory, task_engine=engine)
    names = {item["name"] for item in registry.catalog()}
    assert {"task_create", "task_run", "task_cancel", "task_resume", "task_list", "task_status"} <= names

    created = registry.execute("task_create", {"command": "do stuff", "steps": [{"tool": "get_current_time"}]})
    task_id = created["task_id"]
    assert registry.execute("task_status", {"task_id": task_id})["status"] == "pending"
    listed = registry.execute("task_list", {})
    assert listed["count"] == 1


def test_orchestrator_executes_plan_via_task_engine(tmp_path):
    from app.core.orchestrator import JarvisOrchestrator
    from app.core.router import DirectCommandRouter
    from app.models.schemas import Plan

    settings = Settings(workspace_root=".", database_path=str(tmp_path / "orch.db"))
    registry = ToolRegistry()
    approvals = ApprovalManager()
    executor = ActionExecutor(registry, approvals, settings)

    def handler(args: dict[str, Any]) -> dict[str, Any]:
        return {"result_text": "hello"}

    registry.register("orch_echo", "echo", "low", {}, handler)
    memory = MemoryStore(str(tmp_path / "orch_mem.db"))
    engine = TaskEngine(executor, memory, settings)

    class FakePlanner:
        def plan(self, command, tool_catalog, history, **kwargs):
            return Plan(summary="s", actions=[Action(tool="orch_echo", args={"x": 1})], final_response="done")

    orch = JarvisOrchestrator(
        settings=settings,
        registry=registry,
        router=DirectCommandRouter(),
        planner=FakePlanner(),
        executor=executor,
        approvals=approvals,
        memory=memory,
        task_engine=engine,
    )
    resp = orch.execute_command("say hello")
    assert resp.status == "completed"
    assert resp.results[0].tool == "orch_echo"
    tasks = orch.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["status"] == "completed"
