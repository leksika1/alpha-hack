import math
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
import respx

from app.core.config import Settings
from app.domain.vectors import EmbeddingResponseError, validate_vectors
from app.integrations.embeddings import (
    EmbeddingUnavailableError,
    LocalEmbedding,
    RemoteEmbedding,
    get_embedder,
    to_pgvector,
)


def test_real_vectors_are_normalized_without_overflow() -> None:
    first, second = validate_vectors([[3, 4], [1e200, 1e200]], 2, 2)
    assert first == pytest.approx([0.6, 0.8])
    assert math.hypot(*second) == pytest.approx(1)


@pytest.mark.parametrize("vectors", [[], [[1]], [[0, 0]], [[math.nan, 1]], [[math.inf, 1]], [["x", 1]]])
def test_invalid_real_vectors_are_rejected(vectors: Any) -> None:
    with pytest.raises(EmbeddingResponseError):
        validate_vectors(vectors, 1, 2)


@pytest.mark.parametrize("vector", [[], [math.nan], [math.inf], [-math.inf]])
def test_invalid_pgvector_literals_are_rejected(vector: list[float]) -> None:
    with pytest.raises(EmbeddingResponseError):
        to_pgvector(vector)


async def test_remote_restores_response_order_and_sends_authentication() -> None:
    with respx.mock as mock:
        request = mock.post("https://embedding.test/v1/embeddings").respond(
            200, json={"data": [{"index": 1, "embedding": [0, 2]}, {"index": 0, "embedding": [3, 0]}]}
        )
        vectors = await RemoteEmbedding(
            "https://embedding.test/v1", "model", 2, "test-secret"
        ).embed_documents(["first", "second"])
    assert vectors == [[1, 0], [0, 1]]
    assert request.calls.last.request.headers["authorization"] == "Bearer test-secret"


@pytest.mark.parametrize(
    "data",
    [
        [],
        [{"index": 0, "embedding": [1, 0]}, {"index": 0, "embedding": [0, 1]}],
        [{"index": -1, "embedding": [1, 0]}, {"index": 1, "embedding": [0, 1]}],
        [{"index": False, "embedding": [1, 0]}, {"index": 1, "embedding": [0, 1]}],
        [{"index": 0, "embedding": [1]}, {"index": 1, "embedding": [0, 1]}],
        [{"index": 0, "embedding": [0, 0]}, {"index": 1, "embedding": [0, 1]}],
        [{"index": 0, "embedding": "invalid"}, {"index": 1, "embedding": [0, 1]}],
        [{"embedding": [1, 0]}, {"index": 1, "embedding": [0, 1]}],
    ],
)
async def test_malformed_remote_batches_are_rejected(data: Any) -> None:
    with respx.mock as mock:
        mock.post("https://embedding.test/embeddings").respond(200, json={"data": data})
        with pytest.raises(EmbeddingResponseError):
            await RemoteEmbedding("https://embedding.test", "model", 2).embed_documents(["a", "b"])


async def test_remote_http_errors_propagate() -> None:
    with respx.mock as mock:
        mock.post("https://embedding.test/embeddings").respond(503)
        with pytest.raises(httpx.HTTPStatusError):
            await RemoteEmbedding("https://embedding.test", "model", 2).embed_query("query")


async def test_local_failure_never_uses_fake_vectors_and_has_a_retry_cooldown(monkeypatch: Any) -> None:
    import fastembed

    load = Mock(side_effect=RuntimeError("model download failed"))
    monkeypatch.setattr(fastembed, "TextEmbedding", load)
    embedder = LocalEmbedding("real-model", 384)
    for _ in range(2):
        with pytest.raises(EmbeddingUnavailableError):
            await embedder.embed_query("query")
        assert embedder.name == "real-model"
    load.assert_called_once()


def test_configured_remote_key_is_used() -> None:
    settings = Settings(
        _env_file=None,
        embedding_provider="remote",
        embedding_remote_url="https://embedding.test",
        embedding_remote_key="configured-secret",
    )
    embedder = get_embedder(settings)
    assert isinstance(embedder, RemoteEmbedding)
    assert embedder.headers["Authorization"] == "Bearer configured-secret"
