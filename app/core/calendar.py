from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.logging import get_logger

logger = get_logger("calendar")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 60,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
"""


class CalendarStore:
    """Local SQLite calendar for personal events and upcoming-event queries."""

    def __init__(self, database_path: str) -> None:
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        with self._lock:
            with sqlite3.connect(self.database_path) as connection:
                connection.execute(_SCHEMA)
                connection.commit()

    def add_event(
        self,
        title: str,
        start_utc: str,
        duration_minutes: int = 60,
        notes: str = "",
    ) -> dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise ValueError("Event title is required.")
        try:
            datetime.fromisoformat(start_utc)
        except ValueError as exc:
            raise ValueError(f"Invalid start time: {start_utc}. Use ISO format (e.g. 2026-08-19T14:30).") from exc
        event = {
            "id": str(uuid.uuid4())[:8],
            "title": title,
            "start_utc": start_utc,
            "duration_minutes": max(1, int(duration_minutes)),
            "notes": (notes or "").strip(),
            "created_at": datetime.now(UTC).isoformat(),
        }
        with self._lock:
            with sqlite3.connect(self.database_path) as connection:
                connection.execute(
                    "INSERT INTO events (id, title, start_utc, duration_minutes, notes, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (event["id"], event["title"], event["start_utc"], event["duration_minutes"], event["notes"], event["created_at"]),
                )
                connection.commit()
        logger.info("added event %s: %s at %s", event["id"], event["title"], event["start_utc"])
        return event

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self._lock:
            with sqlite3.connect(self.database_path) as connection:
                rows = connection.execute(
                    "SELECT id, title, start_utc, duration_minutes, notes FROM events ORDER BY start_utc LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def upcoming_events(self, hours: float = 24, limit: int = 20) -> list[dict[str, Any]]:
        horizon = (datetime.now(UTC) + timedelta(hours=max(0.0, float(hours)))).isoformat()
        limit = max(1, min(500, int(limit)))
        with self._lock:
            with sqlite3.connect(self.database_path) as connection:
                rows = connection.execute(
                    "SELECT id, title, start_utc, duration_minutes, notes FROM events "
                    "WHERE start_utc >= ? AND start_utc <= ? ORDER BY start_utc LIMIT ?",
                    (datetime.now(UTC).isoformat(), horizon, limit),
                ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def next_event(self) -> dict[str, Any] | None:
        with self._lock:
            with sqlite3.connect(self.database_path) as connection:
                row = connection.execute(
                    "SELECT id, title, start_utc, duration_minutes, notes FROM events "
                    "WHERE start_utc >= ? ORDER BY start_utc LIMIT 1",
                    (datetime.now(UTC).isoformat(),),
                ).fetchone()
        return self._row_to_event(row) if row else None

    def delete_event(self, event_id: str) -> bool:
        with self._lock:
            with sqlite3.connect(self.database_path) as connection:
                cursor = connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
                connection.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_event(row: tuple) -> dict[str, Any]:
        event_id, title, start_utc, duration_minutes, notes = row
        return {
            "id": event_id,
            "title": title,
            "start_utc": start_utc,
            "duration_minutes": int(duration_minutes),
            "notes": notes or "",
        }
