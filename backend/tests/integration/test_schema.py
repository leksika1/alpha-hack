"""ORM metadata and Alembic migrations must describe the same schema."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from app.repositories.models import Base

pytestmark = pytest.mark.integration


async def test_no_drift_between_models_and_migrations(infra: dict[str, str]) -> None:
    def _diff(sync_conn):  # type: ignore[no-untyped-def]
        ctx = MigrationContext.configure(
            sync_conn,
            opts={
                "include_schemas": True,
                "compare_type": True,
                "include_name": lambda name, type_, parents: type_ != "schema" or name in {"staging", "core"},
            },
        )
        return compare_metadata(ctx, Base.metadata)

    engine = create_async_engine(infra["DATABASE_URL"])
    async with engine.connect() as conn:
        diff = await conn.run_sync(_diff)
    await engine.dispose()
    assert diff == [], f"schema drift: {diff}"
