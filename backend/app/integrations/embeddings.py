"""EmbeddingProvider implementations (ADR-006): fake | local | remote, selected by EMBEDDING_PROVIDER.

* fake   — deterministic signed feature hashing of word stems (tests, offline e2e): lexically similar
           texts get similar vectors, so vector search behaves sensibly without a model;
* local  — fastembed (ONNX, no torch) with the model set in EMBEDDING_MODEL (chosen by the ML team);
           downloaded on first use and cached in FASTEMBED_CACHE_PATH;
* remote — OpenAI-compatible `/embeddings` endpoint (e.g. a TEI server of the ML team).
"""

import asyncio
import hashlib
import math
import time
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.clustering import stems
from app.domain.vectors import EmbeddingResponseError, validate_vectors
from app.repositories.models import EMBEDDING_DIM

BATCH = 64
log = get_logger(__name__)


class EmbeddingUnavailableError(RuntimeError):
    pass


def to_pgvector(vec: Sequence[float]) -> str:
    """Text literal for `$n::vector` parameters (no asyncpg codec registration needed)."""
    if not vec or not all(math.isfinite(x) for x in vec):
        raise EmbeddingResponseError("Cannot store an empty or non-finite vector")
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeEmbedding:
    name = "fake-hash@1"

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in stems(text):
            h = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
            vec[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        return _normalize(vec)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class LocalEmbedding:
    """fastembed model, downloaded on first use; failures never substitute test vectors."""

    def __init__(self, model: str, dim: int) -> None:
        self.model_name = model
        self.dim = dim
        self._model: Any = None
        self._retry_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self.model_name

    async def _load(self) -> Any:
        async with self._lock:
            if self._model is None:
                if time.monotonic() < self._retry_at:
                    raise EmbeddingUnavailableError("Local embedding model is temporarily unavailable")
                try:
                    from fastembed import TextEmbedding

                    self._model = await asyncio.to_thread(TextEmbedding, self.model_name)
                except Exception as exc:
                    self._retry_at = time.monotonic() + 60
                    log.warning(
                        "embedding_model_unavailable", model=self.model_name, error=type(exc).__name__
                    )
                    raise EmbeddingUnavailableError("Local embedding model could not be loaded") from exc
        return self._model

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        model = await self._load()
        vectors = await asyncio.to_thread(
            lambda: [v.tolist() for v in model.embed(list(texts), batch_size=BATCH)]
        )
        return validate_vectors(vectors, len(texts), self.dim)

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]


class RemoteEmbedding:
    def __init__(self, url: str, model: str, dim: int, api_key: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.name = f"remote:{model}"
        self.dim = dim
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60, headers=self.headers) as client:
            for i in range(0, len(texts), BATCH):
                resp = await client.post(
                    f"{self.url}/embeddings", json={"model": self.model, "input": list(texts[i : i + BATCH])}
                )
                resp.raise_for_status()
                count = len(texts[i : i + BATCH])
                try:
                    items = resp.json()["data"]
                    if not isinstance(items, list) or len(items) != count:
                        raise EmbeddingResponseError("Embedding response count does not match input count")
                    indices = [item["index"] for item in items]
                    if any(type(index) is not int for index in indices) or sorted(indices) != list(
                        range(count)
                    ):
                        raise EmbeddingResponseError("Embedding response indices are missing or duplicated")
                    ordered = sorted(items, key=lambda item: item["index"])
                    vectors = [item["embedding"] for item in ordered]
                    if any(not isinstance(vector, list) for vector in vectors):
                        raise EmbeddingResponseError("Embedding response contains an invalid vector")
                    out.extend(validate_vectors(vectors, count, self.dim))
                except (KeyError, TypeError, ValueError) as exc:
                    raise EmbeddingResponseError("Invalid embedding response") from exc
        return out

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]


Embedder = FakeEmbedding | LocalEmbedding | RemoteEmbedding


@lru_cache
def _cached(provider: str, model: str | None, dim: int, url: str | None, api_key: str | None) -> Embedder:
    if dim != EMBEDDING_DIM:
        raise ValueError(f"EMBEDDING_DIM={dim} but the schema stores vector({EMBEDDING_DIM}); see ADR-006")
    if provider == "fake":
        return FakeEmbedding(dim)
    if not model:
        raise ValueError(
            f"EMBEDDING_MODEL is required for EMBEDDING_PROVIDER={provider} (chosen by the ML team)"
        )
    if provider == "local":
        return LocalEmbedding(model, dim)
    if provider == "remote":
        if not url:
            raise ValueError("EMBEDDING_REMOTE_URL is required for EMBEDDING_PROVIDER=remote")
        return RemoteEmbedding(url, model, dim, api_key)
    raise ValueError(f"unknown EMBEDDING_PROVIDER {provider!r}")


def get_embedder(settings: Settings) -> Embedder:
    return _cached(
        settings.embedding_provider,
        settings.embedding_model,
        settings.embedding_dim,
        settings.embedding_remote_url,
        settings.embedding_remote_key.get_secret_value() if settings.embedding_remote_key else None,
    )
