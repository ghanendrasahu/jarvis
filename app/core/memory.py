from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.logging import get_logger

logger = get_logger("memory")


class MemoryStore:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._lock = Lock()
        Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._connect()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn
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
                CREATE TABLE IF NOT EXISTS planner_cache (
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs_applied (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    site TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT,
                    location TEXT,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    notes TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_applied_url ON jobs_applied(url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_applied_status ON jobs_applied(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_history_status ON tool_history(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_history_tool ON tool_history(tool)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_execution_metrics_created ON execution_metrics(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_command_history_created ON command_history(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_planner_cache_updated ON planner_cache(updated_at)")
            conn.commit()

    def prune(self, days: int = 60) -> dict[str, int]:
        """Delete history/metrics older than ``days``. Returns rows removed per table."""
        if days <= 0:
            return {"command_history": 0, "tool_history": 0, "execution_metrics": 0}
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        counts: dict[str, int] = {}
        with self._lock:
            conn = self._conn
            for table in ("command_history", "tool_history", "execution_metrics"):
                cursor = conn.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))
                counts[table] = cursor.rowcount
            conn.commit()
        if any(counts.values()):
            logger.info("pruned old records: %s", counts)
        return counts

    def log_command(self, source: str, command: str, response_text: str) -> None:
        created_at = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO command_history (created_at, source, command, response_text)
                VALUES (?, ?, ?, ?)
                """,
                (created_at, source, command, response_text),
            )
            self._conn.commit()

    def log_tool_result(
        self,
        command: str,
        tool: str,
        status: str,
        risk: str,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        created_at = datetime.now(UTC).isoformat()
        output_json = json.dumps(output or {})
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tool_history (created_at, command, tool, status, risk, output_json, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (created_at, command, tool, status, risk, output_json, error),
            )
            self._conn.commit()

    def recent_conversation(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
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
            rows = self._conn.execute(
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

    def set_state(self, key: str, value: Any) -> None:
        if not key.strip():
            raise ValueError("State key cannot be empty.")
        created_at = datetime.now(UTC).isoformat()
        payload = json.dumps(value)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO runtime_state (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_at=excluded.updated_at
                """,
                (key.strip(), payload, created_at),
            )
            self._conn.commit()

    def get_state(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute(
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

    def cache_get(self, key: str, ttl_days: int = 0) -> Any:
        """Return a cached planner value, evicting it if it is older than ``ttl_days`` (0 = never expires)."""
        if not key.strip():
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT value_json, updated_at
                FROM planner_cache
                WHERE key = ?
                """,
                (key.strip(),),
            ).fetchone()
        if not row:
            return None
        if ttl_days > 0:
            cutoff = (datetime.now(UTC) - timedelta(days=ttl_days)).isoformat()
            if row["updated_at"] < cutoff:
                self._evict_cache(key.strip())
                return None
        try:
            return json.loads(row["value_json"] or "{}")
        except Exception:
            self._evict_cache(key.strip())
            return None

    def cache_set(self, key: str, value: Any, max_entries: int = 500) -> None:
        if not key.strip():
            raise ValueError("Cache key cannot be empty.")
        payload = json.dumps(value)
        created_at = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO planner_cache (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_at=excluded.updated_at
                """,
                (key.strip(), payload, created_at),
            )
            if max_entries > 0:
                self._conn.execute(
                    """
                    DELETE FROM planner_cache
                    WHERE key NOT IN (
                        SELECT key FROM planner_cache ORDER BY updated_at DESC LIMIT ?
                    )
                    """,
                    (max_entries,),
                )
            self._conn.commit()

    def _evict_cache(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM planner_cache WHERE key = ?", (key,))
            self._conn.commit()

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
        created_at = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
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
            self._conn.commit()

    def recent_execution_metrics(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(5000, int(limit)))
        with self._lock:
            rows = self._conn.execute(
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
            failed_tools_rows = self._conn.execute(
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

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def is_job_applied(self, url: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM jobs_applied WHERE url = ? AND status = 'applied' LIMIT 1",
                (url,),
            ).fetchone()
        return row is not None

    def update_job_status(self, url: str, status: str, notes: str | None = None) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM jobs_applied WHERE url = ? ORDER BY id DESC LIMIT 1",
                (url,),
            ).fetchone()
            if not row:
                return False
            self._conn.execute(
                "UPDATE jobs_applied SET status = ?, notes = ? WHERE id = ?",
                (status, notes, row["id"]),
            )
            self._conn.commit()
        return True

    def log_job_application(
        self,
        site: str,
        title: str,
        company: str | None,
        location: str | None,
        url: str,
        status: str,
        notes: str | None = None,
    ) -> None:
        created_at = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs_applied (created_at, site, title, company, location, url, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (created_at, site, title, company, location, url, status, notes),
            )
            self._conn.commit()

    def recent_job_applications(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(500, int(limit)))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT created_at, site, title, company, location, url, status, notes
                FROM jobs_applied
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def job_applications_summary(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS count FROM jobs_applied GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def session_review(self, since: str | None = None) -> dict[str, Any]:
        """Summarize everything that happened since an ISO timestamp (or everything if None)."""
        where = ""
        params: list[Any] = []
        if since:
            where = "WHERE created_at > ?"
            params = [since]
        with self._lock:
            cmd_rows = self._conn.execute(
                f"SELECT source, command, response_text, created_at FROM command_history {where} ORDER BY id",
                params,
            ).fetchall()
            tool_rows = self._conn.execute(
                f"SELECT tool, status FROM tool_history {where} ORDER BY id",
                params,
            ).fetchall()
            job_rows = self._conn.execute(
                f"SELECT site, title, company, status, created_at FROM jobs_applied {where} ORDER BY id",
                params,
            ).fetchall()

        commands = [dict(row) for row in cmd_rows]
        tool_rows = [dict(row) for row in tool_rows]
        job_rows = [dict(row) for row in job_rows]
        source_counts: dict[str, int] = {}
        for row in commands:
            source = str(row.get("source") or "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1

        tool_counts: dict[str, int] = {}
        top_tools: dict[str, int] = {}
        for row in tool_rows:
            status = str(row.get("status") or "unknown")
            tool_counts[status] = tool_counts.get(status, 0) + 1
            tool = str(row.get("tool") or "?")
            top_tools[tool] = top_tools.get(tool, 0) + 1

        jobs = [dict(row) for row in job_rows]
        job_status: dict[str, int] = {}
        for row in jobs:
            status = str(row.get("status") or "unknown")
            job_status[status] = job_status.get(status, 0) + 1

        return {
            "commands": commands,
            "command_count": len(commands),
            "sources": source_counts,
            "tool_counts": tool_counts,
            "top_tools": dict(sorted(top_tools.items(), key=lambda kv: kv[1], reverse=True)[:8]),
            "job_applications": jobs,
            "job_status": job_status,
            "scheduled_runs": list(self.get_state("scheduled_runs", default=[]) or []),
        }
