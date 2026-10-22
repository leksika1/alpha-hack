"""Provider interfaces (ports). Implementations live in app/integrations:

* ``fake``     — deterministic, for tests;
* ``baseline`` — our rule-based implementation (default for scoring/tagging);
* ``remote``   — ML service over HTTP (contracts/ml-service.openapi.yaml).

The domain layer depends only on these protocols, never on concrete providers.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.domain.contracts.insight import Insight, Role


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns texts into fixed-size vectors (dimension = EMBEDDING_DIM)."""

    name: str
    dim: int

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True, slots=True)
class ScoreResult:
    score: float
    breakdown: dict[str, float]
    matched_features: list[str] = field(default_factory=list)
    scorer: str = "baseline@1"


@runtime_checkable
class RelevanceScorer(Protocol):
    """Scores event x company candidates; must return a per-component breakdown."""

    name: str

    async def score(
        self, company_context: dict[str, Any], event: dict[str, Any], role: Role
    ) -> ScoreResult: ...


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    request_id: str
    company_id: str
    event_id: str
    role: Role
    run_id: str
    candidate: ScoreResult | None = None
    allow_llm: bool = True
    tools: dict[str, Any] | None = None  # contracts/ml-service.openapi.yaml#/components/schemas/ToolsAccess


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    status: str  # completed | skipped | failed
    insight: Insight | None = None
    raw_insight: dict[str, Any] | None = None
    skip_reason: str | None = None
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


@runtime_checkable
class InsightGenerator(Protocol):
    """Produces an insight for (company, event, role) following contracts/insight.schema.json."""

    name: str

    async def generate(self, request: GenerationRequest) -> GenerationOutcome: ...


@dataclass(frozen=True, slots=True)
class OcrPage:
    page: int
    text: str
    confidence: float | None
    method: str  # text_layer | ocr_tesseract | ocr_vlm | fake


@runtime_checkable
class OcrProvider(Protocol):
    """Extracts text from a rendered PDF page image."""

    name: str

    async def recognize(self, image_png: bytes, page: int) -> OcrPage: ...
