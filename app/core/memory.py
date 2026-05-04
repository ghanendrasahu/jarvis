from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class MemoryStore:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._lock = Lock()
        Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    command TEXT NOT NULL,
                    response_text TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    command TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    status TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    output_json TEXT,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    command TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    actions_total INTEGER NOT NULL,
                    actions_completed INTEGER NOT NULL,
                    actions_failed INTEGER NOT NULL,
                    actions_pending INTEGER NOT NULL
                )
                """
            )
            conn.commit()

    def log_command(self, source: str, command: str, response_text: str) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO command_history (created_at, source, command, response_text)
                    VALUES (?, ?, ?, ?)
                    """,
                    (created_at, source, command, response_text),
                )
                conn.commit()

    def log_tool_result(
        self,
        command: str,
        tool: str,
        status: str,
        risk: str,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        output_json = json.dumps(output or {})
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO tool_history (created_at, command, tool, status, risk, output_json, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (created_at, command, tool, status, risk, output_json, error),
                )
                conn.commit()

    def recent_conversation(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT created_at, source, command, response_text
                    FROM command_history
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def recent_tool_history(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT created_at, command, tool, status, risk, output_json, error
                    FROM tool_history
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["output_json"] = json.loads(payload.get("output_json") or "{}")
            result.append(payload)
        return result

    def set_state(self, key: str, value: dict[str, Any]) -> None:
        if not key.strip():
            raise ValueError("State key cannot be empty.")
        created_at = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(value)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO runtime_state (key, value_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value_json=excluded.value_json,
                        updated_at=excluded.updated_at
                    """,
                    (key.strip(), payload, created_at),
                )
                conn.commit()

    def get_state(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT value_json
                    FROM runtime_state
                    WHERE key = ?
                    """,
                    (key.strip(),),
                ).fetchone()
        if not row:
            return dict(default or {})
        try:
            return json.loads(row["value_json"] or "{}")
        except Exception:
            return dict(default or {})

    def log_execution_metric(
        self,
        source: str,
        command: str,
        status: str,
        duration_ms: int,
        actions_total: int,
        actions_completed: int,
        actions_failed: int,
        actions_pending: int,
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO execution_metrics (
                        created_at, source, command, status, duration_ms,
                        actions_total, actions_completed, actions_failed, actions_pending
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        created_at,
                        source,
                        command,
                        status,
                        int(duration_ms),
                        int(actions_total),
                        int(actions_completed),
                        int(actions_failed),
                        int(actions_pending),
                    ),
                )
                conn.commit()

    def recent_execution_metrics(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(5000, int(limit)))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT created_at, source, command, status, duration_ms,
                           actions_total, actions_completed, actions_failed, actions_pending
                    FROM execution_metrics
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def metrics_summary(self, limit: int = 500) -> dict[str, Any]:
        safe_limit = max(10, min(5000, int(limit)))
        rows = self.recent_execution_metrics(limit=safe_limit)
        if not rows:
            return {
                "window": safe_limit,
                "total_commands": 0,
                "status_counts": {},
                "avg_duration_ms": 0,
                "success_rate": 0.0,
                "failure_rate": 0.0,
                "top_failed_tools": [],
            }

        total = len(rows)
        duration_sum = sum(int(item.get("duration_ms", 0)) for item in rows)
        status_counts: dict[str, int] = {}
        for row in rows:
            status = str(row.get("status", "unknown"))
            status_counts[status] = status_counts.get(status, 0) + 1

        completed_count = status_counts.get("completed", 0)
        partial_count = status_counts.get("partial", 0)
        successful = completed_count + partial_count
        failed_count = status_counts.get("failed", 0)

        with self._lock:
            with self._connect() as conn:
                failed_tools_rows = conn.execute(
                    """
                    SELECT tool, COUNT(*) as failures
                    FROM tool_history
                    WHERE status = 'failed'
                    GROUP BY tool
                    ORDER BY failures DESC
                    LIMIT 10
                    """
                ).fetchall()

        return {
            "window": safe_limit,
            "total_commands": total,
            "status_counts": status_counts,
            "avg_duration_ms": round(duration_sum / total, 2),
            "success_rate": round(successful / total, 4),
            "failure_rate": round(failed_count / total, 4),
            "top_failed_tools": [dict(row) for row in failed_tools_rows],
        }
