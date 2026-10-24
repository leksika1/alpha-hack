"""Finding a site's news feeds for an admin (ADR-036): by name in the directory of known sites, or by a link
to the site — feeds the page announces (`<link rel="alternate">`), directory feeds of that domain and
the usual paths (`/rss`, `/feed`, …). Every candidate is checked like a feed an admin adds (`check_feed`).
"""

import asyncio
import json
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.core.config import Settings
from app.integrations.news.article import looks_like_feed
from app.integrations.news.http import PoliteFetcher
from app.integrations.news.safety import check_url_shape
from app.services.compliance import registry_entry
from app.services.news_sources_admin import CHECK_TIMEOUT_S, check_feed

DIRECTORY_PATH = Path(__file__).resolve().parents[2] / "data" / "reference" / "news_directory.json"
COMMON_FEED_PATHS = ("/rss", "/rss.xml", "/rss/", "/feed", "/feed/", "/news/rss", "/index.xml", "/atom.xml")
FEED_TYPES = {"application/rss+xml", "application/atom+xml", "application/rdf+xml"}
MAX_CANDIDATES = 10
CHECK_CONCURRENCY = 4
_DOMAIN = re.compile(r"^(?:https?://)?[\w.-]+\.[a-zа-я]{2,}(?:[/:?#]\S*)?$", re.IGNORECASE)


@lru_cache
def directory() -> list[dict[str, Any]]:
    sites: list[dict[str, Any]] = json.loads(DIRECTORY_PATH.read_text(encoding="utf-8"))["sites"]
    return sites


def _norm(text: str) -> str:
    return re.sub(r"[\s«»\"'.\-]+", " ", text.lower().replace("ё", "е")).strip()


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def search_directory(query: str) -> list[dict[str, Any]]:
    q = _norm(query)
    if not q:
        return []
    found = []
    for site in directory():
        names = [_norm(n) for n in (site["name"], *site.get("aliases", []), site["site"])]
        if (
            any(q == n for n in names)
            or any(q in n for n in names)
            or any(n in q for n in names if len(n) > 3)
        ):
            found.append(site)
    return found


class _FeedLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if (
            tag == "link"
            and "alternate" in a.get("rel", "").lower()
            and a.get("type", "").lower() in FEED_TYPES
        ) and a.get("href"):
            self.links.append((a["href"], a.get("title", "")))


def feed_links(page: bytes, base_url: str) -> list[tuple[str, str]]:
    parser = _FeedLinks()
    parser.feed(page.decode("utf-8", errors="replace"))
    return [(urljoin(base_url, href), title) for href, title in parser.links]


def site_url(query: str) -> str | None:
    """The query as a site URL when it looks like a domain or a link, else None (a name)."""
    q = query.strip()
    if " " in q or not _DOMAIN.match(q):
        return None
    return check_url_shape(q if "://" in q else f"https://{q}")


async def discover(
    settings: Settings, query: str, known_urls: set[str], fetcher: PoliteFetcher | None = None
) -> dict[str, Any]:
    """Candidate feeds with their check results. ValueError for a malformed link."""
    url = site_url(query)
    candidates: list[dict[str, Any]] = []
    blocked = None

    def add(feed_url: str, title: str, name: str | None, origin: str) -> None:
        if feed_url not in {c["url"] for c in candidates} and len(candidates) < MAX_CANDIDATES:
            candidates.append({"url": feed_url, "title": title, "name": name, "found_by": origin})

    if url is None:
        for site in search_directory(query):
            for feed in site["feeds"]:
                add(feed["url"], feed["title"], site["name"], "directory")
    else:
        entry = registry_entry(url)
        if entry is not None:
            blocked = f"{entry.name}: {entry.status} (реестр)"
        else:
            host = _host(url)
            for site in directory():
                if host == site["site"] or host.endswith("." + site["site"]):
                    for feed in site["feeds"]:
                        add(feed["url"], feed["title"], site["name"], "directory")
            own = fetcher is None
            page_fetcher = fetcher or PoliteFetcher(
                settings.news_user_agent,
                min_interval_s=0.0,
                timeout_s=CHECK_TIMEOUT_S,
                public_only=settings.news_public_hosts_only,
            )
            try:
                page = await page_fetcher.fetch(url)
            finally:
                if own:
                    await page_fetcher.close()
            if page.outcome == "ok" and looks_like_feed(page.body, page.content_type):
                add(url, "", None, "link")
            elif page.outcome == "ok":
                for link, title in feed_links(page.body, url):
                    add(link, title, None, "page")
            root = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
            for path in COMMON_FEED_PATHS:
                add(root + path, "", None, "guess")

    sem = asyncio.Semaphore(CHECK_CONCURRENCY)

    async def checked(c: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            try:
                result = await check_feed(settings, c["url"], fetcher)
            except ValueError as exc:
                return {
                    **c,
                    "ok": False,
                    "outcome": "invalid_url",
                    "status_code": None,
                    "already_added": False,
                    "items": 0,
                    "sample": [],
                    "error": str(exc),
                }
        return {
            **c,
            "ok": result.ok,
            "outcome": result.outcome,
            "status_code": result.status_code,
            "items": result.items,
            "sample": result.sample,
            "error": result.error,
            "already_added": c["url"] in known_urls,
        }

    results = await asyncio.gather(*(checked(c) for c in candidates))
    # Guessed paths are shown only when they work; the rest — with their status
    shown = [r for r in results if r["found_by"] != "guess" or r["ok"]]
    shown.sort(key=lambda r: (not r["ok"], r["found_by"] == "guess"))
    return {
        "query": query,
        "kind": "name" if url is None else "site",
        "blocked": blocked,
        "candidates": shown,
    }
