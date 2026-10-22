import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.main import create_app

pytestmark = pytest.mark.integration


def test_ready_reports_all_dependencies_ok(infra: dict[str, str]) -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/ready")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"postgres": "ok", "redis": "ok", "s3": "ok"}


def test_base_migration_creates_extensions_and_schemas(infra: dict[str, str]) -> None:
    async def _query() -> tuple[set[str], set[str]]:
        engine = create_async_engine(infra["DATABASE_URL"])
        async with engine.connect() as conn:
            ext = {r[0] for r in await conn.execute(text("SELECT extname FROM pg_extension"))}
            schemas = {
                r[0] for r in await conn.execute(text("SELECT schema_name FROM information_schema.schemata"))
            }
        await engine.dispose()
        return ext, schemas

    extensions, schemas = asyncio.run(_query())
    assert {"vector", "pg_trgm"} <= extensions
    assert {"staging", "core"} <= schemas


def test_ready_degrades_when_dependency_missing(
    infra: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import config as config_module
    from app.core import db as db_module

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    config_module.get_settings.cache_clear()
    db_module.get_engine.cache_clear()
    try:
        with TestClient(create_app()) as client:
            resp = client.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["checks"]["redis"].startswith("error")
        assert resp.json()["checks"]["postgres"] == "ok"
    finally:
        monkeypatch.undo()
        config_module.get_settings.cache_clear()
        db_module.get_engine.cache_clear()
