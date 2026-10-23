"""Analytics ingestion entry points for the CLI, the seed and the worker task."""

from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.s3 import S3ObjectStore, get_s3_client
from app.integrations.embeddings import get_embedder
from app.integrations.ocr import get_ocr
from app.services.analytics_ingest import IngestOutcome, ingest_directory, reembed

BASE_DIR = Path(__file__).resolve().parents[2]  # backend/ (or /app in the image)
SYNTHETIC_DIR = BASE_DIR / "data" / "synthetic" / "analytics"
# /app/data/raw in the image (mounted), <repo>/data/raw locally
RAW_DIRS = (BASE_DIR / "data" / "raw" / "analytics", BASE_DIR.parent / "data" / "raw" / "analytics")


def analytics_dirs(settings: Settings) -> list[tuple[Path, str]]:
    """Organizers' PDFs when DATA_DIR points to the raw data, otherwise the synthetic demo PDFs."""
    raw = next((d for d in RAW_DIRS if d.is_dir() and any(d.glob("*.pdf"))), None)
    if raw is not None and "raw" in settings.data_dir:
        return [(raw, "organizers")]
    return [(SYNTHETIC_DIR, "synthetic")]


async def run_analytics_ingest(
    conn: Any, settings: Settings, directory: Path | None = None, origin: str = "upload", force: bool = False
) -> list[IngestOutcome]:
    store = S3ObjectStore(get_s3_client(), settings.s3_bucket_analytics)
    ocr = get_ocr(settings.ocr_provider)
    embedder = get_embedder(settings)
    targets = [(directory, origin)] if directory is not None else analytics_dirs(settings)
    outcomes: list[IngestOutcome] = []
    for path, kind in targets:
        outcomes.extend(await ingest_directory(conn, store, ocr, embedder, path, kind, force))
    return outcomes


async def run_reembed(conn: Any, settings: Settings) -> int:
    return await reembed(conn, get_embedder(settings))
