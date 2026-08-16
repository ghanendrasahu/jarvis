from __future__ import annotations

import re
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.core.logging import get_logger
from app.core.memory import MemoryStore

logger = get_logger("scheduler")

_REDACTED_PLACEHOLDER = "<REDACTED>"

_WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def parse_recurrence(text: str) -> dict[str, Any] | None:
    """Parse a human recurrence like 'daily at 09:00', 'hourly', 'every 30 minutes', 'weekly on mon at 09:00'."""
    raw = (text or "").strip().lower()
    if not raw:
        return None
    match = re.fullmatch(r"daily at (\d{1,2}):(\d{2})", raw)
    if match:
        return {"type": "daily", "hour": int(match.group(1)), "minute": int(match.group(2))}
    match = re.fullmatch(
        r"weekly on (mon|monday|tue|tuesday|wed|wednesday|thu|thursday|fri|friday|sat|saturday|sun|sunday) at (\d{1,2}):(\d{2})",
        raw,
    )
    if match:
        return {"type": "weekly", "weekday": _WEEKDAYS[match.group(1)], "hour": int(match.group(2)), "minute": int(match.group(3))}
    if raw == "hourly":
        return {"type": "interval", "minutes": 60}
    match = re.fullmatch(r"every (\d+) minutes", raw)
    if match:
        return {"type": "interval", "minutes": max(1, int(match.group(1)))}
    return None


def local_now() -> datetime:
    """Current wall-clock time as a naive local datetime (scheduler timezone semantics)."""
    return datetime.now().astimezone().replace(tzinfo=None)


def local_tz_name() -> str:
    try:
        return str(datetime.now().astimezone().tzname()) or "local"
    except Exception:
        return "local"


