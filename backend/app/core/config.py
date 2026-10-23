"""Application settings. All secrets come from environment variables (see .env.example)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Generative LLMs allowed by the case (appendix 3). Alias -> display name.
ALLOWED_LLM_MODELS: dict[str, str] = {
    "gemma-4-26b-a4b": "Gemma 4 26B A4B",
    "glm-5.3-flash": "GLM-5.3-Flash",
    "qwen3.6-35b-a3b": "Qwen3.6 35B A3B",
    "deepseek-v4.1-flash": "DeepSeek-V4.1-Flash",
    "qwen3-vl-30b-a3b-instruct": "Qwen3-VL-30B-A3B-Instruct",
    "kimi-k2.6": "Kimi-K2.6",
}


class Settings(BaseSettings):
    # empty variables (compose `${VAR:-}`) mean "use the default"
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )

    # Storage
    database_url: str = "postgresql+asyncpg://alfa:alfa@localhost:5432/alfa"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_public_endpoint_url: str | None = None
    s3_access_key: str = "minioadmin"
    s3_secret_key: SecretStr = SecretStr("minioadmin")
    s3_region: str = "us-east-1"
    s3_bucket_analytics: str = "analytics"
    s3_bucket_news_raw: str = "news-raw"
    s3_bucket_snapshots: str = "snapshots"

    # Auth
    jwt_secret: SecretStr = SecretStr("dev-only-jwt-secret-change-me-in-env-0123456789")
    jwt_access_ttl_min: int = 30
    jwt_refresh_ttl_days: int = 7
    service_token: SecretStr = SecretStr("change-me-service-token")

    # Base URL under which ML reaches this backend (/internal/tools, /internal/llm)
    internal_api_url: str = "http://localhost:8000"

    # ML / providers
    ml_service_url: str = "http://localhost:8100"
    ml_request_timeout_s: float = 60.0
    ml_max_retries: int = 2
    ml_service_token: SecretStr = SecretStr("change-me-ml-token")
    insight_generator: Literal["stub", "remote", "fake"] = "stub"
    embedding_provider: Literal["fake", "local", "remote"] = "fake"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dim: int = 384
    embedding_remote_url: str | None = None
    embedding_remote_key: SecretStr | None = None
    scorer_provider: Literal["baseline", "remote", "fake"] = "baseline"
    ocr_provider: Literal["tesseract", "vlm", "fake"] = "tesseract"
    analytics_max_bytes: int = Field(default=50_000_000, ge=1024, le=200_000_000)
    analytics_max_pages: int = Field(default=150, ge=1, le=1000)
    analytics_max_render_pixels: int = Field(default=25_000_000, ge=1_000_000, le=100_000_000)

    # LLM Gateway (ADR-028)
    llm_provider: Literal["openai", "fake"] = "openai"  # fake: deterministic answers, tests/e2e only
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model_map: dict[str, str] = Field(default_factory=dict)  # alias -> provider model id
    # alias -> [RUB per 1M input tokens, RUB per 1M output tokens]; models without a price use the default
    llm_prices: dict[str, tuple[float, float]] = Field(default_factory=dict)
    llm_default_price_rub_per_1m: tuple[float, float] = (100.0, 300.0)  # conservative until tariffs are known
    llm_daily_budget_rub: float = 450.0
    llm_daily_token_limit: int = 3_000_000
    llm_budget_utc_offset_h: int = 3  # the "day" of the daily budget: Moscow time (UTC+3, no DST)
    llm_request_timeout_s: float = 60.0
    llm_max_retries: int = 1
    llm_default_max_tokens: int = 1024  # used for the budget reservation when max_tokens is not given
    llm_cache_ttl_h: int = 168

    # Data / pipelines
    data_dir: str = "data/synthetic"
    eligible_client_types: list[str] = Field(default_factory=lambda: ["CLIENT"])
    news_fixtures_enabled: bool = False
    news_poll_interval_min: int = 15
    news_user_agent: str = "AlfaFutureBot/0.1 (+hackathon prototype; contact: see README)"
    fixture_news_path: str = "data/synthetic/news/fixture_news.json"
    news_sources_path: str = "data/reference/news_sources.json"
    news_live_sources_enabled: bool = True  # false in e2e/offline: only the fixture source is polled
    news_fetch_concurrency: int = 4
    news_min_interval_s: float = 2.0  # per host
    news_failures_to_down: int = 3

    # Matching / generation
    match_min_score: float = 0.2
    match_max_per_company: int = 50
    match_scope: Literal["users", "all"] = "users"  # all: candidates for the whole client base (ADR-029)
    insight_ttl_days: int = 14
    tool_calls_per_run: int = 120

    # Rate limits (Redis token bucket per caller)
    rate_limit_enabled: bool = True
    rate_limit_public_per_min: int = 120
    rate_limit_internal_per_min: int = 1200


# Development defaults of secrets (code and docker-compose/.env.example); refused in APP_ENV=prod.
DEV_SECRETS: dict[str, set[str]] = {
    "jwt_secret": {"dev-only-jwt-secret-change-me-in-env-0123456789"},
    "service_token": {"change-me-service-token", "dev-only-service-token"},
    "ml_service_token": {"change-me-ml-token", "dev-only-ml-token"},
}


def insecure_defaults(settings: Settings) -> list[str]:
    return [name for name, dev in DEV_SECRETS.items() if getattr(settings, name).get_secret_value() in dev]


@lru_cache
def get_settings() -> Settings:
    return Settings()
