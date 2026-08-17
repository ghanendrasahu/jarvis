from __future__ import annotations

from typing import Any

from app.config import Settings
from app.core.rag import RagIndex
from app.core.safe_paths import resolve_safe_path
from app.tools.registry import ToolRegistry


def register_rag_tools(registry: ToolRegistry, settings: Settings, rag: RagIndex) -> None:
    def remember_note(args: dict[str, Any]) -> dict[str, Any]:
        note = str(args.get("note", "")).strip()
        if not note:
            raise ValueError("Missing note text.")
        title = " ".join(note.split()[:8])
        doc_id = rag.add_text(doc_type="note", title=title, source="user", content=note)
        return {
            "saved": True,
            "note_id": doc_id,
            "title": title,
            "result_text": f"Remembered note '{title}'.",
        }

    def recall(args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("Missing recall query.")
        top_k = max(1, min(20, int(args.get("top_k", settings.rag_top_k))))
        hits = rag.nearest(query, top_k=top_k)
        result_text = "No matching knowledge found." if not hits else f"Found {len(hits)} matching item(s) from your knowledge base."
        return {
            "query": query,
            "count": len(hits),
            "results": hits,
            "result_text": result_text,
        }

    def index_file(args: dict[str, Any]) -> dict[str, Any]:
        path_value = str(args.get("path", "")).strip()
        if not path_value:
            raise ValueError("Missing path to index.")
        safe = resolve_safe_path(path_value, settings, must_exist=True)
        if safe.is_dir():
            doc_id = rag.index_notes_dir(str(safe))
            return {"indexed": doc_id, "indexed_files": doc_id, "path": str(safe)}
        doc_id = rag.add_file(str(safe), doc_type="file")
        return {"indexed": 1, "indexed_files": 1, "note_id": doc_id, "path": str(safe)}

    def index_notes(args: dict[str, Any]) -> dict[str, Any]:
        count = rag.index_notes_dir()
        return {"indexed": count, "indexed_files": count, "path": str(rag.settings.rag_notes_dir)}

    def rag_stats(args: dict[str, Any]) -> dict[str, Any]:
        return {"stats": rag.stats(), "result_text": "Knowledge base ready."}

    registry.register(
        name="remember_note",
        description="Save a personal note into the knowledge base for later retrieval.",
        risk="low",
        parameters={"note": "string"},
        handler=remember_note,
    )
    registry.register(
        name="recall",
        description="Search the personal knowledge base and return the most relevant saved notes or file excerpts.",
        risk="low",
        parameters={"query": "string", "top_k": "integer"},
        handler=recall,
    )
    registry.register(
        name="index_file",
        description="Index a text file or folder (recursively) into the knowledge base.",
        risk="medium",
        parameters={"path": "string"},
        handler=index_file,
    )
    registry.register(
        name="index_notes",
        description="Index the notes folder (data/notes) into the knowledge base.",
        risk="low",
        parameters={},
        handler=index_notes,
    )
    registry.register(
        name="knowledge_stats",
        description="Report the number of documents and chunks in the knowledge base.",
        risk="low",
        parameters={},
        handler=rag_stats,
    )
