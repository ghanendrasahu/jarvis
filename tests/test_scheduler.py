from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.core.memory import MemoryStore
from app.core.scheduler import Scheduler, _due, parse_recurrence


class FakeResult:
    status = "completed"
    response_text = "ok"


class FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute_command(self, command: str, source: str = "api", auto_approve: bool = False) -> FakeResult:
        self.calls.append(command)
        return FakeResult()


def test_parse_recurrence_forms():
    assert parse_recurrence("daily at 09:00") == {"type": "daily", "hour": 9, "minute": 0}
    assert parse_recurrence("hourly") == {"type": "interval", "minutes": 60}
    assert parse_recurrence("every 30 minutes") == {"type": "interval", "minutes": 30}
    assert parse_recurrence("weekly on monday at 10:30")["type"] == "weekly"
    assert parse_recurrence("garbage") is None


def test_due_daily():
    now = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    spec = {"type": "daily", "hour": 9, "minute": 0}
    yesterday_8am = (now - timedelta(days=1)).replace(hour=8, minute=0).isoformat()
    assert _due(spec, yesterday_8am, now)
    assert _due(spec, None, now)
    today_after_target = now.replace(hour=9, minute=5).isoformat()
    assert not _due(spec, today_after_target, now)


def test_scheduler_runs_due_job_and_removes(tmp_path):
    memory = MemoryStore(str(tmp_path / "scheduler.db"))
    orchestrator = FakeOrchestrator()
    scheduler = Scheduler(Settings(workspace_root="."), memory, orchestrator)

    job = scheduler.add("system status", "every 5 minutes")
    assert job["enabled"] is True
    assert len(scheduler.list()) == 1

    jobs = scheduler.list()
    jobs[0]["last_run"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    memory.set_state("scheduler_jobs", jobs)

    scheduler.tick()

    deadline = time.monotonic() + 6.0
    updated = scheduler.list()[0]
    while updated["last_status"] is None and time.monotonic() < deadline:
        time.sleep(0.05)
        updated = scheduler.list()[0]

    assert updated["last_status"] == "completed"
    assert orchestrator.calls == ["system status"]
    runs = memory.get_state("scheduled_runs", default=[])
    assert len(runs) == 1

    assert scheduler.remove(job["id"]) is True
    assert scheduler.list() == []
    assert scheduler.remove("missing") is False
