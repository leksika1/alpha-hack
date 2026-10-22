"""Idempotent seed step run by the `seed` service after migrations (make seed).

1. S3 buckets.
2. Companies: if core.companies is empty, ingest DATA_DIR (organizers' data in data/raw or the
   committed synthetic dataset). Re-ingest explicitly with `make ingest-companies`.
Later phases add demo users and pre-generated insights (docs/PROGRESS.md, phase 2).
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.core.pg import pg_connection  # noqa: E402
from app.core.s3 import all_buckets, ensure_buckets, get_s3_client  # noqa: E402
from app.services.ingest_companies import COMPANIES_FILE, ingest_companies  # noqa: E402

log = get_logger("seed")


async def _seed_companies(data_dir: Path) -> None:
    async with pg_connection() as conn:
        existing = await conn.fetchval("SELECT count(*) FROM core.companies")
        if existing:
            log.info("seed_companies_skip", existing=existing)
            return
        if not (data_dir / COMPANIES_FILE).exists():
            log.warning("seed_companies_no_data", data_dir=str(data_dir))
            return
        result = await ingest_companies(conn, data_dir, ROOT / "data" / "reference")
        log.info("seed_companies_loaded", run_id=str(result.run_id), valid=result.stats["companies_valid"])


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    buckets = all_buckets(settings)
    ensure_buckets(get_s3_client(), buckets)
    log.info("seed_buckets_ready", buckets=buckets)
    data_dir = Path(settings.data_dir)
    asyncio.run(_seed_companies(data_dir if data_dir.is_absolute() else ROOT / data_dir))


if __name__ == "__main__":
    main()
