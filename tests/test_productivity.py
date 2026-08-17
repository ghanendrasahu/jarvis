from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.core.calendar import CalendarStore
from app.core.memory import MemoryStore
from app.core.scheduler import Scheduler, local_now, parse_reminder_when
from app.tools.productivity_tools import (
    _send_smtp_email,
    register_productivity_tools,
)
from app.tools.registry import ToolRegistry


def _store(tmp_path) -> CalendarStore:
    return CalendarStore(str(tmp_path / "calendar.db"))


def test_calendar_add_and_list(tmp_path):
    calendar = _store(tmp_path)
    start = datetime.now(UTC).isoformat()
    event = calendar.add_event("Standup", start, duration_minutes=30, notes="sync")
    assert event["title"] == "Standup"
    listed = calendar.list_events()
    assert len(listed) == 1 and listed[0]["id"] == event["id"]
    assert listed[0]["notes"] == "sync"


def test_calendar_upcoming_and_next(tmp_path):
    calendar = _store(tmp_path)
    later = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    calendar.add_event("Past meeting", past)
    calendar.add_event("Future standup", later)
    upcoming = calendar.upcoming_events(hours=24)
    assert [event["title"] for event in upcoming] == ["Future standup"]
    next_event = calendar.next_event()
    assert next_event is not None and next_event["title"] == "Future standup"


def test_calendar_delete(tmp_path):
    calendar = _store(tmp_path)
    event = calendar.add_event("One-off", datetime.now(UTC).isoformat())
    assert calendar.delete_event(event["id"])
    assert not calendar.delete_event(event["id"])
    assert calendar.list_events() == []


def test_calendar_rejects_invalid_start(tmp_path):
    calendar = _store(tmp_path)
    with pytest.raises(ValueError, match="Invalid start time"):
        calendar.add_event("Bad", "not-a-date")


def test_parse_reminder_when_variants():
    now = local_now()
    later = parse_reminder_when("in 30 minutes")
    assert later > now
    assert parse_reminder_when("at 09:00").hour == 9
    assert parse_reminder_when("tomorrow at 10:15").day == now.day + 1


def test_parse_reminder_when_rejects_garbage():
    with pytest.raises(ValueError, match="Unrecognized reminder time"):
        parse_reminder_when("sometime soon")


def _scheduler(tmp_path) -> Scheduler:
    memory = MemoryStore(str(tmp_path / "memory.db"))
    return Scheduler(Settings(), memory, orchestrator=None)


def test_scheduler_add_once_and_fire(tmp_path):
    scheduler = _scheduler(tmp_path)
    when = local_now() + timedelta(minutes=5)
    job = scheduler.add_once("announce hello", when)
    assert job["spec"]["type"] == "once"
    assert scheduler._job_next_run(job, local_now()) == when
    assert job["enabled"] is True


def test_scheduler_once_rejects_past_and_disables_after_fire(tmp_path):
    scheduler = _scheduler(tmp_path)
    with pytest.raises(ValueError, match="in the past"):
        scheduler.add_once("announce late", local_now() - timedelta(minutes=1))

    scheduler.add("announce test", "once at 00:00")
    jobs = scheduler.list_jobs()
    due = local_now() - timedelta(minutes=1)
    jobs[0]["spec"] = {"type": "once", "at": due.isoformat()}
    jobs[0]["next_run"] = due.isoformat()
    jobs[0]["enabled"] = True
    scheduler._save(jobs)

    scheduler.tick()
    scheduler.wait_idle(timeout=3.0)
    assert scheduler.list_jobs()[0]["enabled"] is False


def test_send_email_requires_config():
    settings = Settings(smtp_host=None, smtp_username=None, smtp_password=None)
    with pytest.raises(RuntimeError, match="SMTP is not configured"):
        _send_smtp_email(settings, "a@b.com", "hi", "body")


def test_send_email_sends(monkeypatch):
    settings = Settings(smtp_host="smtp.test", smtp_username="u", smtp_password="p", smtp_from="me@x.com")
    sent: dict = {}

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self):
            pass

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr("app.tools.productivity_tools.smtplib.SMTP", FakeSMTP)
    _send_smtp_email(settings, "a@b.com", "subject", "body text")
    assert sent["login"] == ("u", "p")
    assert sent["message"]["To"] == "a@b.com"
    assert "body text" in sent["message"].get_content()


def _registry(tmp_path, scheduler=None, calendar=None):
    settings = Settings(workspace_root=str(tmp_path), data_dir=str(tmp_path / "data"))
    registry = ToolRegistry()
    register_productivity_tools(registry, settings, scheduler=scheduler, calendar=calendar)
    return registry


def test_productivity_tools_register(tmp_path):
    registry = _registry(tmp_path, scheduler=_scheduler(tmp_path), calendar=_store(tmp_path))
    for name in ("send_email", "announce", "set_reminder", "add_event", "upcoming_events", "copy_to_clipboard", "read_clipboard"):
        assert registry.get(name) is not None


def test_announce_tool(tmp_path):
    registry = _registry(tmp_path)
    out = registry.execute("announce", {"message": "time to stand up"})
    assert out["result_text"] == "time to stand up"


def test_set_reminder_tool(tmp_path):
    scheduler = _scheduler(tmp_path)
    registry = _registry(tmp_path, scheduler=scheduler)
    out = registry.execute("set_reminder", {"message": "drink water", "when": "in 30 minutes"})
    assert "Reminder set" in out["result_text"]
    assert scheduler.list_jobs()[0]["command"] == "announce drink water"


def test_set_reminder_rejects_bad_time(tmp_path):
    scheduler = _scheduler(tmp_path)
    registry = _registry(tmp_path, scheduler=scheduler)
    with pytest.raises(ValueError, match="Unrecognized reminder time"):
        registry.execute("set_reminder", {"message": "x", "when": "later"})
