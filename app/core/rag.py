from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Any

from app.config import Settings
from app.core.embeddings import HashedEmbedder, create_embedder
from app.core.logging import get_logger
from app.core.redact import redact_text

logger = get_logger("rag")

_TEXT_EXTENSIONS = {".txt", ".md", ".rst", ".py", ".json", ".yaml", ".yml", ".csv", ".log"}
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_CHUNKS_PER_DOC = 500

_CHUNK_QUERY = """
SELECT c.doc_id, d.doc_type, d.title, d.source, c.chunk_index, c.content,
       c.semantic_json, c.lexical_json
FROM chunks c
JOIN documents d ON d.id = c.doc_id
"""


def _chunk_text(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_chars, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks[:_MAX_CHUNKS_PER_DOC]


def _read_text_file(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            logger.info("skipping large file: %s", path)
            return None
        raw = path.read_bytes()
        if b"\x00" in raw[:4096]:
            return None
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None


def _l2_norm(values: list[float]) -> float:
    return (sum(value * value for value in values) or 0.0) ** 0.5


class RagIndex:
    """Local SQLite knowledge base with dense-vector retrieval.

    Documents are chunked, embedded (semantic via Ollama when available,
    lexical bag-of-shingles otherwise), and stored alongside their vectors.
    Retrieval is cosine similarity over the best matching embedding kind.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database_path = settings.rag_index_path
        self.chunk_chars = settings.rag_chunk_chars
        self.overlap_chars = settings.rag_chunk_overlap_chars
        self.embedder = create_embedder(settings)
        self._lexical = HashedEmbedder()
        self._semantic_active: bool | None = None
        self._lock = Lock()
        Path(self.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._connect()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    semantic_json TEXT,
                    lexical_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_docs_identity ON documents(doc_type, title, source)")
            conn.commit()

    def _embed_semantic(self, text: str) -> list[float] | None:
        if self._semantic_active is False:
            return None
        try:
            vector = self.embedder.embed(text)
            self._semantic_active = True
            return vector
        except Exception:
            self._semantic_active = False
            logger.warning("semantic embeddings unavailable; using lexical retrieval")
            return None

    def add_text(self, doc_type: str, title: str, source: str, content: str) -> int:
        """Insert (or update) a document and re-embed its chunks. Returns the doc id."""
        safe_content = redact_text(content)
        chunks = _chunk_text(safe_content, self.chunk_chars, self.overlap_chars)
        if not chunks:
            raise ValueError("Document content is empty.")
        title = title.strip()[:500] or "Untitled"
        source = source.strip()[:1000]
        created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            conn = self._conn
            cursor = conn.execute(
                "SELECT id FROM documents WHERE doc_type = ? AND title = ? AND source = ?",
                (doc_type, title, source),
            )
            row = cursor.fetchone()
            if row:
                doc_id = int(row["id"])
                conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
                conn.execute("UPDATE documents SET created_at = ? WHERE id = ?", (created_at, doc_id))
            else:
                cursor = conn.execute(
                    "INSERT INTO documents (doc_type, title, source, created_at) VALUES (?, ?, ?, ?)",
                    (doc_type, title, source, created_at),
                )
                doc_id = int(cursor.lastrowid or 0)
            for index, chunk in enumerate(chunks):
                lexical = self._lexical.embed(chunk)
                semantic = self._embed_semantic(chunk)
                conn.execute(
                    "INSERT INTO chunks (doc_id, chunk_index, content, semantic_json, lexical_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        doc_id,
                        index,
                        chunk,
                        json.dumps(semantic) if semantic is not None else None,
                        json.dumps(lexical),
                    ),
                )
            conn.commit()
        return doc_id

    def add_file(self, path: str, doc_type: str = "file") -> int:
        file_path = Path(path).expanduser()
        content = _read_text_file(file_path)
        if content is None:
            raise ValueError(f"Cannot read file (binary or missing): {file_path}")
        return self.add_text(doc_type=doc_type, title=file_path.name, source=str(file_path), content=content)

    def index_notes_dir(self, directory: str | None = None) -> int:
        """Index text files under a directory (recursively). Returns files indexed."""
        root = Path(directory or self.settings.rag_notes_dir).expanduser()
        if not root.exists():
            root.mkdir(parents=True, exist_ok=True)
            return 0
        indexed = 0
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _TEXT_EXTENSIONS:
                continue
            if any(part.startswith(".") for part in path.parts):
                continue
            try:
                self.add_file(str(path), doc_type="file")
                indexed += 1
            except (ValueError, OSError) as exc:
                logger.warning("skipping %s: %s", path, exc)
        return indexed

    def remove_doc(self, doc_id: int) -> bool:
        with self._lock:
            self._conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            cursor = self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chunks")
            self._conn.execute("DELETE FROM documents")
            self._conn.commit()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            documents = int(self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
            chunks = int(self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        return {"documents": documents, "chunks": chunks, "index_path": self.database_path}

    def nearest(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Return the top-k most relevant chunks for a query, most relevant first."""
        if top_k <= 0:
            return []
        query_lexical = self._lexical.embed(query)
        query_semantic = self._embed_semantic(query)
        with self._lock:
            rows = self._conn.execute(_CHUNK_QUERY).fetchall()
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            semantic_json = row["semantic_json"]
            if query_semantic is not None and semantic_json is not None:
                chunk_vector = json.loads(semantic_json)
                query_vector = query_semantic
            else:
                chunk_vector = json.loads(row["lexical_json"])
                query_vector = query_lexical
            chunk_norm = _l2_norm(chunk_vector)
            query_norm = _l2_norm(query_vector)
            if chunk_norm == 0.0 or query_norm == 0.0:
                continue
            dot = sum(a * b for a, b in zip(chunk_vector, query_vector, strict=False))
            scored.append((dot / (chunk_norm * query_norm), row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {
                "doc_id": int(row["doc_id"]),
                "doc_type": row["doc_type"],
                "title": row["title"],
                "source": row["source"],
                "content": redact_text(row["content"]),
                "score": round(score, 4),
            }
            for score, row in scored[:top_k]
        ]

    def build_context(self, query: str, top_k: int | None = None) -> str:
        """Formatted retrieval context for prompt injection; empty when nothing relevant."""
        if not query:
            return ""
        hits = self.nearest(query, top_k or self.settings.rag_top_k)
        if not hits:
            return ""
        lines = ["RETRIEVED_CONTEXT (personal knowledge base):"]
        for index, hit in enumerate(hits, start=1):
            source = f" ({hit['source']})" if hit["source"] else ""
            snippet = hit["content"].replace("\n", " ")[:300]
            lines.append(f"{index}. [{hit['title']}]{source}: {snippet}")
        return "\n".join(lines) + "\n\n"
