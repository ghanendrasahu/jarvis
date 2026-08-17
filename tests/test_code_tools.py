from __future__ import annotations

import pytest

from app.config import Settings
from app.tools.code_tools import register_code_tools
from app.tools.registry import ToolRegistry


def _setup(tmp_path) -> tuple[Settings, ToolRegistry, dict]:
    project = tmp_path / "proj"
    project.mkdir()
    settings = Settings(workspace_root=str(project))
    registry = ToolRegistry()
    register_code_tools(registry, settings)
    return settings, registry, {}


def test_edit_file_replaces_once(tmp_path):
    _, registry, _ = _setup(tmp_path)
    path = str(tmp_path / "proj" / "demo.py")
    open(path, "w", encoding="utf-8").write("aaa bbb\n")
    out = registry.execute("edit_file", {"path": path, "old_string": "aaa", "new_string": "ccc"})
    assert out["replaced"] == 1
    assert open(path, encoding="utf-8").read() == "ccc bbb\n"


def test_edit_file_not_found(tmp_path):
    _, registry, _ = _setup(tmp_path)
    path = str(tmp_path / "proj" / "demo.py")
    open(path, "w", encoding="utf-8").write("hello\n")
    with pytest.raises(ValueError, match="not found"):
        registry.execute("edit_file", {"path": path, "old_string": "zzz", "new_string": "x"})


def test_edit_file_ambiguous_requires_replace_all(tmp_path):
    _, registry, _ = _setup(tmp_path)
    path = str(tmp_path / "proj" / "demo.py")
    open(path, "w", encoding="utf-8").write("a a\n")
    with pytest.raises(ValueError, match="found 2 times"):
        registry.execute("edit_file", {"path": path, "old_string": "a", "new_string": "b"})
    out = registry.execute("edit_file", {"path": path, "old_string": "a", "new_string": "b", "replace_all": True})
    assert out["replaced"] == 2
    assert open(path, encoding="utf-8").read() == "b b\n"


def test_search_code_finds_matches_and_skips_vendor(tmp_path):
    _, registry, _ = _setup(tmp_path)
    src = tmp_path / "proj" / "app.py"
    src.write_text("def hello():\n    return 42\n", encoding="utf-8")
    vendor = tmp_path / "proj" / ".venv"
    vendor.mkdir()
    (vendor / "lib.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    out = registry.execute("search_code", {"pattern": "def hello"})
    assert out["count"] == 1
    assert out["matches"][0]["line"] == 1
    assert "app.py" in out["matches"][0]["path"]


def test_run_tests_reports_pass_and_fail(tmp_path):
    settings, registry, _ = _setup(tmp_path)
    tests = tmp_path / "proj" / "test_demo.py"
    tests.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    out = registry.execute("run_tests", {})
    assert out["passed"] is True

    tests.write_text("def test_bad():\n    assert False\n", encoding="utf-8")
    out = registry.execute("run_tests", {})
    assert out["passed"] is False
    assert out["return_code"] != 0


def test_lint_code_reports_issues(tmp_path):
    settings, registry, _ = _setup(tmp_path)
    clean = tmp_path / "proj" / "clean.py"
    clean.write_text("def foo() -> int:\n    return 1\n", encoding="utf-8")
    out = registry.execute("lint_code", {"path": "clean.py"})
    assert out["passed"] is True

    dirty = tmp_path / "proj" / "dirty.py"
    dirty.write_text("import os\n", encoding="utf-8")
    out = registry.execute("lint_code", {"path": "dirty.py"})
    assert out["passed"] is False
