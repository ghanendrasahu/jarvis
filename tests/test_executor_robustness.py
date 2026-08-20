from __future__ import annotations

import threading
import time
from typing import Any

from app.config import Settings
from app.core.approvals import ApprovalManager
from app.core.embeddings import CachedEmbedder
from app.core.executor import ActionExecutor
from app.core.rag import RagIndex
from app.models.schemas import Action
from app.tools.registry import ToolRegistry


def _settings(tmp_path, **kwargs: Any) -> Settings:
    base = {
        "workspace_root": str(tmp_path),
        "database_path": str(tmp_path / "robust.db"),
    }
    base.update(kwargs)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Phase O — embedding cache
# ---------------------------------------------------------------------------


class _CountingEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        return [float(ord(c)) for c in text[:8]]


def test_cached_embedder_serves_repeats_from_memory():
    inner = _CountingEmbedder()
    cache = CachedEmbedder(inner, max_entries=16)
    v1 = cache.embed("same query text")
    v2 = cache.embed("same query text")
    assert v1 == v2
    assert inner.calls == 1
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1


def test_cached_embedder_bounds_size():
    inner = _CountingEmbedder()
    cache = CachedEmbedder(inner, max_entries=3)
    for i in range(10):
        cache.embed(f"query number {i}")
    assert cache.stats()["size"] == 3
    assert inner.calls == 10


def test_cached_embedder_evicts_lru():
    inner = _CountingEmbedder()
    cache = CachedEmbedder(inner, max_entries=2)
    cache.embed("a")
    cache.embed("b")
    cache.embed("a")  # refresh a (LRU order: b, a)
    cache.embed("c")  # evicts b
    cache.embed("a")  # hit
    assert inner.calls == 3  # a, b, c only
    assert cache.stats()["hits"] == 2


def test_rag_query_embedding_cached_when_semantic_enabled(tmp_path, monkeypatch):
    from app.core import embeddings

    calls = {"n": 0}

    class _FakeOllama:
        def embed(self, text: str) -> list[float]:
            calls["n"] += 1
            return [0.1, 0.2, 0.3]

    monkeypatch.setattr(embeddings, "OllamaEmbedder", lambda settings: _FakeOllama())
    settings = Settings(
        workspace_root=str(tmp_path),
        rag_index_path=str(tmp_path / "rag.db"),
        rag_embeddings_enabled=True,
        rag_embed_model="nomic-embed-text",
    )
    rag = RagIndex(settings)
    rag.add_text("note", "title", "src", "the quick brown fox jumps over the lazy dog")
    assert calls["n"] > 0
    after_index = calls["n"]
    hits1 = rag.nearest("quick brown fox", top_k=1)
    after_first_query = calls["n"]
    hits2 = rag.nearest("quick brown fox", top_k=1)
    assert hits1 and hits2
    assert after_first_query == after_index + 1
    # Repeating the query must not re-hit the embedder.
    assert calls["n"] == after_first_query
    assert isinstance(rag.embedder, CachedEmbedder)
    assert rag.embedder.stats()["hits"] >= 1


# ---------------------------------------------------------------------------
# Phase P — executor robustness
# ---------------------------------------------------------------------------


def test_retry_succeeds_after_transient_failure(tmp_path):
    registry = ToolRegistry()
    attempts = {"n": 0}

    def flaky(_: dict) -> dict:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient blip")
        return {"result_text": "ok after retries"}

    registry.register("flaky_tool", "flaky", "low", {}, flaky)
    settings = _settings(tmp_path, tool_retry_attempts=3, tool_retry_backoff_seconds=0.01)
    executor = ActionExecutor(registry, ApprovalManager(), settings)
    result = executor.execute("run", Action(tool="flaky_tool", risk="low"))
    assert result.status == "completed"
    assert attempts["n"] == 3
    assert result.metadata["attempt"] == 3


def test_high_risk_fails_fast_no_retry(tmp_path):
    registry = ToolRegistry()
    attempts = {"n": 0}

    def always_fails(_: dict) -> dict:
        attempts["n"] += 1
        raise RuntimeError("boom")

    registry.register("boom_tool", "boom", "high", {}, always_fails)
    settings = _settings(
        tmp_path, tool_retry_attempts=3, tool_retry_backoff_seconds=0.01, approval_policy="none"
    )
    executor = ActionExecutor(registry, ApprovalManager(), settings)
    result = executor.execute("run", Action(tool="boom_tool", risk="high"))
    assert result.status == "failed"
    assert attempts["n"] == 1  # high-risk never retries
    assert "boom" in (result.error or "")


def test_timeout_does_not_retry(tmp_path):
    registry = ToolRegistry()

    def slow(_: dict) -> dict:
        time.sleep(3)
        return {"ok": True}

    registry.register("slow_tool", "slow", "medium", {}, slow, timeout_seconds=0.05)
    settings = _settings(tmp_path, tool_retry_attempts=3, approval_policy="none")
    executor = ActionExecutor(registry, ApprovalManager(), settings)
    result = executor.execute("run", Action(tool="slow_tool", risk="medium"))
    assert result.status == "failed"
    assert result.metadata.get("timed_out") is True
    assert result.metadata.get("attempt") == 1


def test_malformed_arguments_become_failed_result(tmp_path):
    registry = ToolRegistry()

    def numeric(_: dict) -> dict:
        raise ValueError("limit must be an integer")

    registry.register("needs_int", "parse int", "low", {}, numeric)
    executor = ActionExecutor(registry, ApprovalManager(), _settings(tmp_path))
    result = executor.execute("run", Action(tool="needs_int", args={"limit": "not-a-number"}, risk="low"))
    assert result.status == "failed"
    assert "limit must be an integer" in (result.error or "")
    assert result.success is False


def test_concurrent_executions_are_thread_safe(tmp_path):
    registry = ToolRegistry()

    def counter(args: dict) -> dict:
        time.sleep(0.01)
        return {"value": args.get("value")}

    registry.register("echo_value", "echo", "low", {}, counter)
    executor = ActionExecutor(registry, ApprovalManager(), _settings(tmp_path))

    errors: list[Exception] = []
    results: list[str] = []

    def worker(value: int) -> None:
        try:
            result = executor.execute("run", Action(tool="echo_value", args={"value": value}, risk="low"))
            results.append(result.output["value"])
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert sorted(int(v) for v in results) == list(range(32))


def test_approval_pending_result_carries_metadata(tmp_path):
    registry = ToolRegistry()

    def touch(_: dict) -> dict:
        return {"ok": True}

    registry.register("write_file_x", "write", "medium", {}, touch)
    executor = ActionExecutor(registry, ApprovalManager(), _settings(tmp_path))
    result = executor.execute("run", Action(tool="write_file_x", risk="medium"))
    assert result.status == "pending"
    assert result.metadata["requires_approval"] is True
    assert result.metadata["tool_category"] == "general"
    assert result.success is False
