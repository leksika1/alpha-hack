"""InsightGenerator selection by INSIGHT_GENERATOR (stub | remote | fake)."""

import httpx

from app.core.config import Settings
from app.integrations.ml_client import HttpInsightGenerator


def get_generator(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> HttpInsightGenerator:
    """`stub` and `remote` speak the same HTTP contract and differ only by ML_SERVICE_URL.
    `fake` is reserved for tests, which inject an in-process transport to ml-stub."""
    name = "ml-stub" if settings.insight_generator in {"stub", "fake"} else "ml-service"
    gen = HttpInsightGenerator(
        settings.ml_service_url,
        settings.ml_service_token.get_secret_value(),
        timeout_s=settings.ml_request_timeout_s,
        max_retries=settings.ml_max_retries,
        transport=transport,
    )
    gen.name = name
    return gen
