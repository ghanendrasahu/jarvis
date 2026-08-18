from __future__ import annotations

from typing import Any

from app.agents.job_agent import get_job_agent
from app.config import Settings
from app.core.memory import MemoryStore
from app.tools.registry import ToolRegistry


def _status_text(task: dict[str, Any]) -> str:
    status = task.get("status")
    if status == "idle":
        return "No job-application task is running."
    if status == "canceled":
        return "Job-application task canceled."
    phase = task.get("phase") or "starting"
    applied = task.get("applied", 0)
    reason = task.get("paused_reason")
    text = f"Job task {status} on {phase}. {applied} applied, {task.get('skipped', 0)} skipped, {task.get('failed', 0)} failed."
    if reason:
        text += f" Paused: {reason}."
    return text


def register_job_tools(registry: ToolRegistry, settings: Settings, memory: MemoryStore) -> None:
    agent = get_job_agent(settings, memory)

    def job_apply(args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("Missing search query.")
        sites = args.get("sites") or None
        max_applications = args.get("max_applications")
        task = agent.start_apply_run(query=query, sites=sites, max_applications=max_applications)
        return {
            "result_text": f"Started applying for '{query}' across {len(task.get('sites') or [])} job sites.",
            "task": task,
        }

    def job_status(_: dict[str, Any]) -> dict[str, Any]:
        task = agent.status()
        return {"result_text": _status_text(task), "task": task}

    def job_cancel(_: dict[str, Any]) -> dict[str, Any]:
        task = agent.cancel()
        return {"result_text": _status_text(task), "task": task}

    def job_applied(args: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(200, int(args.get("limit", 50))))
        items = agent.applied(limit=limit)
        return {"count": len(items), "items": items, "totals": memory.job_applications_summary()}

    registry.register(
        name="job_apply",
        description="Start an autonomous job-application run for a search query across configured job boards.",
        risk="high",
        parameters={"query": "string", "sites": "list[string]", "max_applications": "integer"},
        handler=job_apply,
    )
    registry.register(
        name="job_status",
        description="Return the current autonomous job-application task status and progress.",
        risk="low",
        parameters={},
        handler=job_status,
    )
    registry.register(
        name="job_cancel",
        description="Cancel the running autonomous job-application task.",
        risk="medium",
        parameters={},
        handler=job_cancel,
    )
    registry.register(
        name="job_applied",
        description="List jobs the agent has already applied to (dedupe / audit trail).",
        risk="low",
        parameters={"limit": "integer"},
        handler=job_applied,
    )
