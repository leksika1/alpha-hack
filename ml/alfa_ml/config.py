"""Service settings from environment variables (12-factor; secrets never in code)."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    ml_service_token: str = ""
    log_level: str = "INFO"
    contracts_dir: Path | None = None

    # LLM: all generative calls go through the backend LLM Gateway (tools.llm_base_url of each request).
    ml_llm_enabled: bool = True
    ml_llm_planner: bool = True  # LLM planner on top of the rule plan (cheap model, small answer)
    ml_llm_critic: bool = True  # LLM critic after deterministic checks
    ml_llm_timeout_s: float = 45.0  # one call; the run deadline (backend: 60 s) caps it further
    ml_llm_no_think: bool = True  # switch the reasoning of Qwen/GLM off where the provider supports it
    # Aliases from GET /internal/llm/models; unknown aliases are resolved by model family (see llm.py).
    ml_model_planner: str = "gemma-4-26b-a4b"
    ml_model_analyst: str = "qwen3.6-35b-a3b"
    ml_model_critic: str = "glm-5.3-flash"
    ml_model_chat: str = "qwen3.6-35b-a3b"

    # Agent budget per run (backend generate timeout is 60 s).
    ml_max_iterations: int = Field(default=1, ge=0, le=4)
    ml_deadline_margin_ms: int = 6000
    ml_skip_threshold: float = 0.35
    # budget guard: the LLM writes only insights that matter (pre-score >= this); the rest is rules@1
    ml_llm_min_score: float = 0.5
    ml_max_news: int = 5
    ml_max_analytics_hits: int = 4


@lru_cache
def get_settings() -> Settings:
    contracts = os.environ.get("CONTRACTS_DIR")
    return Settings(
        ml_service_token=_env("ML_SERVICE_TOKEN", ""),
        log_level=_env("LOG_LEVEL", "INFO"),
        contracts_dir=Path(contracts) if contracts else None,
        ml_llm_enabled=_flag("ML_LLM_ENABLED", True),
        ml_llm_planner=_flag("ML_LLM_PLANNER", True),
        ml_llm_critic=_flag("ML_LLM_CRITIC", True),
        ml_llm_timeout_s=float(_env("ML_LLM_TIMEOUT_S", "45")),
        ml_llm_no_think=_flag("ML_LLM_NO_THINK", True),
        ml_model_planner=_env("ML_MODEL_PLANNER", "gemma-4-26b-a4b"),
        ml_model_analyst=_env("ML_MODEL_ANALYST", "qwen3.6-35b-a3b"),
        ml_model_critic=_env("ML_MODEL_CRITIC", "glm-5.3-flash"),
        ml_model_chat=_env("ML_MODEL_CHAT", "qwen3.6-35b-a3b"),
        ml_max_iterations=int(_env("ML_MAX_ITERATIONS", "1")),
        ml_deadline_margin_ms=int(_env("ML_DEADLINE_MARGIN_MS", "6000")),
        ml_skip_threshold=float(_env("ML_SKIP_THRESHOLD", "0.35")),
        ml_llm_min_score=float(_env("ML_LLM_MIN_SCORE", "0.5")),
        ml_max_news=int(_env("ML_MAX_NEWS", "5")),
        ml_max_analytics_hits=int(_env("ML_MAX_ANALYTICS_HITS", "4")),
    )
