import sys
from pathlib import Path

import httpx
import pytest

from alfa_ml import llm as llm_mod
from alfa_ml import tools_client
from alfa_ml.config import get_settings
from tests.fakes import LLMScript, make_backend

BACKEND = Path(__file__).resolve().parents[2] / "backend"


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ML_SERVICE_TOKEN", "")
    get_settings.cache_clear()
    llm_mod._MODELS_CACHE.clear()
    llm_mod.breaker_reset()


@pytest.fixture
def llm_script() -> LLMScript:
    return LLMScript()


@pytest.fixture
def backend(llm_script: LLMScript):  # type: ignore[no-untyped-def]
    app, steps = make_backend(llm_script)
    transport = httpx.ASGITransport(app=app)
    tools_client.TRANSPORT = transport
    llm_mod.TRANSPORT = transport
    yield steps
    tools_client.TRANSPORT = None
    llm_mod.TRANSPORT = None


@pytest.fixture
def backend_validator():  # type: ignore[no-untyped-def]
    """The backend's own validator (backend/app/domain) — the gate an insight must pass to reach the feed."""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    try:
        from app.domain.insight_validator import ValidationContext, validate_insight
    except Exception as exc:
        pytest.skip(f"backend domain not importable: {exc}")

    def check(
        insight: dict, ctx_features: dict, chunk_ids: set[str], products: set[str]
    ) -> list[dict[str, str]]:
        res = validate_insight(
            insight,
            ValidationContext(
                company_id=insight["company_id"],
                event_id=insight["event_id"],
                feature_paths=frozenset(ctx_features),
                known_products=frozenset(products),
                chunk_exists=lambda c: c in chunk_ids,
            ),
        )
        return res.reasons()

    return check
