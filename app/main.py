"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health
from app.api.v1 import router as v1_router
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware

DESCRIPTION = """
Бэкенд сервиса персонализированных бизнес-инсайтов «Альфа-Будущее» для интернет-банка «Альфа-Бизнес».

* `/api/v1` — публичное API для фронтенда (JWT).
* Ошибки — RFC 7807 `application/problem+json`. Пагинация — курсором.
* Внутренние API (`/internal/tools`, `/internal/llm`) описаны в `contracts/`.
"""


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    app = FastAPI(
        title="Alfa Future — Business Insights API",
        version=__version__,
        description=DESCRIPTION,
        openapi_url="/api/v1/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
    )
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
    return app


app = create_app()
