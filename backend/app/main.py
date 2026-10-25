"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health
from app.api.internal import llm as internal_llm
from app.api.internal import tools as internal_tools
from app.api.v1 import router as v1_router
from app.core.config import get_settings, insecure_defaults
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.pg import create_pool
from app.core.ratelimit import RateLimiter, RateLimitMiddleware

DESCRIPTION = """
Бэкенд сервиса персонализированных бизнес-инсайтов «Альфа-Будущее» для интернет-банка «Альфа-Бизнес».

* `/api/v1` — публичное API для фронтенда (JWT: `POST /api/v1/auth/login`, демо-учётки — в README).
* Ошибки — RFC 7807 `application/problem+json` (`trace_id` для поиска в логах). Пагинация — курсором.
* Внутренние API (`/internal/tools`, `/internal/llm`) описаны в `contracts/`.

Материалы носят информационный характер и не являются инвестиционной рекомендацией.
"""

log = get_logger(__name__)


async def _queue_generation(company_ids: list[str]) -> None:
    """Default enqueue: Taskiq task; failures are logged (the feed still serves existing insights)."""
    from app.workers.tasks import enqueue_generation

    try:
        await enqueue_generation(company_ids)
    except Exception:
        log.exception("enqueue_generation_failed", company_ids=company_ids)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    try:
        app.state.pool = await create_pool(settings.database_url)
    except Exception:
        app.state.pool = None
        log.exception("db_pool_unavailable")
    from app.workers.broker import broker

    broker_started = False
    try:
        await broker.startup()
        broker_started = True
    except Exception:
        log.exception("broker_unavailable")
    if not hasattr(app.state, "enqueue"):
        app.state.enqueue = _queue_generation
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.redis_url, socket_timeout=0.5, socket_connect_timeout=0.5)
    app.state.rate_limiter = RateLimiter(redis_client)
    try:
        yield
    finally:
        await redis_client.aclose()
        if broker_started:
            await broker.shutdown()
        if app.state.pool is not None:
            await app.state.pool.close()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    if settings.app_env == "prod" and (weak := insecure_defaults(settings)):
        raise RuntimeError(f"APP_ENV=prod with development secrets: {', '.join(weak)} (set them in env)")
    app = FastAPI(
        title="Alfa Future — Business Insights API",
        version=__version__,
        description=DESCRIPTION,
        openapi_url="/api/v1/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "X-Trace-Id"],
    )
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(v1_router, prefix="/api/v1")
    app.include_router(internal_tools.router)
    app.include_router(internal_llm.router)
    return app


app = create_app()
