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
        # Return task info so the user can check status/resume
        return {
            "result_text": f"Started applying for '{query}' across {len(task.get('sites') or [])} job sites.",
            "task_id": task.get("task_id"),
            "status": task.get("status"),
            "phase": task.get("phase"),
        }

    def job_status(_: dict[str, Any]) -> dict[str, Any]:
        task = agent.status()
        return {"result_text": _status_text(task), "task": task}

    def job_cancel(_: dict[str, Any]) -> dict[str, Any]:
        task = agent.cancel()
        return {"result_text": _status_text(task), "task": task}

    def job_resume(_: dict[str, Any]) -> dict[str, Any]:
        task = agent.resume()
        return {"result_text": _status_text(task), "task": task}

    def job_applied(args: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(200, int(args.get("limit", 50))))
        items = agent.applied(limit=limit)
        return {"count": len(items), "items": items, "totals": memory.job_applications_summary()}

    def job_update_status(args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url", "")).strip()
        status = str(args.get("status", "")).strip().lower()
        if not url:
            raise ValueError("Missing job URL.")
        allowed = {"applied", "interview", "offer", "rejected", "withdrawn"}
        if status not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}.")
        notes = str(args.get("notes", "")).strip() or None
        interview_stage = str(args.get("interview_stage", "")).strip() or None
        next_action = str(args.get("next_action", "")).strip() or None
        deadline = str(args.get("deadline", "")).strip() or None
        if not memory.update_job_status(url, status, notes, interview_stage, next_action, deadline):
            raise ValueError("No applied job found with that URL.")
        result = {"result_text": f"Marked job as {status}.", "url": url, "status": status}
        if interview_stage is not None:
            result["interview_stage"] = interview_stage
        if next_action is not None:
            result["next_action"] = next_action
        if deadline is not None:
            result["deadline"] = deadline
        return result

    def job_pipeline(_: dict[str, Any]) -> dict[str, Any]:
        totals = memory.job_applications_summary()
        items = memory.recent_job_applications(limit=100)
        # Group items by status for a quick pipeline view
        grouped: dict[str, int] = {}
        for item in items:
            s = item.get("status", "unknown")
            grouped[s] = grouped.get(s, 0) + 1
        return {"totals": totals, "count": len(items), "items": items, "grouped": grouped}

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
        name="job_resume",
        description="Resume a paused job-application task after the user signs in or solves a CAPTCHA.",
        risk="medium",
        parameters={},
        handler=job_resume,
    )
    registry.register(
        name="job_update_status",
        description="Update the pipeline status (applied, interview, offer, rejected, withdrawn) of an applied job by URL.",
        risk="medium",
        parameters={"url": "string", "status": "string", "notes": "string"},
        handler=job_update_status,
    )
    registry.register(
        name="job_pipeline",
        description="Return the job pipeline: grouped counts by status and the latest applied jobs.",
        risk="low",
        parameters={},
        handler=job_pipeline,
    )
    registry.register(
        name="job_applied",
        description="List jobs the agent has already applied to (dedupe / audit trail).",
        risk="low",
        parameters={"limit": "integer"},
        handler=job_applied,
    )
