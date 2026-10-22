"""Raw asyncpg connections for bulk operations (COPY, set-based SQL)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from app.core.config import get_settings


def asyncpg_dsn(database_url: str | None = None) -> str:
    url = database_url or get_settings().database_url
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


@asynccontextmanager
async def pg_connection(database_url: str | None = None) -> AsyncIterator[Any]:
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        yield conn
    finally:
        await conn.close()
