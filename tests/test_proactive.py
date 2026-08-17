from __future__ import annotations

from app.config import Settings
from app.core.idle import IdleDetector
from app.core.memory import MemoryStore
from app.core.suggest import SuggestionEngine
from app.tools.proactive_tools import register_proactive_tools
from app.tools.registry import ToolRegistry


def test_suggestion_engine_rules():
    engine = SuggestionEngine()
    assert engine.suggest("fix the bug in scheduler.py")[0] == "run_tests via 'run the test suite' to confirm green"
    assert "analyze the screen" in engine.suggest("take a screenshot")


def test_suggestion_engine_dedupe_and_limit():
    engine = SuggestionEngine()
    items = engine.suggest("system status", limit=3)
    assert len(items) <= 3
    assert len(items) == len(set(items))


def test_idle_detector_no_crash():
    detector = IdleDetector(memory=None)
    assert detector.idle_seconds() >= 0
    assert detector.is_idle(0) is True


def test_idle_detector_fallback_from_memory(tmp_path):
    memory = MemoryStore(str(tmp_path / "m.db"))
    detector = IdleDetector(memory=memory)
    assert detector.idle_seconds() == 0
    detector.is_idle(300)


def _registry(tmp_path, memory):
    settings = Settings(workspace_root=str(tmp_path), data_dir=str(tmp_path / "data"))
    registry = ToolRegistry()
    register_proactive_tools(registry, settings, memory)
    return registry


def test_proactive_tools_register(tmp_path):
    memory = MemoryStore(str(tmp_path / "m.db"))
    registry = _registry(tmp_path, memory)
    for name in ("suggest_next", "idle_time", "idle_status"):
        assert registry.get(name) is not None


def test_suggest_next_uses_last_command(tmp_path):
    memory = MemoryStore(str(tmp_path / "m.db"))
    memory.set_state("activity", {"last_command": "fix the bug"})
    registry = _registry(tmp_path, memory)
    out = registry.execute("suggest_next", {})
    assert out["count"] > 0
    assert "run_tests" in out["result_text"]


def test_idle_time_tool(tmp_path):
    memory = MemoryStore(str(tmp_path / "m.db"))
    registry = _registry(tmp_path, memory)
    out = registry.execute("idle_time", {})
    assert out["idle_seconds"] >= 0
    out = registry.execute("idle_status", {"threshold_seconds": 0})
    assert out["idle"] is True
