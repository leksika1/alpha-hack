"""A link or a news text a user sends (ADR-038): the same pipeline as any news, scoped to the user's company.

* a link is fetched politely (robots.txt, public addresses only) and the article is extracted; if the site is
  unreachable from the server, the answer says so instead of failing;
* registry sites and texts that break Russian law are refused with the reason (ADR-037);
* the news goes through dedup and clustering; an event known only from users' submissions is private to their
  companies (`events.private_company_ids`) until a real source reports it;
* the event is matched for the user's company only; a relevant one is queued for generation.
"""

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from app.core.config import Settings
from app.core.s3 import S3ObjectStore, get_s3_client
from app.domain.compliance import LAW, NEWS_RULES, check_text
from app.integrations.news.article import extract_article, looks_like_feed
from app.integrations.news.http import FetchResult, PoliteFetcher
from app.integrations.news.safety import check_url_shape
from app.integrations.scorers import get_scorer
from app.services.compliance import registry_entry
from app.services.interactions import log_action
from app.services.matching import match, roles_by_company
from app.services.news_ingest import ingest_manual
from app.services.news_store import USER_SOURCE_ID, canonical_url, text_hash

DAILY_LIMIT = 30
MIN_TEXT_CHARS = 100
FETCH_TIMEOUT_S = 15.0
SOURCE_NAME = "Ссылки и тексты пользователей"
UNAVAILABLE = "Ресурс сейчас недоступен для просмотра"
REASONS = {
    "robots_disallowed": "сайт запрещает автоматическое чтение страниц (robots.txt)",
    "http_error": "сайт ответил ошибкой",
    "network_error": "сайт не отвечает или недоступен с сервера",
    "too_large": "страница слишком большая",
    "not_modified": "сайт не отдал содержимое",
}

Enqueue = Callable[[list[str]], Awaitable[None]]


class DailyLimitError(Exception):
    pass


@dataclass
class SubmitResult:
    status: str  # processed | duplicate | unavailable | blocked | not_article
    message: str
    reason: str | None = None
    news_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None
    title: str | None = None
    url: str | None = None
    relevant: bool = False
    generation_queued: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _unavailable(fetch: FetchResult) -> str:
    reason = REASONS.get(fetch.outcome, "не удалось открыть страницу")
    if fetch.outcome == "http_error" and fetch.status_code:
        reason = f"{reason} {fetch.status_code}"
    return reason


async def _fetch(settings: Settings, url: str, fetcher: PoliteFetcher | None) -> FetchResult:
    own = fetcher is None
    fetcher = fetcher or PoliteFetcher(
        settings.news_user_agent,
        min_interval_s=0.0,
        timeout_s=FETCH_TIMEOUT_S,
        public_only=settings.news_public_hosts_only,
    )
    try:
        return await fetcher.fetch(url)
    finally:
        if own:
            await fetcher.close()


async def submit(
    conn: Any,
    settings: Settings,
    user_id: uuid.UUID,
    company_id: str,
    *,
    url: str | None,
    title: str | None,
    text: str | None,
    enqueue: Enqueue,
    fetcher: PoliteFetcher | None = None,
) -> SubmitResult:
    """ValueError for a malformed URL; DailyLimitError over DAILY_LIMIT submissions a day."""
    sent_today = await conn.fetchval(
        """SELECT count(*) FROM core.user_actions WHERE user_id = $1 AND action_type = 'news_submitted'
           AND created_at > now() - interval '1 day'""",
        user_id,
    )
    if sent_today >= DAILY_LIMIT:
        raise DailyLimitError
    result = await _submit(conn, settings, company_id, url, title, text, enqueue, fetcher)
    await log_action(
        conn,
        user_id,
        company_id,
        None,
        "news_submitted",
        {"status": result.status, "url": result.url, "event_id": str(result.event_id or "") or None},
    )
    return result


