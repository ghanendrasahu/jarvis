from __future__ import annotations

from datetime import datetime, timedelta

from app.config import Settings
from app.core.memory import MemoryStore
from app.core.scheduler import (
    Scheduler,
    _due,
    initial_next_run,
    local_now,
    next_run,
    parse_recurrence,
)


class FakeResult:
    status = "completed"
    response_text = "ok"


class FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute_command(self, command: str, source: str = "api", auto_approve: bool = False) -> FakeResult:
        self.calls.append(command)
        return FakeResult()


def _make_scheduler(tmp_path) -> tuple[Scheduler, FakeOrchestrator]:
    memory = MemoryStore(str(tmp_path / "scheduler.db"))
    orchestrator = FakeOrchestrator()
    scheduler = Scheduler(Settings(workspace_root="."), memory, orchestrator)
    return scheduler, orchestrator


def test_parse_recurrence_forms():
    assert parse_recurrence("daily at 09:00") == {"type": "daily", "hour": 9, "minute": 0}
    assert parse_recurrence("hourly") == {"type": "interval", "minutes": 60}
    assert parse_recurrence("every 30 minutes") == {"type": "interval", "minutes": 30}
    weekly = parse_recurrence("weekly on monday at 10:30")
    assert weekly is not None
    assert weekly["type"] == "weekly"
    assert parse_recurrence("garbage") is None


def test_due_daily_local_time():
    now = datetime(2026, 8, 18, 10, 0)  # naive local: Tuesday
    spec = {"type": "daily", "hour": 9, "minute": 0}
    yesterday_8am = (now - timedelta(days=1)).replace(hour=8, minute=0).isoformat()
    assert _due(spec, yesterday_8am, now)
    today_after_target = now.replace(hour=9, minute=5).isoformat()
    assert not _due(spec, today_after_target, now)


def test_due_with_no_last_run_never_fires_immediately():
    # Regression: creating a job must not cause an immediate execution.
    assert _due({"type": "interval", "minutes": 30}, None, datetime(2026, 8, 18, 10, 0)) is False
    assert _due({"type": "daily", "hour": 9, "minute": 0}, None, datetime(2026, 8, 18, 10, 0)) is False
    assert _due({"type": "weekly", "weekday": 0, "hour": 9, "minute": 0}, None, datetime(2026, 8, 18, 10, 0)) is False


def test_interval_next_run_is_after_creation():
    spec = {"type": "interval", "minutes": 30}
    created = datetime(2026, 8, 18, 10, 0)
    assert next_run(spec, created) == datetime(2026, 8, 18, 10, 30)
    assert initial_next_run(spec, created) == datetime(2026, 8, 18, 10, 30)


def test_daily_next_run_is_next_local_occurrence():
    spec = {"type": "daily", "hour": 9, "minute": 0}
    # Created before the target time today -> fires today.
    before = datetime(2026, 8, 18, 8, 0)
    assert next_run(spec, before) == datetime(2026, 8, 18, 9, 0)
    # Created after the target time -> fires tomorrow (no immediate catch-up today).
    after = datetime(2026, 8, 18, 14, 0)
    assert next_run(spec, after) == datetime(2026, 8, 19, 9, 0)


def test_weekly_next_run_is_next_weekday():
    spec = {"type": "weekly", "weekday": 0, "hour": 10, "minute": 0}  # Monday
    tuesday = datetime(2026, 8, 18, 9, 0)  # 2026-08-18 is a Tuesday
    assert next_run(spec, tuesday) == datetime(2026, 8, 24, 10, 0)
    monday_before = datetime(2026, 8, 17, 9, 0)  # Monday before 10:00 -> same day
    assert next_run(spec, monday_before) == datetime(2026, 8, 17, 10, 0)


def test_new_job_does_not_fire_immediately(tmp_path):
    scheduler, orchestrator = _make_scheduler(tmp_path)
    job = scheduler.add("system status", "every 5 minutes")
    assert job["enabled"] is True
    scheduler.tick()
    scheduler.wait_idle(timeout=10)
    assert orchestrator.calls == []
    updated = scheduler.list_jobs()[0]
    assert updated["next_run"] is not None


def test_tick_fires_when_next_run_reached(tmp_path):
    scheduler, orchestrator = _make_scheduler(tmp_path)
    job = scheduler.add("system status", "every 5 minutes")
    jobs = scheduler.list_jobs()
    jobs[0]["next_run"] = (local_now() - timedelta(minutes=1)).isoformat()
    scheduler.memory.set_state("scheduler_jobs", jobs)

    scheduler.tick()
    assert scheduler.wait_idle(timeout=20)
    updated = scheduler.list_jobs()[0]
    assert updated["last_status"] == "completed"
    assert orchestrator.calls == ["system status"]

    before = updated["next_run"]
    scheduler.tick()
    assert scheduler.wait_idle(timeout=10)
    assert orchestrator.calls == ["system status"]  # not due again yet
    assert scheduler.list_jobs()[0]["next_run"] == before

    assert scheduler.remove(job["id"]) is True
    assert scheduler.list_jobs() == []
    assert scheduler.remove("missing") is False


def test_daily_job_fires_at_local_target(tmp_path):
    scheduler, orchestrator = _make_scheduler(tmp_path)
    scheduler.add("system status", "daily at 09:00")
    jobs = scheduler.list_jobs()
    jobs[0]["next_run"] = (local_now() - timedelta(hours=1)).isoformat()
    scheduler.memory.set_state("scheduler_jobs", jobs)

    scheduler.tick()
    assert scheduler.wait_idle(timeout=20)
    assert orchestrator.calls == ["system status"]
    updated = scheduler.list_jobs()[0]
    parsed = datetime.fromisoformat(updated["next_run"])
    assert (parsed.hour, parsed.minute) == (9, 0)


def test_disabled_job_does_not_fire(tmp_path):
    scheduler, orchestrator = _make_scheduler(tmp_path)
    job = scheduler.add("system status", "every 5 minutes")
    assert scheduler.set_enabled(job["id"], False) is True
    jobs = scheduler.list_jobs()
    jobs[0]["next_run"] = (local_now() - timedelta(minutes=1)).isoformat()
    scheduler.memory.set_state("scheduler_jobs", jobs)
    scheduler.tick()
    assert orchestrator.calls == []


def test_scheduler_runs_due_job_and_removes(tmp_path):
    scheduler, orchestrator = _make_scheduler(tmp_path)
    job = scheduler.add("system status", "every 5 minutes")
    assert job["enabled"] is True
    assert len(scheduler.list_jobs()) == 1

    jobs = scheduler.list_jobs()
    jobs[0]["next_run"] = (local_now() - timedelta(minutes=1)).isoformat()
    scheduler.memory.set_state("scheduler_jobs", jobs)

    scheduler.tick()
    assert scheduler.wait_idle(timeout=20)
    updated = scheduler.list_jobs()[0]
    assert updated["last_status"] == "completed"
    assert orchestrator.calls == ["system status"]
    runs = scheduler.memory.get_state("scheduled_runs", default=[])
    assert len(runs) == 1

    assert scheduler.remove(job["id"]) is True
    assert scheduler.list_jobs() == []
    assert scheduler.remove("missing") is False
