from __future__ import annotations

import os
from datetime import timedelta

import pytest

from app.config import get_settings
from app.core.scheduler import local_now

# Env keys we override to build a hermetic runtime via build_runtime().
_ENV_KEYS = [
    "DATABASE_PATH",
    "DATA_DIR",
    "LOGS_DIR",
    "RAG_INDEX_PATH",
    "RAG_NOTES_DIR",
    "CALENDAR_PATH",
    "VOICE_ENABLED",
    "TTS_ENABLED",
    "RAG_EMBEDDINGS_ENABLED",
    "WORKSPACE_ROOT",
]


@pytest.fixture
def runtime(tmp_path):
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    for key in saved:
        os.environ.pop(key, None)
    os.environ.update(
        {
            "WORKSPACE_ROOT": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "e2e.db"),
            "DATA_DIR": str(tmp_path / "data"),
            "LOGS_DIR": str(tmp_path / "logs"),
            "RAG_INDEX_PATH": str(tmp_path / "rag.db"),
            "RAG_NOTES_DIR": str(tmp_path / "notes"),
            "CALENDAR_PATH": str(tmp_path / "calendar.db"),
            "VOICE_ENABLED": "false",
            "TTS_ENABLED": "false",
            "RAG_EMBEDDINGS_ENABLED": "false",
        }
    )
    get_settings.cache_clear()
    from app.agents import job_agent as _job_agent_mod

    # get_job_agent is a process-wide singleton; reset it so this runtime's
    # job tools bind to this test's MemoryStore (previous tests bound it to theirs).
    _job_agent_mod._job_agent_singleton = None

    from app.bootstrap import build_runtime

    rt = build_runtime()
    try:
        yield rt
    finally:
        rt.voice.stop()
        rt.monitor.stop()
        rt.scheduler.stop()
        _job_agent_mod._job_agent_singleton = None
        get_settings.cache_clear()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _list_applications(rt):
    response = rt.orchestrator.execute_command("list applications", source="cli", auto_approve=True)
    assert response.status == "completed"
    return response


def test_e2e_jobhunter_scenario(runtime):
    """Deterministic end-to-end 'JobHunter' flow through the real composition root."""
    rt = runtime

    # 1. Empty pipeline.
    response = _list_applications(rt)
    assert response.results and response.results[0].output["count"] == 0

    # 2. Seed a job application (as the browser job agent would on success).
    rt.memory.log_job_application(
        site="indeed",
        title="Senior Python Engineer",
        company="Acme Robotics",
        location="Remote",
        url="https://www.indeed.com/viewjob?id=acme-42",
        status="applied",
        notes="fields=8 resume=uploaded submit=yes",
    )
    assert rt.memory.is_job_applied("https://www.indeed.com/viewjob?id=acme-42")

    # 3. Router-routed command surfaces it.
    response = _list_applications(rt)
    items = response.results[0].output["items"]
    assert len(items) == 1
    assert items[0]["title"] == "Senior Python Engineer"

    # 4. Advance the pipeline via the application-tracking tool.
    updated = rt.registry.execute(
        "job_update_status",
        {
            "url": "https://www.indeed.com/viewjob?id=acme-42",
            "status": "interview",
            "notes": "recruiter reached out",
            "interview_stage": "2nd round",
            "next_action": "send availability",
            "deadline": "2026-09-01",
        },
    )
    assert updated["status"] == "interview"

    # 5. Pipeline grouped counts reflect the new stage.
    pipeline = rt.registry.execute("job_pipeline", {})
    assert pipeline["grouped"]["interview"] == 1
    assert pipeline["totals"]["interview"] == 1
    assert pipeline["count"] == 1

    # 6. RAG knowledge path is reachable and redacts secrets on insert.
    remembered = rt.registry.execute(
        "remember_note", {"note": "Acme interview prep: talk about path planning. api_key=sk-leak-123"}
    )
    assert remembered.get("note_id") is not None
    context = rt.rag.build_context("acme interview prep", top_k=1)
    assert "RETRIEVED_CONTEXT" in context
    assert "sk-leak-123" not in context

    # 7. Approvals: a medium-risk action through the real executor is gated,
    #    then resumed through the orchestrator's approve path (no network).
    from app.models.schemas import Action

    pending = rt.executor.execute(
        "job cancel", Action(tool="job_cancel", risk="medium", reason="cancel the job run"), auto_approve=False
    )
    assert pending.status == "pending"
    assert pending.pending_approval_id
    approved = rt.orchestrator.approve(pending.pending_approval_id)
    assert approved.status == "approved"
    assert approved.execution.status == "completed"

    rejected = rt.executor.execute(
        "job resume", Action(tool="job_resume", risk="medium", reason="resume the job run"), auto_approve=False
    )
    assert rejected.status == "pending"
    assert rt.orchestrator.reject(rejected.pending_approval_id).status == "rejected"
    assert not rt.approvals.list_pending()

    # 8. Scheduler e2e: schedule a low-risk command, force it due, let it fire.
    #    Stop the background thread first so the manual tick is race-free.
    rt.scheduler.stop()
    job = rt.registry.execute("schedule", {"command": "list applications", "recurrence": "every 30 minutes"})
    job_id = job["job"]["id"]
    jobs = rt.scheduler.list_jobs()
    jobs[0]["next_run"] = (local_now() - timedelta(minutes=1)).isoformat()
    rt.scheduler.memory.set_state("scheduler_jobs", jobs)
    rt.scheduler.tick()
    assert rt.scheduler.wait_idle(timeout=20)
    fired = rt.scheduler.list_jobs()[0]
    assert fired["last_status"] == "completed"
    assert len(rt.scheduler.memory.get_state("scheduled_runs", default=[])) == 1
    assert rt.scheduler.remove(job_id) is True

    # 9. Suggestion engine has context-aware follow-ups for the job hunt.
    response = _list_applications(rt)
    assert response.suggestions is not None


def test_e2e_runtime_smoke_counts(runtime):
    """The full runtime exposes the expected integrated surfaces."""
    rt = runtime
    names = {item["name"] for item in rt.registry.catalog()}
    assert {"job_applied", "job_pipeline", "job_update_status", "remember_note", "recall", "task_list"} <= names
    assert rt.diagnoser is not None
    assert rt.calendar is not None
    diag = rt.registry.execute("diagnose_system", {})
    assert diag["status"] == "completed"
