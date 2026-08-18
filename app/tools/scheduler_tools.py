from __future__ import annotations

from typing import Any

from app.core.scheduler import Scheduler
from app.tools.registry import ToolRegistry


def register_scheduler_tools(registry: ToolRegistry, scheduler: Scheduler) -> None:
    def schedule(args: dict[str, Any]) -> dict[str, Any]:
        command = str(args.get("command", "")).strip()
        recurrence = str(args.get("recurrence", "")).strip()
        job = scheduler.add(command, recurrence)
        return {"result_text": f"Scheduled '{command}' ({recurrence}). id={job['id']}", "job": job}

    def list_schedules(_: dict[str, Any]) -> dict[str, Any]:
        jobs = scheduler.list()
        return {"count": len(jobs), "jobs": jobs, "result_text": f"{len(jobs)} schedule(s)."}

    def cancel_schedule(args: dict[str, Any]) -> dict[str, Any]:
        job_id = str(args.get("id", "")).strip()
        if not scheduler.remove(job_id):
            raise ValueError(f"No schedule with id '{job_id}'.")
        return {"result_text": f"Canceled schedule {job_id}."}

    registry.register(
        name="schedule",
        description=(
            "Schedule a command to run automatically. Recurrence accepts 'daily at HH:MM', 'hourly', "
            "'every N minutes', or 'weekly on <day> at HH:MM'."
        ),
        risk="medium",
        parameters={"command": "string", "recurrence": "string"},
        handler=schedule,
    )
    registry.register(
        name="list_schedules",
        description="List all scheduled recurring commands and their last run status.",
        risk="low",
        parameters={},
        handler=list_schedules,
    )
    registry.register(
        name="cancel_schedule",
        description="Cancel a scheduled recurring command by its id.",
        risk="medium",
        parameters={"id": "string"},
        handler=cancel_schedule,
    )
