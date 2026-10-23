"""Validate real embedding batches before they can reach the search index."""

import math
from collections.abc import Sequence


class EmbeddingResponseError(ValueError):
    pass


def validate_vectors(vectors: Sequence[Sequence[float]], count: int, dim: int) -> list[list[float]]:
    if len(vectors) != count:
        raise EmbeddingResponseError("Embedding response count does not match input count")
    result = []
    for vector in vectors:
        if len(vector) != dim:
            raise EmbeddingResponseError("Embedding response dimension does not match the index")
        try:
            values = [float(x) for x in vector]
        except (TypeError, ValueError, OverflowError) as exc:
            raise EmbeddingResponseError("Embedding response contains a non-numeric value") from exc
        norm = math.hypot(*values)
        if not math.isfinite(norm) or norm == 0:
            raise EmbeddingResponseError("Embedding response must be finite and nonzero")
        result.append([x / norm for x in values])
    return result
