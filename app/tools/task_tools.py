from __future__ import annotations

from typing import Any, cast

from app.config import Settings
from app.models.schemas import Action, RiskLevel
from app.tools.registry import ToolRegistry


def _task_summary(task: Any) -> str:
    progress = task.progress
    return (
        f"task {task.id} [{task.status}] {progress['completed']}/{progress['total']} steps"
        f" ({progress['percent']}%), current: {progress['current_step'] or 'none'}"
    )


def register_task_tools(registry: ToolRegistry, settings: Settings, engine: Any) -> None:
    def create(args: dict[str, Any]) -> dict[str, Any]:
        command = str(args.get("command") or "").strip()
        steps_data = args.get("steps") or []
        if not command or not steps_data:
            raise ValueError("task_create requires 'command' and a non-empty 'steps' list.")
        actions: list[Action] = []
        for item in steps_data:
            if not isinstance(item, dict) or not item.get("tool"):
                raise ValueError("Each step needs a 'tool' name.")
            actions.append(
                Action(
                    tool=str(item["tool"]),
                    args=item.get("args") or {},
                    risk=cast(RiskLevel, str(item.get("risk") or "low")),
                    reason=str(item.get("reason") or ""),
                )
            )
        task = engine.create(command, actions, source="tool")
        return {"task_id": task.id, "steps": len(task.steps), "result_text": f"Created {_task_summary(task)}"}

    def run(args: dict[str, Any]) -> dict[str, Any]:
        task_id = str(args.get("task_id") or "").strip()
        auto_approve = bool(args.get("auto_approve"))
        timeout = args.get("timeout_seconds")
        task = engine.run(task_id, auto_approve=auto_approve, timeout_seconds=timeout)
        if task is None:
            raise ValueError(f"task not found: {task_id}")
        return {"task_id": task.id, "status": task.status, "progress": task.progress, "result_text": _task_summary(task)}

    def cancel(args: dict[str, Any]) -> dict[str, Any]:
        task_id = str(args.get("task_id") or "").strip()
        if not engine.cancel(task_id):
            raise ValueError(f"task not found: {task_id}")
        return {"task_id": task_id, "result_text": f"Cancelled task {task_id}."}

    def resume(args: dict[str, Any]) -> dict[str, Any]:
        task_id = str(args.get("task_id") or "").strip()
        task = engine.resume(task_id, auto_approve=bool(args.get("auto_approve")))
        if task is None:
            raise ValueError(f"task not found: {task_id}")
        return {"task_id": task.id, "status": task.status, "progress": task.progress, "result_text": _task_summary(task)}

    def list_tasks(args: dict[str, Any]) -> dict[str, Any]:
        tasks = engine.list(limit=int(args.get("limit") or 20))
        items = [
            {
                "id": task.id,
                "command": task.command,
                "source": task.source,
                "status": task.status,
                "progress": task.progress,
                "created_at": task.created_at,
            }
            for task in tasks
        ]
        return {"count": len(items), "items": items, "result_text": f"{len(items)} task(s) tracked."}

    def status(args: dict[str, Any]) -> dict[str, Any]:
        task_id = str(args.get("task_id") or "").strip()
        task = engine.get(task_id)
        if task is None:
            raise ValueError(f"task not found: {task_id}")
        return {
            "task_id": task.id,
            "status": task.status,
            "command": task.command,
            "source": task.source,
            "progress": task.progress,
            "error": task.error,
            "steps": [
                {
                    "id": step.id,
                    "tool": step.tool,
                    "status": step.status,
                    "error": step.error,
                    "attempts": step.attempts,
                }
                for step in task.steps
            ],
            "result_text": _task_summary(task),
        }

    registry.register(
        "task_create", "Create a resumable multi-step task from a list of tool steps.", "low",
        {"command": "str", "steps": "list[dict] with tool/args/risk/reason"},
        create,
        category="tasks",
    )
    registry.register(
        "task_run", "Run a created task; executes pending steps in order.", "medium",
        {"task_id": "str", "auto_approve": "bool (optional)", "timeout_seconds": "float (optional)"},
        run,
        category="tasks",
        timeout_seconds=3600,
    )
    registry.register(
        "task_cancel", "Cancel a task; remaining steps are marked cancelled.", "medium",
        {"task_id": "str"},
        cancel,
        category="tasks",
    )
    registry.register(
        "task_resume", "Resume a failed/cancelled/pending task, re-running unfinished steps.", "medium",
        {"task_id": "str", "auto_approve": "bool (optional)"},
        resume,
        category="tasks",
        timeout_seconds=3600,
    )
    registry.register(
        "task_list", "List tracked tasks with their status and progress.", "low",
        {"limit": "int (optional)"},
        list_tasks,
        category="tasks",
    )
    registry.register(
        "task_status", "Inspect a task: steps, status, progress, and errors.", "low",
        {"task_id": "str"},
        status,
        category="tasks",
    )
