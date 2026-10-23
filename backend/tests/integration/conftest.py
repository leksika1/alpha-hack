"""Shared infrastructure for integration tests: real Postgres+pgvector, Redis and MinIO via testcontainers."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.community.minio import MinioContainer
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from app.core import config as config_module
from app.core import db as db_module
from app.core import s3 as s3_module

ROOT = Path(__file__).resolve().parents[2]

POSTGRES_IMAGE = "pgvector/pgvector:0.8.0-pg16"
REDIS_IMAGE = "redis:7.4-alpine"
# Official MinIO images are no longer published (ADR-004).
MINIO_IMAGE = "pgsty/minio:RELEASE.2026-08-04T00-00-00Z"


def _reset_caches() -> None:
    config_module.get_settings.cache_clear()
    db_module.get_engine.cache_clear()
    db_module.get_sessionmaker.cache_clear()
    s3_module.get_s3_client.cache_clear()


@pytest.fixture(scope="session")
def infra() -> Iterator[dict[str, str]]:
    pg = PostgresContainer(POSTGRES_IMAGE, username="alfa", password="alfa", dbname="alfa", driver="asyncpg")
    rd = RedisContainer(REDIS_IMAGE)
    mn = MinioContainer(MINIO_IMAGE)
    with pg, rd, mn:
        minio_cfg = mn.get_config()
        env = {
            "DATABASE_URL": pg.get_connection_url(),
            "REDIS_URL": f"redis://{rd.get_container_host_ip()}:{rd.get_exposed_port(6379)}/0",
            "S3_ENDPOINT_URL": f"http://{minio_cfg['endpoint']}",
            "S3_ACCESS_KEY": minio_cfg["access_key"],
            "S3_SECRET_KEY": minio_cfg["secret_key"],
            "APP_ENV": "test",
        }
        previous = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        _reset_caches()
        alembic_cfg = Config(str(ROOT / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(alembic_cfg, "head")
        s3_module.ensure_buckets(
            s3_module.get_s3_client(), s3_module.all_buckets(config_module.get_settings())
        )
        try:
            yield env
        finally:
            for k, v in previous.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _reset_caches()