def to_local(value: str | None) -> datetime | None:
    """Parse a stored ISO timestamp into a naive local datetime (handles UTC-aware values)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except Exception:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def next_run(spec: dict[str, Any], after: datetime) -> datetime:
    """Compute the next deterministic execution time strictly after ``after`` (naive local time)."""
    kind = spec.get("type")
    if kind == "interval":
        minutes = max(1, int(spec.get("minutes", 60)))
        return after + timedelta(minutes=minutes)
    hour = int(spec.get("hour", 0))
    minute = int(spec.get("minute", 0))
    if kind == "daily":
        target = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= after:
            target += timedelta(days=1)
        return target
    if kind == "weekly":
        weekday = int(spec.get("weekday", 0))
        days_ahead = (weekday - after.weekday()) % 7
        target = after + timedelta(days=days_ahead)
        target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= after:
            target += timedelta(days=7)
        return target
    # Unknown/legacy spec: fall back to a conservative hourly cadence.
    return after + timedelta(hours=1)


def initial_next_run(spec: dict[str, Any], created_at: datetime) -> datetime:
    """First execution is the next occurrence after creation (never fires immediately)."""
    return next_run(spec, created_at)


def _due(spec: dict[str, Any], last_run: str | None, now: datetime) -> bool:
    """True when a job should fire at local time ``now`` given its last run.

    A job with no recorded last run is never due through this helper: creating a job must not
    cause an immediate execution. The Scheduler tracks an explicit ``next_run`` instead.
    """
    last = to_local(last_run)
    if last is None:
        return False
    return now >= next_run(spec, last)


class Scheduler:
    """Persistent recurring-command scheduler running in a daemon thread."""

    def __init__(self, settings: Settings, memory: MemoryStore, orchestrator: Any, poll_seconds: int = 20) -> None:
        self.settings = settings
        self.memory = memory
        self.orchestrator = orchestrator
        self._poll_seconds = poll_seconds
        self._thread: threading.Thread | None = None
        self._running = False
        self._active: set[threading.Thread] = set()
        self.on_run: Callable[[dict[str, Any], str, str], None] | None = None

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="jarvis-scheduler")
        self._thread.start()
        logger.info("scheduler started")
        return True

    def stop(self) -> None:
        self._running = False

    def add(self, command: str, recurrence: str) -> dict[str, Any]:
        command = (command or "").strip()
        if not command:
            raise ValueError("Missing command to schedule.")
        spec = parse_recurrence(recurrence)
        if spec is None:
            raise ValueError(
                "Unrecognized recurrence. Use 'daily at HH:MM', 'hourly', 'every N minutes', or 'weekly on <day> at HH:MM'."
            )
        created_local = local_now()
        job = {
            "id": str(uuid.uuid4())[:8],
            "command": command,
            "recurrence": (recurrence or "").strip(),
            "spec": spec,
            "enabled": True,
            "last_run": None,
            "last_status": None,
            "last_result": None,
            "created_at": datetime.now(UTC).isoformat(),
            "tz": local_tz_name(),
            "next_run": initial_next_run(spec, created_local).isoformat(),
        }
        jobs = self._jobs()
        jobs.append(job)
        self._save(jobs)
        logger.info(
            "scheduled job %s: %s (%s), next run %s (%s)",
            job["id"], command, recurrence, job["next_run"], job["tz"],
        )
        return job

    def remove(self, job_id: str) -> bool:
        jobs = self._jobs()
        kept = [job for job in jobs if job.get("id") != job_id]
        if len(kept) == len(jobs):
            return False
        self._save(kept)
        return True

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        jobs = self._jobs()
        for job in jobs:
            if job.get("id") == job_id:
                job["enabled"] = bool(enabled)
                self._save(jobs)
                return True
        return False

    def list_jobs(self) -> list[dict[str, Any]]:
        return self._jobs()

    def _job_next_run(self, job: dict[str, Any], now: datetime) -> datetime | None:
        """Return the job's next execution time (naive local), deriving it for legacy jobs."""
        next_iso = job.get("next_run")
        if next_iso:
            return to_local(next_iso)
        last = to_local(job.get("last_run")) or to_local(job.get("created_at"))
        if last is None:
            return None
        return next_run(job.get("spec") or {}, last)

    def tick(self) -> None:
        jobs = self._jobs()
        now = local_now()
        due: list[dict[str, Any]] = []
        for job in jobs:
            if not job.get("enabled"):
                continue
            next_at = self._job_next_run(job, now)
            if next_at is None or now < next_at:
                continue
            job["last_run"] = now.isoformat()
            job["next_run"] = next_run(job.get("spec") or {}, now).isoformat()
            job["tz"] = local_tz_name()
            due.append(job)
        self._save(jobs)
        for job in due:
            self._launch(job)

    def _launch(self, job: dict[str, Any]) -> None:
        def worker() -> None:
            try:
                result = self.orchestrator.execute_command(command=job["command"], source="scheduler", auto_approve=False)
                status = str(result.status)
                text = str(result.response_text or "")[:200]
            except Exception as exc:
                status = "failed"
                text = str(exc)[:200]
            self._update_after_run(str(job.get("id")), status, text)
            if self.on_run is not None:
                try:
                    self.on_run(job, status, text)
                except Exception:
                    pass
            self._active.discard(threading.current_thread())

        thread = threading.Thread(target=worker, daemon=True)
        self._active.add(thread)
        thread.start()

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Block until all in-flight job workers finish (used by shutdown/tests)."""
        deadline = time.monotonic() + timeout
        while self._active and time.monotonic() < deadline:
            time.sleep(0.02)
        return not self._active

    def _update_after_run(self, job_id: str, status: str, text: str) -> None:
        jobs = self._jobs()
        for job in jobs:
            if job.get("id") == job_id:
                job["last_status"] = status
                job["last_result"] = text
                self._save(jobs)
                break
        runs = list(self.memory.get_state("scheduled_runs", default=[]) or [])
        runs.append(
            {"ts": datetime.now(UTC).isoformat(), "job_id": job_id, "status": status, "result": text}
        )
        self.memory.set_state("scheduled_runs", runs[-50:])

    def _jobs(self) -> list[dict[str, Any]]:
        return list(self.memory.get_state("scheduler_jobs", default=[]) or [])

    def _save(self, jobs: list[dict[str, Any]]) -> None:
        self.memory.set_state("scheduler_jobs", jobs)

    def _loop(self) -> None:
        while self._running:
            try:
                self.tick()
            except Exception as exc:
                logger.warning("scheduler tick failed: %s", exc)
            time.sleep(self._poll_seconds)
