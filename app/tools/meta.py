from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.tools.registry import ReadWriteKind, ToolRegistry

"""Central contract metadata for every registered tool (Phase B).

Kept separate from the registration call sites so the 65 handlers stay
unchanged. ``apply_tool_meta`` enriches registered specs in place; unknown
tools keep their safe defaults. Add a row here whenever a new tool is
registered.
"""


@dataclass(frozen=True)
class ToolMeta:
    category: str
    read_write: ReadWriteKind
    external_action: bool = False
    timeout_seconds: float | None = None
    cancellation: str = "abort"
    error_behavior: str = "raise"


TOOL_META: dict[str, ToolMeta] = {
    # ---- system / desktop ---------------------------------------------------
    "open_application": ToolMeta("app_launch", "execute", external_action=True, timeout_seconds=30),
    "close_application": ToolMeta("app_launch", "execute", external_action=True, timeout_seconds=30),
    "open_url": ToolMeta("app_launch", "execute", external_action=True, timeout_seconds=20),
    "list_app_shortcuts": ToolMeta("desktop", "read"),
    "list_known_apps": ToolMeta("desktop", "read"),
    "list_processes": ToolMeta("desktop", "read"),
    "system_status": ToolMeta("desktop", "read"),
    "get_current_time": ToolMeta("desktop", "read"),
    "list_windows": ToolMeta("desktop", "read"),
    "focus_window": ToolMeta("desktop", "execute", external_action=True, timeout_seconds=10),
    "capture_screenshot": ToolMeta("desktop", "write", timeout_seconds=20),
    "type_text": ToolMeta("desktop", "execute", external_action=True, timeout_seconds=15),
    "press_hotkey": ToolMeta("desktop", "execute", external_action=True, timeout_seconds=15),
    "click_mouse": ToolMeta("desktop", "execute", external_action=True, timeout_seconds=15),
    "run_shell_command": ToolMeta("system", "execute", external_action=True, timeout_seconds=30, cancellation="abort"),
    # ---- files ----------------------------------------------------------------
    "find_files": ToolMeta("files", "read", timeout_seconds=30),
    "read_text_file": ToolMeta("files", "read", timeout_seconds=10),
    "write_text_file": ToolMeta("files", "write", timeout_seconds=10),
    "move_file": ToolMeta("files", "write", timeout_seconds=15),
    "delete_file": ToolMeta("files", "write", external_action=True, timeout_seconds=15),
    # ---- browser --------------------------------------------------------------
    "search_web": ToolMeta("browser", "read", external_action=True, timeout_seconds=30),
    "read_webpage": ToolMeta("browser", "read", external_action=True, timeout_seconds=30),
    "open_youtube_video": ToolMeta("browser", "execute", external_action=True, timeout_seconds=30),
    "control_youtube": ToolMeta("browser", "execute", external_action=True, timeout_seconds=20),
    "seek_youtube": ToolMeta("browser", "execute", external_action=True, timeout_seconds=20),
    "open_whatsapp_chat": ToolMeta("browser", "execute", external_action=True, timeout_seconds=30),
    "browser_action": ToolMeta("browser", "execute", external_action=True, timeout_seconds=30),
    # ---- code -----------------------------------------------------------------
    "search_code": ToolMeta("code", "read", timeout_seconds=30),
    "edit_file": ToolMeta("code", "write", timeout_seconds=15),
    "run_tests": ToolMeta("code", "execute", timeout_seconds=240, cancellation="abort"),
    "lint_code": ToolMeta("code", "execute", timeout_seconds=120, cancellation="abort"),
    # ---- knowledge / RAG --------------------------------------------------------
    "remember_note": ToolMeta("knowledge", "write"),
    "recall": ToolMeta("knowledge", "read"),
    "index_file": ToolMeta("knowledge", "write", timeout_seconds=120),
    "index_notes": ToolMeta("knowledge", "write", timeout_seconds=120),
    "knowledge_stats": ToolMeta("knowledge", "read"),
    # ---- vision ----------------------------------------------------------------
    "analyze_image": ToolMeta("vision", "read", external_action=True, timeout_seconds=90),
    "analyze_screen": ToolMeta("vision", "read", external_action=True, timeout_seconds=90),
    "ocr_screen": ToolMeta("vision", "read", external_action=True, timeout_seconds=60),
    "ocr_image": ToolMeta("vision", "read", external_action=True, timeout_seconds=60),
    # ---- jobs -------------------------------------------------------------------
    "job_apply": ToolMeta("jobs", "execute", external_action=True, timeout_seconds=3600, cancellation="abort"),
    "job_status": ToolMeta("jobs", "read"),
    "job_cancel": ToolMeta("jobs", "write", external_action=True),
    "job_resume": ToolMeta("jobs", "execute", external_action=True),
    "job_update_status": ToolMeta("jobs", "write"),
    "job_pipeline": ToolMeta("jobs", "read"),
    "job_applied": ToolMeta("jobs", "read"),
    # ---- productivity --------------------------------------------------------------
    "send_email": ToolMeta("productivity", "execute", external_action=True, timeout_seconds=60),
    "announce": ToolMeta("productivity", "execute", external_action=True, timeout_seconds=20),
    "set_reminder": ToolMeta("productivity", "write"),
    "add_event": ToolMeta("productivity", "write"),
    "list_events": ToolMeta("productivity", "read"),
    "upcoming_events": ToolMeta("productivity", "read"),
    "delete_event": ToolMeta("productivity", "write"),
    "copy_to_clipboard": ToolMeta("productivity", "write"),
    "read_clipboard": ToolMeta("productivity", "read"),
    # ---- scheduler ----------------------------------------------------------------
    "schedule": ToolMeta("scheduler", "write"),
    "list_schedules": ToolMeta("scheduler", "read"),
    "cancel_schedule": ToolMeta("scheduler", "write"),
    # ---- tasks ----------------------------------------------------------------------
    "task_create": ToolMeta("tasks", "write"),
    "task_run": ToolMeta("tasks", "execute", timeout_seconds=3600),
    "task_cancel": ToolMeta("tasks", "write"),
    "task_resume": ToolMeta("tasks", "execute", timeout_seconds=3600),
    "task_list": ToolMeta("tasks", "read"),
    "task_status": ToolMeta("tasks", "read"),
    # ---- memory -------------------------------------------------------------------------------
    "memory_recall": ToolMeta("memory", "read"),
    "memory_list": ToolMeta("memory", "read"),
    "memory_forget": ToolMeta("memory", "write"),
    "memory_clear": ToolMeta("memory", "write"),
    # ---- agent / metrics / proactive -----------------------------------------------
    "agent_done": ToolMeta("agent", "write"),
    "get_runtime_metrics": ToolMeta("metrics", "read"),
    "get_runtime_context": ToolMeta("metrics", "read"),
    "suggest_next": ToolMeta("proactive", "read"),
    "idle_time": ToolMeta("proactive", "read"),
    "idle_status": ToolMeta("proactive", "read"),
}


def apply_tool_meta(registry: ToolRegistry) -> int:
    """Enrich registered tools with their contract metadata. Returns count applied."""
    applied = 0
    for name, meta in TOOL_META.items():
        if registry.set_metadata(
            name,
            category=meta.category,
            read_write=meta.read_write,
            external_action=meta.external_action,
            timeout_seconds=meta.timeout_seconds,
            cancellation=meta.cancellation,
            error_behavior=meta.error_behavior,
        ):
            applied += 1
    return applied


def tool_meta_summary(registry: ToolRegistry) -> list[dict[str, Any]]:
    """Compact per-category inventory for HELP panels / debugging."""
    by_category: dict[str, int] = {}
    for item in registry.catalog():
        category = str(item.get("category") or "general")
        by_category[category] = by_category.get(category, 0) + 1
    return [
        {"category": category, "count": count}
        for category, count in sorted(by_category.items(), key=lambda kv: kv[0])
    ]
