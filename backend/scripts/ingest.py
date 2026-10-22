"""CLI for ingest pipelines.

    python scripts/ingest.py companies [--data-dir DIR]
    python scripts/ingest.py news [--sources ID ...] [--reprocess RAW_ID ...] [--enqueue]

`analytics` is added in phase 4.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.core.pg import pg_connection  # noqa: E402
from app.core.s3 import get_s3_client  # noqa: E402
from app.services.ingest_companies import ingest_companies  # noqa: E402
from app.services.news_ingest import S3ObjectStore, reprocess_raw  # noqa: E402
from app.services.news_run import run_news_collection  # noqa: E402
from app.services.pipeline import match_new_events  # noqa: E402

REFERENCE_DIR = ROOT / "data" / "reference"


def _print(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


async def _companies(data_dir: Path) -> None:
    async with pg_connection() as conn:
        result = await ingest_companies(conn, data_dir, REFERENCE_DIR)
    _print({"run_id": str(result.run_id), **result.stats})


async def _news(sources: list[str] | None, reprocess: list[str] | None, enqueue: bool) -> None:
    settings = get_settings()
    async with pg_connection() as conn:
        if reprocess:
            store = S3ObjectStore(get_s3_client(), settings.s3_bucket_news_raw)
            res = await reprocess_raw(conn, store, reprocess)
            companies = await match_new_events(conn, settings, res.event_ids)
            summary = {**res.as_dict(), "companies_to_generate": companies}
        else:
            run = await run_news_collection(conn, settings, source_ids=sources)
            companies, summary = run.companies_to_generate, run.as_dict()
    _print(summary)
    if enqueue and companies:
        from app.workers.broker import broker
        from app.workers.tasks import enqueue_generation

        await broker.startup()
        try:
            await enqueue_generation(companies)
        finally:
            await broker.shutdown()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pipeline", choices=["companies", "news"])
    parser.add_argument(
        "--data-dir", default=settings.data_dir, help="companies: directory with the three CSV files"
    )
    parser.add_argument("--sources", nargs="*", help="news: poll only these source ids")
    parser.add_argument("--reprocess", nargs="*", help="news: re-parse these news_raw ids from S3")
    parser.add_argument(
        "--enqueue", action="store_true", help="news: queue insight generation for new matches"
    )
    args = parser.parse_args()
    if args.pipeline == "companies":
        data_dir = Path(args.data_dir)
        asyncio.run(_companies(data_dir if data_dir.is_absolute() else ROOT / data_dir))
    else:
        asyncio.run(_news(args.sources or None, args.reprocess or None, args.enqueue))


if __name__ == "__main__":
    main()
