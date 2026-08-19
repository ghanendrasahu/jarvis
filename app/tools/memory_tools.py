from __future__ import annotations

from typing import Any

from app.config import Settings
from app.core.episodic import EpisodicMemory
from app.tools.registry import ToolRegistry


def _episodic_memory(memory: Any) -> EpisodicMemory:
    """Return an EpisodicMemory instance wired to the same DB as *memory*.

    The *memory* argument is a MemoryStore instance; we use its ``database_path``
    attribute so both stores share the same SQLite file.
    """
    from app.core.memory import MemoryStore  # local import to avoid cycles
    if isinstance(memory, MemoryStore):
        return EpisodicMemory(memory.database_path)
    # Fallback: construct from the path string if somehow passed directly.
    return EpisodicMemory(str(memory))


def memory_recall(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    limit = int(args.get("limit") or 5)
    min_score = float(args.get("min_score") or 0.05)
    mem = _episodic_memory(args.get("memory"))
    episodes = mem.recall_episodes(query=query, limit=limit, min_score=min_score)
    return {
        "count": len(episodes),
        "episodes": [
            {
                "id": e["id"],
                "command": e["command"],
                "status": e["status"],
                "summary": e["summary"],
                "tools": e.get("tools", []),
                "importance": e["importance"],
                "recall_count": e["recall_count"],
                "last_recalled_at": e.get("last_recalled_at"),
            }
            for e in episodes
        ],
        "result_text": f"Found {len(episodes)} relevant memory entry{'s' if len(episodes) != 1 else ''}.",
    }


def memory_list(args: dict[str, Any]) -> dict[str, Any]:
    limit = int(args.get("limit") or 20)
    mem = _episodic_memory(args.get("memory"))
    episodes = mem.list_recent(limit=limit)
    # Build a compact overview: recent episodes + top tools
    top_tools: dict[str, int] = {}
    for ep in episodes:
        for t in ep.get("tools", []):
            top_tools[t] = top_tools.get(t, 0) + 1
    return {
        "count": len(episodes),
        "episodes": [
            {
                "id": e["id"],
                "command": e["command"],
                "status": e["status"],
                "summary": e["summary"],
                "tools": e.get("tools", []),
            }
            for e in episodes
        ],
        "top_tools": dict(sorted(top_tools.items(), key=lambda kv: kv[1], reverse=True)[:8]),
        "result_text": f"{len(episodes)} episode{'s' if len(episodes) != 1 else ''} listed.",
    }


def memory_forget(args: dict[str, Any]) -> dict[str, Any]:
    pattern = args.get("pattern")
    episode_id = args.get("episode_id")
    mem = _episodic_memory(args.get("memory"))
    deleted = mem.forget_episodes(pattern=pattern, episode_id=episode_id)
    return {
        "deleted": deleted,
        "result_text": f"Forgot {deleted} episodic memory entry{'s' if deleted != 1 else ''}.",
    }


def memory_clear(args: dict[str, Any]) -> dict[str, Any]:
    mem = _episodic_memory(args.get("memory"))
    deleted = mem.clear_all_episodes()
    return {
        "deleted": deleted,
        "result_text": f"Cleared all {deleted} episodic memory entry{'s' if deleted != 1 else ''}.",
    }


def register_memory_tools(registry: ToolRegistry, settings: Settings, memory: Any) -> None:
    """Register the 4 memory tools with the given *registry* and *settings*."""

    registry.register(
        "memory_recall",
        "Recall episodic memories relevant to a query.",
        "low",
        {"query": "str", "limit": "int (optional)", "min_score": "float (optional)"},
        memory_recall,
        category="memory",
    )
    registry.register(
        "memory_list",
        "List recent episodic memories plus a unified view of top tools.",
        "low",
        {"limit": "int (optional)"},
        memory_list,
        category="memory",
    )
    registry.register(
        "memory_forget",
        "Forget episodic memories matching a command pattern or ID.",
        "medium",
        {"pattern": "str (optional)", "episode_id": "int (optional)"},
        memory_forget,
        category="memory",
    )
    registry.register(
        "memory_clear",
        "Clear ALL episodic memories (destructive).",
        "high",
        {},
        memory_clear,
        category="memory",
    )
