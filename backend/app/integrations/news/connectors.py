"""News connectors: a plugin = parse(raw bytes) -> NewsDraft list for one source kind (SPEC FR-N1).

Adding a source of an existing kind is a row in data/reference/news_sources.json; a new kind is a class
registered in CONNECTORS. Fetching is shared (PoliteFetcher), so connectors only parse — which also lets the
pipeline re-process stored raw bodies from S3 without re-downloading.
"""

import calendar
import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Protocol

import feedparser

from app.services.news_store import NewsDraft, fixture_drafts

MAX_BODY_CHARS = 20_000


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag in {"p", "br", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    text = html.unescape("".join(parser.parts))
    return re.sub(r"\s+", " ", text).strip()[:MAX_BODY_CHARS]


@dataclass(frozen=True, slots=True)
class SourceInfo:
    id: str
    name: str
    kind: str
    url: str | None


class NewsConnector(Protocol):
    kind: str

    def parse(self, raw: bytes, source: SourceInfo, now: datetime) -> list[NewsDraft]: ...


def _entry_time(entry: Any, now: datetime) -> datetime:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            dt = datetime.fromtimestamp(calendar.timegm(value), tz=UTC)
            return min(dt, now)  # feeds sometimes publish future timestamps
    return now


class RssConnector:
    """RSS 2.0 / Atom / RDF via feedparser (encodings, CDATA, malformed feeds)."""

    kind = "rss"

    def parse(self, raw: bytes, source: SourceInfo, now: datetime) -> list[NewsDraft]:
        feed = feedparser.parse(raw)
        drafts: list[NewsDraft] = []
        for entry in feed.entries:
            title = html_to_text(entry.get("title"))
            link = entry.get("link") or ""
            if not title or not link.startswith(("http://", "https://")):
                continue
            body_html = ""
            if entry.get("content"):
                body_html = " ".join(c.get("value", "") for c in entry["content"])
            body = html_to_text(body_html or entry.get("summary") or entry.get("description") or "")
            if body.startswith(title):
                body = body[len(title) :].lstrip(" .—-")
            drafts.append(
                NewsDraft(
                    url=link,
                    title=title[:500],
                    body=body or title,
                    published_at=_entry_time(entry, now),
                    publisher=source.name,
                    external_id=(entry.get("id") or entry.get("guid") or link)[:500],
                )
            )
        return drafts


class FixtureConnector:
    """Local synthetic news (NEWS_FIXTURES_ENABLED): JSON with `items[]` dated relative to now."""

    kind = "fixture"

    def parse(self, raw: bytes, source: SourceInfo, now: datetime) -> list[NewsDraft]:
        return fixture_drafts(json.loads(raw.decode("utf-8")), now)


CONNECTORS: dict[str, NewsConnector] = {c.kind: c for c in (RssConnector(), FixtureConnector())}


def connector_for(kind: str) -> NewsConnector:
    try:
        return CONNECTORS[kind]
    except KeyError as exc:
        raise ValueError(f"no connector for source kind {kind!r}") from exc
