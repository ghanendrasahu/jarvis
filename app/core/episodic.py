from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.core.memory import MemoryStore

logger = get_logger("episodic")


class EpisodicMemory:
    """Episodic memory layer on top of the shared SQLite database.

    Stores one row per command episode: command, status, tools used, importance
    score, recall count, and timestamps. Provides deterministic retrieval
    (token-overlap + recency + importance) and selective forgetting.
    """

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._conn: sqlite3.Connection = sqlite3.connect(
            database_path, check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodic_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                command TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                tools_json TEXT NOT NULL DEFAULT '[]',
                importance REAL NOT NULL DEFAULT 1.0,
                recall_count INTEGER NOT NULL DEFAULT 0,
                last_recalled_at TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episodic_created ON episodic_memory(created_at)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episodic_command ON episodic_memory(command)"
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record_episode(
        self,
        command: str,
        status: str = "completed",
        summary: str = "",
        tools: list[str] | None = None,
        source: str = "api",
    ) -> None:
        """Record a completed command as an episodic memory entry."""
        now = datetime.now(UTC).isoformat()
        tools_json = json.dumps(tools or [])
        importance = 1.0
        # Dedupe: skip if an episode with the same command+created_at already exists
        exists = self._conn.execute(
            """
            SELECT 1 FROM episodic_memory
            WHERE command = ? AND created_at = ?
            """,
            (command, now),
        ).fetchone()
        if exists:
            return
        self._conn.execute(
            """
            INSERT INTO episodic_memory
                (created_at, command, status, summary, tools_json, importance, recall_count, last_recalled_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
            """,
            (now, command, status, summary, tools_json, importance),
        )
        self._conn.commit()
        logger.debug(
            "recorded episodic memory: command=%s status=%s", command[:40], status
        )

    # ------------------------------------------------------------------
    # Read / recall
    # ------------------------------------------------------------------

    def _token_overlap(self, a: str, b: str) -> float:
        """Simple Jaccard-like overlap: |a∩b| / |a∪b|, returns 0 if both empty."""
        if not a and not b:
            return 0.0
        set_a = set(a.lower().split())
        set_b = set(b.lower().split())
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union

    def recall_episodes(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.05,
    ) -> list[dict[str, Any]]:
        """Return up to *limit* episodes relevant to *query*.

        Scoring = token_overlap(query, episode_text) + recency_bonus + importance_bonus.
        episode_text = command + " " + summary + " " + tools joined.
        """
        if not query.strip():
            return []
        rows = self._conn.execute(
            "SELECT id, created_at, command, status, summary, tools_json, importance, recall_count, last_recalled_at"
            " FROM episodic_memory ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
        now = datetime.now(UTC).isoformat()
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            row_dict: dict[str, Any] = {
                "id": row[0],
                "created_at": row[1],
                "command": row[2],
                "status": row[3],
                "summary": row[4],
                "tools": json.loads(row[5] or "[]"),
                "importance": float(row[6] or 1.0),
                "recall_count": int(row[7] or 0),
                "last_recalled_at": row[8],
            }
            episode_text = " ".join(
                [str(row_dict["command"]), str(row_dict["summary"]), " ".join(row_dict["tools"])]
            )
            overlap = self._token_overlap(query, episode_text)
            # recency bonus: newer = higher; age in hours
            try:
                created = datetime.fromisoformat(row_dict["created_at"])
                age_hours = max(0, (datetime.fromisoformat(now) - created).total_seconds()) / 3600.0
                recency_bonus = max(0.0, 1.0 - age_hours / 168.0) * 0.15  # half-life ~1 week
            except Exception:
                recency_bonus = 0.0
            # importance bonus
            imp_bonus = (row_dict["importance"] - 1.0) * 0.05
            score = overlap + recency_bonus + imp_bonus
            if score >= min_score:
                scored.append((score, row_dict))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s[1] for s in scored[:limit]]

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent episodes (by created_at desc)."""
        rows = self._conn.execute(
            "SELECT id, created_at, command, status, summary, tools_json, importance, recall_count, last_recalled_at"
            " FROM episodic_memory ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "id": row[0],
                    "created_at": row[1],
                    "command": row[2],
                    "status": row[3],
                    "summary": row[4],
                    "tools": json.loads(row[5] or "[]"),
                    "importance": float(row[6] or 1.0),
                    "recall_count": int(row[7] or 0),
                    "last_recalled_at": row[8],
                }
            )
        return result

    # ------------------------------------------------------------------
    # Forget / clear
    # ------------------------------------------------------------------

    def forget_episodes(self, pattern: str | None = None, episode_id: int | None = None) -> int:
        """Delete episodes matching *pattern* (command LIKE) or *episode_id*.

        Returns the number of rows deleted.
        """
        if episode_id is not None:
            self._conn.execute("DELETE FROM episodic_memory WHERE id = ?", (episode_id,))
        elif pattern is not None:
            self._conn.execute(
                "DELETE FROM episodic_memory WHERE command LIKE ?", (f"%{pattern}%",)
            )
        else:
            return 0
        self._conn.commit()
        deleted = self._conn.execute("SELECT changes()").fetchone()[0]
        logger.info("forgot %d episodic memory entry(ies)", deleted)
        return deleted

    def clear_all_episodes(self) -> int:
        """Delete ALL episodic memory entries. Returns count deleted."""
        self._conn.execute("DELETE FROM episodic_memory")
        self._conn.commit()
        deleted = self._conn.execute("SELECT changes()").fetchone()[0]
        logger.warning("cleared all %d episodic memory entries", deleted)
        return deleted

    # ------------------------------------------------------------------
    # Consolidation / summary
    # ------------------------------------------------------------------

    def episode_summary(self, limit: int = 20) -> dict[str, Any]:
        """Return a summary of episodic memory statistics and recent entries."""
        rows = self.list_recent(limit=limit)
        status_counts: dict[str, int] = {}
        tool_counts: dict[str, int] = {}
        importance_total = 0.0
        for entry in rows:
            status_counts[entry["status"]] = status_counts.get(entry["status"], 0) + 1
            importance_total += entry["importance"]
            for tool in entry.get("tools", []):
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
        return {
            "total_episodes": len(rows),
            "status_counts": status_counts,
            "average_importance": round(importance_total / max(len(rows), 1), 3),
            "top_tools": dict(sorted(tool_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]),
            "recent": rows,
        }

    def consolidate_episodes(self, max_new: int = 200, memory: MemoryStore | None = None) -> int:
        """Backfill episodes from command_history that are not yet recorded.

        Tracks last consolidated command_history id in MemoryStore's runtime_state
        as ``episodic_consolidated_id``. This method is idempotent.
        """
        from app.core.memory import MemoryStore  # local import to avoid cycles

        mem: MemoryStore = memory or MemoryStore(self.database_path)
        raw_last_id = mem.get_state("episodic_consolidated_id", default=0)
        last_id = raw_last_id if isinstance(raw_last_id, int) else 0
        rows = mem._conn.execute(
            """
            SELECT id, created_at, source, command, response_text
            FROM command_history
            WHERE id > ?
            ORDER BY id LIMIT ?
            """,
            (last_id, max_new),
        ).fetchall()
        if not rows:
            return 0
        for row in rows:
            row_id, created_at, source, command, response_text = row
            # Derive status from matching execution_metrics within a 2s window
            status = "completed"
            try:
                # Look for a matching execution_metrics row by command+created_at proximity
                em_row = mem._conn.execute(
                    """
                    SELECT status, actions_completed, actions_failed
                    FROM execution_metrics
                    WHERE command = ? AND created_at BETWEEN ? AND datetime(?, '+2 seconds')
                    ORDER BY id LIMIT 1
                    """,
                    (command, created_at, created_at),
                ).fetchone()
                if em_row:
                    if (em_row[2] or 0) > 0:
                        status = "partial"
                    elif (em_row[1] or 0) > 0:
                        status = "completed"
                    else:
                        status = "failed"
            except Exception:
                status = "completed"
            # Derive tools from tool_history for this command in the same window
            tools_set: set[str] = set()
            try:
                tool_rows = mem._conn.execute(
                    """
                    SELECT DISTINCT tool FROM tool_history
                    WHERE command = ? AND created_at BETWEEN ? AND datetime(?, '+2 seconds')
                    """,
                    (command, created_at, created_at),
                ).fetchall()
                tools_set = {str(r[0]) for r in tool_rows}
            except Exception:
                pass
            summary = (response_text or "")[:120]
            if not summary.strip():
                summary = command[:80]
            self.record_episode(
                command=command,
                status=status,
                summary=summary,
                tools=sorted(tools_set) if tools_set else None,
                source=source or "api",
            )
        # Update consolidated id tracker
        new_last = max(row[0] for row in rows)
        mem.set_state("episodic_consolidated_id", new_last)
        logger.info("consolidated %d new episodic episodes (now at id %d)", len(rows), new_last)
        return len(rows)
