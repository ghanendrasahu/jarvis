from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.safe_paths import resolve_safe_path
from app.tools.registry import ToolRegistry

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", "data"}
_SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".exe", ".dll", ".db", ".sqlite"}
_MAX_FILE_BYTES = 512 * 1024
_MAX_LINE_LENGTH = 500


def _iter_text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield path


def register_code_tools(registry: ToolRegistry, settings: Settings) -> None:
    def search_code(args: dict[str, Any]) -> dict[str, Any]:
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("Missing search pattern.")
        root_value = str(args.get("root", settings.workspace_root))
        root = resolve_safe_path(root_value, settings=settings, must_exist=True)
        include = str(args.get("include", "")).strip() or None
        max_results = max(1, min(200, int(args.get("max_results", 50))))
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"Invalid regex: {exc}") from exc

        matches: list[dict[str, Any]] = []
        for path in _iter_text_files(root):
            if include and not path.match(include):
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as file_handle:
                    for line_number, line in enumerate(file_handle, start=1):
                        if regex.search(line.rstrip("\n")):
                            matches.append(
                                {
                                    "path": str(path),
                                    "line": line_number,
                                    "content": line.rstrip("\n")[:_MAX_LINE_LENGTH],
                                }
                            )
                            if len(matches) >= max_results:
                                return {"pattern": pattern, "count": len(matches), "matches": matches}
            except OSError:
                continue
        return {"pattern": pattern, "count": len(matches), "matches": matches}

    def edit_file(args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        old_string = str(args.get("old_string", ""))
        new_string = str(args.get("new_string", ""))
        replace_all = bool(args.get("replace_all", False))
        if not path:
            raise ValueError("Missing file path.")
        if not old_string:
            raise ValueError("old_string is required.")
        resolved = resolve_safe_path(path, settings=settings, must_exist=True)
        content = resolved.read_text(encoding="utf-8")
        occurrences = content.count(old_string)
        if occurrences == 0:
            raise ValueError(
                f"old_string not found in {resolved} ({resolved.stat().st_size} bytes). Use search_code or read_text_file first."
            )
        if occurrences > 1 and not replace_all:
            raise ValueError(f"old_string found {occurrences} times; set replace_all=true or make old_string more specific.")
        updated = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        resolved.write_text(updated, encoding="utf-8")
        return {
            "path": str(resolved),
            "replaced": occurrences if replace_all else 1,
            "bytes_changed": len(updated.encode("utf-8")) - len(content.encode("utf-8")),
            "result_text": f"Edited {resolved.name}.",
        }

    def run_tests(args: dict[str, Any]) -> dict[str, Any]:
        paths = str(args.get("paths", "")).strip()
        timeout_seconds = max(5, min(int(args.get("timeout_seconds", 120)), 300))
        command = [sys.executable, "-m", "pytest", "-q"]
        if paths:
            command.append(paths)
        started = time.perf_counter()
        completed = subprocess.run(
            command,
            cwd=settings.workspace_root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "command": " ".join(command),
            "return_code": completed.returncode,
            "passed": completed.returncode == 0,
            "stdout": completed.stdout[-3000:],
            "stderr": completed.stderr[-3000:],
            "duration_ms": duration_ms,
            "result_text": "Tests passed." if completed.returncode == 0 else "Tests failed.",
        }

    def lint_code(args: dict[str, Any]) -> dict[str, Any]:
        path_value = str(args.get("path", "")).strip() or "."
        resolved = resolve_safe_path(path_value, settings=settings, must_exist=True)
        timeout_seconds = max(5, min(int(args.get("timeout_seconds", 60)), 180))
        started = time.perf_counter()
        completed = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(resolved)],
            cwd=settings.workspace_root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "path": str(resolved),
            "return_code": completed.returncode,
            "passed": completed.returncode == 0,
            "stdout": completed.stdout[-3000:],
            "stderr": completed.stderr[-3000:],
            "duration_ms": duration_ms,
            "result_text": "Lint passed." if completed.returncode == 0 else "Lint found issues.",
        }

    registry.register(
        name="search_code",
        description="Regex-search source files (skips binaries and vendor dirs) and return matching lines.",
        risk="low",
        parameters={"pattern": "string", "root": "string", "include": "string", "max_results": "integer"},
        handler=search_code,
    )
    registry.register(
        name="edit_file",
        description="Make a surgical text replacement in a file (exact old_string match, error if ambiguous).",
        risk="medium",
        parameters={"path": "string", "old_string": "string", "new_string": "string", "replace_all": "boolean"},
        handler=edit_file,
    )
    registry.register(
        name="run_tests",
        description="Run the project test suite (pytest) and report pass/fail with output tail.",
        risk="medium",
        parameters={"paths": "string", "timeout_seconds": "integer"},
        handler=run_tests,
    )
    registry.register(
        name="lint_code",
        description="Run ruff lint on a path and report issues with output tail.",
        risk="medium",
        parameters={"path": "string", "timeout_seconds": "integer"},
        handler=lint_code,
    )
