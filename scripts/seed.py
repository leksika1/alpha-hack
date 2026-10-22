"""Idempotent seed step run by the `seed` service after migrations (make seed).

Phase 0: makes sure S3 buckets exist. Later phases add: reference data, synthetic/real companies,
demo users and pre-generated insights for demo companies (docs/PROGRESS.md, phase 2).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.core.s3 import all_buckets, ensure_buckets, get_s3_client  # noqa: E402


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    log = get_logger("seed")
    buckets = all_buckets(settings)
    ensure_buckets(get_s3_client(), buckets)
    log.info("seed_buckets_ready", buckets=buckets)


if __name__ == "__main__":
    main()
