from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from app.config import Settings
from app.core.approvals import ApprovalManager
from app.core.executor import ActionExecutor
from app.core.memory import MemoryStore
from app.core.orchestrator import JarvisOrchestrator
from app.core.router import DirectCommandRouter
from app.models.schemas import Plan
from app.tools.registry import ToolRegistry


class FakePlanner:
    def __init__(self) -> None:
        self.calls = 0
        self.last_command: str | None = None

    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
        tool_history: list[dict] | None = None,
        known_facts: dict | None = None,
    ) -> Plan:
        self.calls += 1
        self.last_command = command
        return Plan(summary="summary", actions=[], final_response="ok")


class EmptyPlanner(FakePlanner):
    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
        tool_history: list[dict] | None = None,
        known_facts: dict | None = None,
    ) -> Plan:
        self.calls += 1
        return Plan()


def _make_orchestrator(tmp_path, planner: FakePlanner | None = None, **settings_kwargs: Any):
    defaults: dict[str, Any] = {"llm_provider": "openai"}
    defaults.update(settings_kwargs)
    settings = Settings(workspace_root=".", **defaults)
    memory = MemoryStore(str(tmp_path / "m.db"))
    fake = planner or FakePlanner()
    orchestrator = JarvisOrchestrator(
        settings=settings,
        registry=ToolRegistry(),
        router=DirectCommandRouter(),
        planner=fake,
        executor=cast(ActionExecutor, object()),
        approvals=cast(ApprovalManager, object()),
        memory=memory,
    )
    return orchestrator, fake


def test_cache_hit_skips_planner(tmp_path):
    orch, fake = _make_orchestrator(tmp_path)
    plan1 = orch._plan_or_cached("play lofi on youtube", {})
    plan2 = orch._plan_or_cached("play lofi on youtube", {})
    assert fake.calls == 1
    assert plan1 == plan2


def test_cache_misses_on_different_command(tmp_path):
    orch, fake = _make_orchestrator(tmp_path)
    orch._plan_or_cached("play lofi on youtube", {})
    orch._plan_or_cached("play phonk on youtube", {})
    assert fake.calls == 2


def test_cache_key_includes_active_surface(tmp_path):
    orch, fake = _make_orchestrator(tmp_path)
    orch._plan_or_cached("next", {"active_surface": "youtube"})
    orch._plan_or_cached("next", {"active_surface": "whatsapp"})
    assert fake.calls == 2


def test_cache_disabled_skips_lookup(tmp_path):
    orch, fake = _make_orchestrator(tmp_path, llm_cache_enabled=False)
    orch._plan_or_cached("play lofi on youtube", {})
    orch._plan_or_cached("play lofi on youtube", {})
    assert fake.calls == 2


def test_cache_key_changes_with_model(tmp_path):
    orch_a, fake_a = _make_orchestrator(tmp_path, openai_model="model-a")
    orch_b, fake_b = _make_orchestrator(tmp_path, openai_model="model-b")
    orch_a._plan_or_cached("hello there", {})
    orch_b._plan_or_cached("hello there", {})
    assert fake_a.calls == 1
    assert fake_b.calls == 1


def test_cache_ttl_eviction(tmp_path):
    orch, fake = _make_orchestrator(tmp_path, llm_cache_ttl_days=7)
    orch._plan_or_cached("hello there", {})
    assert fake.calls == 1
    rows = orch.memory._conn.execute("SELECT key FROM planner_cache").fetchall()
    assert rows
    stale = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    for row in rows:
        orch.memory._conn.execute("UPDATE planner_cache SET updated_at = ? WHERE key = ?", (stale, row["key"]))
    orch.memory._conn.commit()
    orch._plan_or_cached("hello there", {})
    assert fake.calls == 2


def test_cache_max_entries_caps_table(tmp_path):
    orch, _ = _make_orchestrator(tmp_path, llm_cache_max_entries=2)
    for i in range(5):
        orch._plan_or_cached(f"command number {i}", {})
    with orch.memory._lock:
        count = orch.memory._conn.execute("SELECT COUNT(*) AS c FROM planner_cache").fetchone()["c"]
    assert count == 2


def test_empty_plan_not_cached(tmp_path):
    orch, fake = _make_orchestrator(tmp_path, planner=EmptyPlanner())
    orch._plan_or_cached("weird", {})
    orch._plan_or_cached("weird", {})
    assert fake.calls == 2
