"""Raw asyncpg connections for bulk operations (COPY, set-based SQL) and the API connection pool.

JSON/JSONB columns are returned as text and passed as text with explicit ``::jsonb`` casts
(no custom codecs), so services behave the same on pooled and standalone connections.
"""

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


async def create_pool(database_url: str | None = None, min_size: int = 1, max_size: int = 10) -> Any:
    return await asyncpg.create_pool(asyncpg_dsn(database_url), min_size=min_size, max_size=max_size)
