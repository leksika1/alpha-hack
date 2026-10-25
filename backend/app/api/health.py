"""Liveness, readiness and Prometheus metrics endpoints."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.logging import get_logger
from app.core.s3 import all_buckets, get_s3_client
from app.services import ops_metrics

router = APIRouter(tags=["service"])
log = get_logger(__name__)

CHECK_TIMEOUT_S = 3.0


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class ReadyOut(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, str]


@router.get("/health", response_model=HealthOut, summary="Liveness")
async def health() -> HealthOut:
    return HealthOut(version=__version__)


async def _check_db() -> None:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis() -> None:
    client = aioredis.from_url(get_settings().redis_url)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _check_s3() -> None:
    settings = get_settings()
    client = get_s3_client()
    for bucket in all_buckets(settings):
        await asyncio.to_thread(client.head_bucket, Bucket=bucket)


async def _run(name: str, check: Callable[[], Awaitable[None]]) -> tuple[str, str]:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_S)
        return name, "ok"
    except Exception as exc:
        return name, f"error: {type(exc).__name__}"


@router.get(
    "/ready",
    response_model=ReadyOut,
    summary="Readiness: Postgres, Redis, S3 buckets",
    responses={503: {"model": ReadyOut, "description": "A dependency is unavailable"}},
)
async def ready(response: Response) -> ReadyOut:
    results = await asyncio.gather(
        _run("postgres", _check_db), _run("redis", _check_redis), _run("s3", _check_s3)
    )
    checks = dict(results)
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = 503
    return ReadyOut(status="ready" if ok else "not_ready", checks=checks)


async def _refresh_pipeline_metrics(request: Request) -> None:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await asyncio.wait_for(ops_metrics.refresh(conn), timeout=CHECK_TIMEOUT_S)
    except Exception as exc:  # the scrape still returns the process metrics and the previous gauges
        log.warning("pipeline_metrics_failed", error=f"{type(exc).__name__}: {exc}")


@router.get("/metrics", summary="Prometheus metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    await _refresh_pipeline_metrics(request)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
