from __future__ import annotations

from typing import Any

from app.config import Settings
from app.core.embeddings import HashedEmbedder
from app.core.rag import RagIndex, _chunk_text
from app.core.retrieval_planner import RetrievalPlanner
from app.models.schemas import Plan
from app.tools.rag_tools import register_rag_tools
from app.tools.registry import ToolRegistry


def _settings(tmp_path, **overrides: Any) -> Settings:
    base = Settings(
        workspace_root=".",
        rag_index_path=str(tmp_path / "rag.db"),
        rag_notes_dir=str(tmp_path / "notes"),
        rag_embeddings_enabled=False,
    )
    return base.model_copy(update=overrides)


def test_chunk_text_splits_with_overlap():
    text = " ".join(["word" + str(i) for i in range(200)])
    chunks = _chunk_text(text, chunk_chars=120, overlap_chars=20)
    assert len(chunks) > 1
    assert all(chunk for chunk in chunks)
    assert chunks[0] != chunks[1]


def test_hashed_embedder_is_deterministic_and_bounded():
    embedder = HashedEmbedder()
    a = embedder.embed("the quick brown fox")
    b = embedder.embed("the quick brown fox")
    c = embedder.embed("unrelated topic entirely")
    assert a == b
    assert len(a) == 384
    assert a != c
    assert all(isinstance(value, float) for value in a)


def test_rag_index_add_and_nearest(tmp_path):
    rag = RagIndex(_settings(tmp_path))
    rag.add_text("note", "project goals", "user", "The Jarvis project aims to ship a personal AI operating system with RAG.")
    rag.add_text("note", "holiday plans", "user", "Going to Goa next month, need to book beach house.")

    hits = rag.nearest("what is the jarvis project about?", top_k=1)
    assert hits
    assert hits[0]["title"] == "project goals"
    assert hits[0]["score"] > 0.0

    hits = rag.nearest("totally unrelated query about quantum physics", top_k=1)
    assert hits[0]["title"] in {"project goals", "holiday plans"}

    stats = rag.stats()
    assert stats["documents"] == 2
    assert stats["chunks"] >= 2

    assert rag.remove_doc(hits[0]["doc_id"]) is True
    assert rag.stats()["documents"] == 1


def test_rag_index_build_context_format(tmp_path):
    rag = RagIndex(_settings(tmp_path))
    rag.add_text("note", "my api keys", "user", "the vault holds password: p@ssw0rd-789 and api_key=sk-live-42")
    context = rag.build_context("where do I store secrets?")
    assert "RETRIEVED_CONTEXT" in context
    assert "sk-live-42" not in context
    assert "p@ssw0rd-789" not in context
    assert "my api keys" in context


def test_rag_index_index_notes_dir(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "readme.md").write_text("# Project notes\nImportant detail about architecture.", encoding="utf-8")
    (notes / "draft.txt").write_text("draft content about the new feature.", encoding="utf-8")
    (notes / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nbinary")
    rag = RagIndex(_settings(tmp_path))
    indexed = rag.index_notes_dir(str(notes))
    assert indexed == 2
    stats = rag.stats()
    assert stats["documents"] == 2
    hits = rag.nearest("new feature draft", top_k=2)
    assert any(hit["title"] == "draft.txt" for hit in hits)


def test_rag_tools_remember_and_recall(tmp_path):
    rag = RagIndex(_settings(tmp_path))
    registry = ToolRegistry()
    register_rag_tools(registry, _settings(tmp_path), rag)

    saved = registry.execute("remember_note", {"note": "Remember to renew the domain on the 15th."})
    assert saved["saved"] is True

    hits = registry.execute("recall", {"query": "when do I renew the domain?"})
    assert hits["count"] >= 1
    assert "renew the domain" in hits["results"][0]["content"]

    stats = registry.execute("knowledge_stats", {})
    assert stats["stats"]["documents"] == 1


def test_retrieval_planner_injects_context(tmp_path):
    rag = RagIndex(_settings(tmp_path))
    rag.add_text("note", "build stack", "user", "The build stack uses Python 3.11 and Windows.")

    captured: dict = {}

    class FakePlanner:
        def plan(self, command, tool_catalog, history, agent_context="", tool_history=None, known_facts=None, rag_context=""):
            captured["rag_context"] = rag_context
            return Plan(summary="ok", actions=[], final_response="done")

    wrapped = RetrievalPlanner(FakePlanner(), rag)  # type: ignore[arg-type]
    wrapped.plan("which python version does the build use?", [], [])
    assert "RETRIEVED_CONTEXT" in captured["rag_context"]
    assert "Python 3.11" in captured["rag_context"]
    assert "build stack" in captured["rag_context"]


def test_retrieval_planner_passthrough_without_rag():
    captured: dict = {}

    class FakePlanner:
        def plan(self, command, tool_catalog, history, agent_context="", tool_history=None, known_facts=None, rag_context=""):
            captured["rag_context"] = rag_context
            return Plan(summary="ok", actions=[], final_response="done")

    wrapped = RetrievalPlanner(FakePlanner(), None)  # type: ignore[arg-type]
    wrapped.plan("anything", [], [])
    assert captured["rag_context"] == ""