async def _submit(
    conn: Any,
    settings: Settings,
    company_id: str,
    url: str | None,
    title: str | None,
    text: str | None,
    enqueue: Enqueue,
    fetcher: PoliteFetcher | None,
) -> SubmitResult:
    publisher, published_at = "Текст от пользователя", datetime.now(UTC)
    if url:
        url = check_url_shape(url)
        entry = registry_entry(url)
        if entry is not None:
            return SubmitResult(
                "blocked", f"Материалы «{entry.name}» не обрабатываются", LAW["registry_source"], url=url
            )
        fetch = await _fetch(settings, url, fetcher)
        if fetch.outcome != "ok":
            return SubmitResult("unavailable", UNAVAILABLE, _unavailable(fetch), url=url)
        if looks_like_feed(fetch.body, fetch.content_type):
            return SubmitResult(
                "not_article",
                "Это лента новостей, а не статья: её может добавить администратор как источник",
                url=url,
            )
        article = extract_article(fetch.body)
        if not article.title or len(article.text) < MIN_TEXT_CHARS:
            return SubmitResult(
                "not_article", "Не удалось выделить текст новости на странице — пришлите её текст", url=url
            )
        title, text = article.title, article.text
        publisher = article.publisher or (urlsplit(url).hostname or "").removeprefix("www.")
        published_at = article.published_at or published_at
    assert title and text
    findings = check_text(f"{title}\n{text}", NEWS_RULES)
    if findings:
        return SubmitResult(
            "blocked", "Материал не может быть обработан: он нарушает законодательство РФ", findings[0].law,
            url=url, title=title,
        )  # fmt: skip
    item_id = "user-" + hashlib.sha256((url or f"{title}\n{text}").encode()).hexdigest()[:16]
    item = {
        "id": item_id,
        "url": url,
        "title": title,
        "body": text,
        "publisher": publisher,
        "published_at": published_at.isoformat(),
    }
    store = S3ObjectStore(get_s3_client(), settings.s3_bucket_news_raw)
    polled = await ingest_manual(conn, store, [item], source_id=USER_SOURCE_ID, source_name=SOURCE_NAME)
    run = polled.sources[0]
    news = await conn.fetchrow(
        """SELECT n.id, n.title, n.url, en.event_id FROM core.news_items n
           LEFT JOIN core.event_news en ON en.news_id = n.id
           WHERE n.canonical_url = $1 OR n.text_hash = $2 ORDER BY n.fetched_at LIMIT 1""",
        canonical_url(url or f"https://manual.local/news/{item_id}"),
        text_hash(title, text),
    )
    if news is None or news["event_id"] is None:  # a near-duplicate of a known news item
        return SubmitResult(
            "duplicate", "Эта новость уже известна сервису: похожая уже обработана", url=url, title=title
        )
    event_id = news["event_id"]
    if event_id in {uuid.UUID(e) for e in polled.new_event_ids}:
        await conn.execute(
            "UPDATE core.events SET private_company_ids = ARRAY[$2::text] WHERE id = $1", event_id, company_id
        )
    else:  # a known event: if users' only, it becomes visible to this company as well
        await conn.execute(
            """UPDATE core.events SET private_company_ids = array_append(private_company_ids, $2::text)
               WHERE id = $1 AND private_company_ids IS NOT NULL AND NOT $2 = ANY(private_company_ids)""",
            event_id,
            company_id,
        )
    roles = await roles_by_company(conn, [company_id])
    await match(
        conn,
        [company_id],
        roles,
        [str(event_id)],
        min_score=settings.match_min_score,
        max_per_company=settings.match_max_per_company,
        scorer=get_scorer(settings),
    )
    candidate = await conn.fetchval(
        """SELECT string_agg(DISTINCT status, ',') FROM core.candidate_matches
           WHERE company_id = $1 AND event_id = $2""",
        company_id,
        event_id,
    )
    pending = bool(candidate and "pending" in candidate)
    if pending:
        await enqueue([company_id])
    new = run.n_new > 0
    return SubmitResult(
        "processed" if new else "duplicate",
        ("Новость обработана" if new else "Эта новость уже известна сервису")
        + (": инсайт для компании готовится" if pending else "")
        + ("" if candidate else ": для этой компании она не значима"),
        news_id=news["id"],
        event_id=event_id,
        title=news["title"],
        url=url,
        relevant=candidate is not None,
        generation_queued=pending,
    )
