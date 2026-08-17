from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

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
        return OllamaEmbedder(settings)
    return HashedEmbedder()
