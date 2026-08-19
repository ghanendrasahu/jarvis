from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.core.approvals import ApprovalManager
from app.core.contract import synthesize_result_text
from app.core.executor import ActionExecutor
from app.models.schemas import Action
from app.tools import register_all_tools
from app.tools.meta import TOOL_META, apply_tool_meta, tool_meta_summary
from app.tools.registry import ToolRegistry


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    settings = Settings(workspace_root=".", database_path="data/test_contract.db")
    from app.core.calendar import CalendarStore
    from app.core.memory import MemoryStore
    from app.core.rag import RagIndex
    from app.core.scheduler import Scheduler

    memory = MemoryStore("data/test_contract.db")
    scheduler = Scheduler(settings=settings, memory=memory, orchestrator=None)
    calendar = CalendarStore("data/test_contract_calendar.db")
    rag = RagIndex(settings)
    register_all_tools(registry, settings, memory=memory, rag=rag, scheduler=scheduler, calendar=calendar)
    return registry


def test_legacy_registration_gets_safe_defaults():
    registry = ToolRegistry()

    def handler(_: dict) -> dict:
        return {"ok": True}

    registry.register("legacy_tool", "a legacy tool", "low", {}, handler)
    spec = registry.get("legacy_tool")
    assert spec is not None
    assert spec.category == "general"
    assert spec.read_write == "execute"
    assert spec.external_action is False
    assert spec.timeout_seconds is None
    assert spec.cancellation == "abort"
    assert spec.error_behavior == "raise"


def test_duplicate_registration_raises():
    registry = ToolRegistry()

    def handler(_: dict) -> dict:
        return {"ok": True}

    registry.register("dup", "first", "low", {}, handler)
    with pytest.raises(ValueError, match="Duplicate tool registration"):
        registry.register("dup", "second", "low", {}, handler)


def test_invalid_risk_rejected():
    registry = ToolRegistry()

    def handler(_: dict) -> dict:
        return {"ok": True}

    with pytest.raises(ValueError, match="Invalid risk"):
        registry.register("bad_risk", "x", "extreme", {}, handler)


def test_tool_meta_enriches_every_registered_tool():
    registry = _registry()
    registered = {item["name"] for item in registry.catalog()}
    applied = apply_tool_meta(registry)
    assert applied == len(registered)
    assert len(registered) >= 65
    for item in registry.catalog():
        assert item["category"] in {
            "app_launch", "desktop", "system", "files", "browser", "code",
            "knowledge", "vision", "jobs", "productivity", "scheduler", "agent",
            "metrics", "proactive", "general",
        }
        assert item["read_write"] in {"read", "write", "execute"}


def test_meta_covers_registered_tools():
    registry = _registry()
    registered = {item["name"] for item in registry.catalog()}
    missing = sorted(registered - set(TOOL_META))
    assert missing == [], f"tools missing from TOOL_META: {missing}"


def test_catalog_exposes_contract_metadata():
    registry = _registry()
    item = next(i for i in registry.catalog() if i["name"] == "system_status")
    assert item["category"] == "desktop"
    assert item["read_write"] == "read"
    assert item["external_action"] is False


def test_synthesize_result_text():
    assert synthesize_result_text("read_text_file", {"path": "/x", "content": "hi"}) == "/x"
    assert synthesize_result_text("read_text_file", {"result_text": "ok", "path": "/x"}) == "ok"
    assert synthesize_result_text("find_files", {"count": 3, "matches": ["a", "b", "c"]}) == "find_files: 3 item(s)"
    assert synthesize_result_text("list_processes", {"count": 5}) == "list_processes: 5 item(s)"
    assert synthesize_result_text("unknown_tool", {"alpha": "beta"}) == "unknown_tool: beta"
    assert synthesize_result_text("unknown_tool", {}) == "ok"
    assert synthesize_result_text("open_application", {"target": "chrome"}) == "chrome"


def test_executor_pending_includes_contract_fields():
    registry = ToolRegistry()

    def handler(_: dict) -> dict:
        return {"ok": True}

    registry.register("sensitive_op", "risky op", "medium", {}, handler)
    approvals = ApprovalManager()
    executor = ActionExecutor(registry, approvals, Settings(workspace_root=".", database_path="data/x.db"))
    result = executor.execute("do it", Action(tool="sensitive_op", risk="medium", reason="because"))
    assert result.status == "pending"
    assert result.requires_approval is True
    assert result.approval_reason is not None
    assert result.category == "general"
    assert result.pending_approval_id is not None
    assert result.metadata["requires_approval"] is True
    assert result.success is False


def test_executor_completed_includes_contract_fields():
    registry = ToolRegistry()

    def handler(_: dict) -> dict:
        return {"result_text": "done", "count": 2}

    registry.register("quick_read", "reads", "low", {}, handler)
    executor = ActionExecutor(registry, ApprovalManager(), Settings(workspace_root=".", database_path="data/x.db"))
    result = executor.execute("read", Action(tool="quick_read", risk="low"))
    assert result.status == "completed"
    assert result.success is True
    assert result.category == "general"
    assert result.metadata["attempt"] == 1
    assert result.metadata["duration_ms"] >= 0
    assert result.requires_approval is False


def test_executor_timeout_enforcement():
    registry = ToolRegistry()

    def slow_handler(_: dict) -> dict:
        time.sleep(5)
        return {"result_text": "late"}

    registry.register(
        "slow_tool", "slow", "low", {}, slow_handler, category="desktop", timeout_seconds=0.05
    )
    executor = ActionExecutor(registry, ApprovalManager(), Settings(workspace_root=".", database_path="data/x.db"))
    result = executor.execute("wait", Action(tool="slow_tool", risk="low"))
    assert result.status == "failed"
    assert "timed out" in (result.error or "")
    assert result.metadata.get("timed_out") is True
    assert result.success is False


def test_meta_summary_shape():
    registry = _registry()
    apply_tool_meta(registry)
    summary = tool_meta_summary(registry)
    assert summary
    assert all({"category", "count"} <= set(item) for item in summary)
    assert sum(item["count"] for item in summary) == len(registry.catalog())
