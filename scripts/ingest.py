"""CLI for ingest pipelines: python scripts/ingest.py companies [--data-dir DIR]

`news` and `analytics` are added in phases 3 and 4.
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
from app.services.ingest_companies import ingest_companies  # noqa: E402

REFERENCE_DIR = ROOT / "data" / "reference"


async def _companies(data_dir: Path) -> None:
    async with pg_connection() as conn:
        result = await ingest_companies(conn, data_dir, REFERENCE_DIR)
    print(
        json.dumps({"run_id": str(result.run_id), **result.stats}, ensure_ascii=False, indent=2, default=str)
    )


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pipeline", choices=["companies"])
    parser.add_argument("--data-dir", default=settings.data_dir, help="directory with the three CSV files")
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    if args.pipeline == "companies":
        asyncio.run(_companies(data_dir))


if __name__ == "__main__":
    main()
