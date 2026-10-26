"""Idempotent seed step run by the `seed` service after migrations (make seed).

1. S3 buckets.
2. Companies: if core.companies is empty, ingest DATA_DIR (organizers' data or the committed synthetic set).
3. Demo: bank products, demo users and access, fixture news (NEWS_FIXTURES_ENABLED), matching.
4. Queue pre-generation of insights for demo companies (the worker runs it once ml-stub and the API are up),
   so the first useful insight is visible right after login; then the first news poll and the Bank of Russia
   figures, so real news and rates do not wait for the schedule (15 minutes / an hour).
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.core.pg import pg_connection  # noqa: E402
from app.core.s3 import all_buckets, ensure_buckets, get_s3_client  # noqa: E402
from app.services.analytics_run import run_analytics_ingest  # noqa: E402
from app.services.ingest_companies import COMPANIES_FILE, ingest_companies  # noqa: E402
from app.services.news_ingest import S3ObjectStore  # noqa: E402
from app.services.seed import seed_demo  # noqa: E402

log = get_logger("seed")
REFERENCE_DIR = ROOT / "data" / "reference"


async def _seed(data_dir: Path, enqueue: bool) -> None:
    settings = get_settings()
    async with pg_connection() as conn:
        existing = await conn.fetchval("SELECT count(*) FROM core.companies")
        if existing:
            log.info("seed_companies_skip", existing=existing)
        elif (data_dir / COMPANIES_FILE).exists():
            result = await ingest_companies(conn, data_dir, REFERENCE_DIR)
            log.info(
                "seed_companies_loaded", run_id=str(result.run_id), valid=result.stats["companies_valid"]
            )
        else:
            log.warning("seed_companies_no_data", data_dir=str(data_dir))
            return
        store = S3ObjectStore(get_s3_client(), settings.s3_bucket_news_raw)
        docs = await run_analytics_ingest(conn, settings)
        log.info("seed_analytics_done", documents=[(d.file_name, d.status, d.chunks) for d in docs])
        summary = await seed_demo(conn, settings, data_dir, REFERENCE_DIR, ROOT, store)
    log.info("seed_demo_done", **summary)
    print(json.dumps(summary, ensure_ascii=False))
    if enqueue:
        from app.workers.broker import broker
        from app.workers.tasks import enqueue_generation, market_refresh_task, poll_news_task

        await broker.startup()
        try:
            if summary["demo_companies"]:
                await enqueue_generation(summary["demo_companies"])
                log.info("seed_generation_queued", companies=summary["demo_companies"])
            if settings.news_live_sources_enabled:
                await poll_news_task.kiq()
            await market_refresh_task.kiq()
            log.info("seed_first_poll_queued")
        finally:
            await broker.shutdown()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    ensure_buckets(get_s3_client(), all_buckets(settings))
    log.info("seed_buckets_ready")
    data_dir = Path(settings.data_dir)
    asyncio.run(
        _seed(data_dir if data_dir.is_absolute() else ROOT / data_dir, enqueue="--no-queue" not in sys.argv)
    )


if __name__ == "__main__":
    main()
