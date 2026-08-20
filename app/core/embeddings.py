from __future__ import annotations

import hashlib
import math
import re
from collections import OrderedDict
from threading import Lock
from typing import Any, Protocol

import requests

from app.config import Settings

_DIMENSIONS = 384


class Embedder(Protocol):
    """Produces a fixed-dimension dense vector for a text span."""

    def embed(self, text: str) -> list[float]:
        ...


class OllamaEmbedder:
    """Semantic embeddings via a local Ollama server (`/api/embed`)."""

    def __init__(self, settings: Settings, model: str | None = None) -> None:
        self.settings = settings
        self.model = model or settings.rag_embed_model

    def embed(self, text: str) -> list[float]:
        response = requests.post(
            f"{self.settings.ollama_base_url.rstrip('/')}/api/embed",
            json={"model": self.model, "input": (text or "")[:8000]},
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
        embeddings = payload.get("embeddings") or []
        if not embeddings or not isinstance(embeddings[0], list):
            raise RuntimeError("Ollama embed returned no embeddings.")
        return [float(value) for value in embeddings[0]]


def _hash_token(token: str) -> int:
    return int.from_bytes(hashlib.md5(token.encode("utf-8", errors="ignore")).digest()[:6], "big")


class CachedEmbedder:
    """Thread-safe, bounded LRU cache over a slow embedder.

    Identical text (e.g. repeated retrieval queries) is embedded once and served
    from memory afterwards, eliminating redundant network/LLM calls. The cache is
    content-addressed by SHA-256 and capped to avoid unbounded growth.
    """

    def __init__(self, inner: Embedder, max_entries: int = 1024) -> None:
        self._inner = inner
        self._max_entries = max(1, max_entries)
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = Lock()
        self._hits = 0
        self._misses = 0

    def embed(self, text: str) -> list[float]:
        key = hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()
        with self._lock:
            cached = self._cache.pop(key, None)
            if cached is not None:
                self._cache[key] = cached
                self._hits += 1
                return cached
            self._misses += 1
        vector = self._inner.embed(text)
        with self._lock:
            self._cache[key] = vector
            if len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
            return vector

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"hits": self._hits, "misses": self._misses, "size": len(self._cache)}


class HashedEmbedder:
    """Deterministic, network-free bag-of-shingles embedder.

    Produces a fixed-dimension vector whose cosine similarity approximates
    lexical overlap. Used for offline retrieval and in tests.
    """

    def __init__(self, dimensions: int = _DIMENSIONS) -> None:
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        if not tokens:
            return vector
        weight = 1.0 / math.sqrt(len(tokens))
        for token in tokens:
            vector[_hash_token(token) % self.dimensions] += weight
        return vector

    def embed(self, text: str) -> list[float]:
        return self._vector(text)


def create_embedder(settings: Settings) -> Embedder:
    if settings.rag_embeddings_enabled:
        return CachedEmbedder(OllamaEmbedder(settings))
    return HashedEmbedder()
