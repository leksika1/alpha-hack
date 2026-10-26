"""One news collection run end-to-end: sync catalog -> poll -> match new events -> companies to regenerate.
Used by the scheduled worker task and by `scripts/ingest.py news`."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.s3 import get_s3_client
from app.integrations.news.http import PoliteFetcher
from app.services.feed_cleanup import retire_demo_feed
from app.services.news_ingest import PollResult, S3ObjectStore, poll, sync_sources
from app.services.pipeline import match_new_events

BASE_DIR = Path(__file__).resolve().parents[2]  # backend/ (or /app in the image)


@dataclass
class NewsRunResult:
    poll: PollResult
    companies_to_generate: list[str]
    demo_events_retired: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.poll.as_dict(),
            "companies_to_generate": self.companies_to_generate,
            "demo_events_retired": self.demo_events_retired,
        }


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else BASE_DIR / p


async def run_news_collection(
    conn: Any,
    settings: Settings,
    store: Any | None = None,
    fetcher: PoliteFetcher | None = None,
    source_ids: list[str] | None = None,
) -> NewsRunResult:
    await sync_sources(
        conn,
        _path(settings.news_sources_path),
        settings.news_live_sources_enabled,
        _path(settings.fixture_news_path) if settings.news_fixtures_enabled else None,
    )
    store = store or S3ObjectStore(get_s3_client(), settings.s3_bucket_news_raw)
    own_fetcher = fetcher is None
    fetcher = fetcher or PoliteFetcher(
        settings.news_user_agent,
        min_interval_s=settings.news_min_interval_s,
        public_only=settings.news_public_hosts_only,
    )
    try:
        result = await poll(
            conn,
            store,
            fetcher,
            concurrency=settings.news_fetch_concurrency,
            failures_to_down=settings.news_failures_to_down,
            source_ids=source_ids,
        )
    finally:
        if own_fetcher:
            await fetcher.close()
    companies = await match_new_events(conn, settings, result.event_ids)
    retired = 0
    if settings.news_fixtures_hide_after_live > 0:
        retired = await retire_demo_feed(conn, settings.news_fixtures_hide_after_live)
    return NewsRunResult(result, companies, retired)
