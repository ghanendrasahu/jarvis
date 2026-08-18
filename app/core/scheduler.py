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


def _due(spec: dict[str, Any], last_run: str | None, now: datetime) -> bool:
    if last_run is None:
        return True
    try:
        last = datetime.fromisoformat(last_run)
    except Exception:
        return True
    kind = spec.get("type")
    if kind == "interval":
        return (now - last) >= timedelta(minutes=int(spec.get("minutes", 60)))
    if kind == "daily":
        target = now.replace(hour=int(spec.get("hour", 0)), minute=int(spec.get("minute", 0)), second=0, microsecond=0)
        return now >= target and last < target
    if kind == "weekly":
        target_day = (now.weekday() - int(spec.get("weekday", 0))) % 7
        target = (now - timedelta(days=target_day)).replace(
            hour=int(spec.get("hour", 0)), minute=int(spec.get("minute", 0)), second=0, microsecond=0
        )
        if target > now:
            target -= timedelta(days=7)
        return now >= target and last < target
    return False


class Scheduler:
    """Persistent recurring-command scheduler running in a daemon thread."""

    def __init__(self, settings: Settings, memory: MemoryStore, orchestrator: Any, poll_seconds: int = 20) -> None:
        self.settings = settings
        self.memory = memory
        self.orchestrator = orchestrator
        self._poll_seconds = poll_seconds
        self._thread: threading.Thread | None = None
        self._running = False
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
        }
        jobs = self._jobs()
        jobs.append(job)
        self._save(jobs)
        logger.info("scheduled job %s: %s (%s)", job["id"], command, recurrence)
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

    def tick(self) -> None:
        jobs = self._jobs()
        now = datetime.now(UTC)
        for job in jobs:
            if not job.get("enabled"):
                continue
            if _due(job.get("spec") or {}, job.get("last_run"), now):
                job["last_run"] = now.isoformat()
                self._launch(job)
        self._save(jobs)

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

        threading.Thread(target=worker, daemon=True).start()

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
