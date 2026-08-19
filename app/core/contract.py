from __future__ import annotations

from typing import Any

"""Unified tool-result contract helpers (Phase B).

Tools still return plain dicts; the executor wraps them into
``ToolExecutionResult``. These helpers give every consumer a single,
consistent way to interpret a tool payload and to render a human-readable
summary regardless of which keys a handler chose to return.
"""

RESULT_TEXT_KEYS: tuple[str, ...] = (
    "result_text",
    "message",
    "text",
    "summary",
    "extracted",
    "opened",
    "count",
    "path",
    "url",
    "target",
    "done",
    "status_text",
)

# Per-tool key preferences so a tool's most meaningful field wins.
_TOOL_TEXT_KEYS: dict[str, tuple[str, ...]] = {
    "open_application": ("target", "message"),
    "close_application": ("message", "closed_count"),
    "list_processes": ("count", "processes"),
    "list_windows": ("count", "windows"),
    "list_app_shortcuts": ("count", "items"),
    "list_known_apps": ("desktop_apps", "web_apps"),
    "system_status": ("cpu_percent", "memory_percent", "disk_percent"),
    "get_current_time": ("time_12h", "date"),
    "find_files": ("count", "matches"),
    "read_text_file": ("path", "chars"),
    "write_text_file": ("path", "bytes_written"),
    "move_file": ("source", "destination"),
    "delete_file": ("path",),
    "search_web": ("count", "results"),
    "read_webpage": ("title", "chars"),
    "open_youtube_video": ("opened", "query"),
    "control_youtube": ("action", "media_state"),
    "seek_youtube": ("timestamp", "url"),
    "open_whatsapp_chat": ("opened", "phone"),
    "browser_action": ("steps", "page"),
    "run_shell_command": ("return_code", "stdout"),
    "search_code": ("count", "matches"),
    "edit_file": ("replaced", "path"),
    "run_tests": ("passed", "result_text"),
    "lint_code": ("passed", "result_text"),
    "analyze_image": ("result_text", "analysis"),
    "analyze_screen": ("result_text", "analysis"),
    "ocr_screen": ("result_text", "chars"),
    "ocr_image": ("result_text", "chars"),
    "recall": ("count", "results"),
    "job_applied": ("count", "items"),
    "job_pipeline": ("totals", "count"),
    "job_status": ("result_text", "status"),
    "list_events": ("count", "events"),
    "upcoming_events": ("count", "events"),
    "list_schedules": ("count", "jobs"),
    "copy_to_clipboard": ("chars",),
    "read_clipboard": ("chars", "text"),
}


def synthesize_result_text(tool: str, output: dict[str, Any] | None) -> str:
    """Render a consistent, human-readable summary for a tool's output dict.

    Falls back gracefully for arbitrary dicts so the orchestrator, agent loop,
    and voice path never have to guess individual key names. An explicit
    ``result_text`` / ``message`` always wins (it is the human-ready summary).
    """
    payload = output or {}
    if not payload:
        return "ok"

    explicit = payload.get("result_text") or payload.get("message")
    if explicit:
        return str(explicit)

    keys = _TOOL_TEXT_KEYS.get(tool) or RESULT_TEXT_KEYS
    for key in keys:
        value = payload.get(key)
        if value is None or value == "":
            continue
        if key == "count":
            return f"{tool}: {value} item(s)"
        if isinstance(value, dict):
            rendered = synthesize_result_text(tool, value)
            if rendered != "ok":
                return rendered
            continue
        if isinstance(value, (list, tuple)):
            if value:
                return f"{tool}: {len(value)} item(s)"
            continue
        return str(value)

    for value in payload.values():
        if isinstance(value, (str, int, float)) and str(value) not in {"", "True", "False", "0"}:
            return f"{tool}: {value}"
    return f"{tool} completed"


def first_result_value(output: dict[str, Any] | None, keys: tuple[str, ...]) -> Any | None:
    """Return the first non-empty value among ``keys`` in a tool output."""
    for key in keys:
        value = (output or {}).get(key)
        if value is not None and value != "":
            return value
    return None
