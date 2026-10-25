"""News connectors: a plugin = parse(raw bytes) -> NewsDraft list for one source kind (SPEC FR-N1).

Adding a source of an existing kind is a row in data/reference/news_sources.json; a new kind is a class
registered in CONNECTORS. Fetching is shared (PoliteFetcher), so connectors only parse — which also lets the
pipeline re-process stored raw bodies from S3 without re-downloading.
"""

import calendar
import html
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

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
    config: dict[str, Any] = field(default_factory=dict)  # kind-specific settings from the catalog


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


class ManualConnector:
    """News submitted by bank analysts via the admin API (`POST /admin/news`): JSON `items[]` with absolute
    `published_at`. Goes through the same pipeline as RSS (raw in S3, dedup, events, matching)."""

    kind = "manual"

    def parse(self, raw: bytes, source: SourceInfo, now: datetime) -> list[NewsDraft]:
        data = json.loads(raw.decode("utf-8"))
        drafts = []
        for item in data["items"]:
            published = datetime.fromisoformat(item["published_at"]) if item.get("published_at") else now
            drafts.append(
                NewsDraft(
                    url=item.get("url") or f"https://manual.local/news/{item['id']}",
                    title=html_to_text(item["title"])[:500],
                    body=html_to_text(item["body"])[:MAX_BODY_CHARS],
                    published_at=published if published.tzinfo else published.replace(tzinfo=UTC),
                    publisher=item.get("publisher") or source.name,
                    external_id=item["id"],
                )
            )
        return drafts


MSK = timezone(timedelta(hours=3))
RU_MONTHS = re.compile(
    r"(январ)|(феврал)|(март)|(апрел)|(ма[йя])|(июн)|(июл)|(август)|(сентябр)|(октябр)|(ноябр)|(декабр)"
)
_DATE_NUMERIC = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\D{1,3}(\d{1,2}):(\d{2}))?")
_DATE_WORDS = re.compile(r"\b(\d{1,2})\s+([а-яё]+)\s+(\d{4})(?:\D{1,3}(\d{1,2}):(\d{2}))?", re.IGNORECASE)


def parse_ru_date(text: str) -> datetime | None:
    """«23.10.2026 18:00», «23 октября 2026 12:39», «23 Октября 2026» -> Moscow time; None without a date."""
    month: int | None = None
    m = _DATE_NUMERIC.search(text)
    if m:
        month = int(m.group(2))
    else:
        m = _DATE_WORDS.search(text)
        word = RU_MONTHS.match(m.group(2).lower()) if m else None
        month = word.lastindex if word else None
    if m is None or month is None:
        return None
    try:
        return datetime(
            int(m.group(3)), month, int(m.group(1)), int(m.group(4) or 0), int(m.group(5) or 0), tzinfo=MSK
        )
    except ValueError:
        return None


class _LinkCollector(HTMLParser):
    """The page as a stream: link texts (with their href) and the text between them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stream: list[tuple[str, str]] = []  # (href or "", text)
        self._href: str | None = None
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "a":
            self._flush()
            self._href = dict(attrs).get("href") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a":
            self._flush()
            self._href = None

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._parts)).strip()
        if text or self._href:
            self.stream.append((self._href or "", text))
        self._parts = []

    def close(self) -> None:
        super().close()
        self._flush()


class HtmlConnector:
    """A news listing page of a site without RSS (ministries, agencies): links whose path matches
    `config.link_pattern` are the news, the link text is the title. The date is the one printed next to the
    link, on the side the page prints dates on; without a date the news is dated by the fetch. Same host
    only."""

    kind = "html"
    MIN_TITLE_CHARS = 20

    def parse(self, raw: bytes, source: SourceInfo, now: datetime) -> list[NewsDraft]:
        pattern = re.compile(source.config["link_pattern"])
        max_items = int(source.config.get("max_items", 30))
        base = source.url or ""
        host = urlsplit(base).hostname
        collector = _LinkCollector()
        collector.feed(raw.decode("utf-8", errors="replace"))
        collector.close()
        stream = collector.stream
        found: dict[str, tuple[str, int]] = {}  # url -> (title, position of the title link)
        for i, (href, text) in enumerate(stream):
            if not href:
                continue
            url = urljoin(base, href).split("#")[0]
            parts = urlsplit(url)
            if parts.hostname != host or not pattern.search(parts.path):
                continue
            if len(text) >= self.MIN_TITLE_CHARS and len(text) > len(found.get(url, ("", 0))[0]):
                found[url] = (text, i)
        positions = sorted(pos for _, pos in found.values())

        def dates(start: int, end: int) -> list[datetime]:
            return [d for h, t in stream[start:end] if not h and (d := parse_ru_date(t))]

        # the dates of a listing are all printed before their titles or all after them
        before = {p: dates(positions[k - 1] + 1 if k else 0, p) for k, p in enumerate(positions)}
        after = {
            p: dates(p + 1, positions[k + 1] if k + 1 < len(positions) else len(stream))
            for k, p in enumerate(positions)
        }
        dates_first = sum(map(bool, before.values())) >= sum(map(bool, after.values()))
        drafts: list[NewsDraft] = []
        for url, (title, pos) in sorted(found.items(), key=lambda kv: kv[1][1])[:max_items]:
            near = before[pos][-1:] if dates_first else after[pos][:1]
            published = min(near[0], now) if near else now
            drafts.append(
                NewsDraft(
                    url=url,
                    title=html.unescape(title)[:500],
                    body=html.unescape(title),
                    published_at=published.astimezone(UTC),
                    publisher=source.name,
                    external_id=url[:500],
                )
            )
        return drafts


CONNECTORS: dict[str, NewsConnector] = {
    c.kind: c for c in (RssConnector(), HtmlConnector(), FixtureConnector(), ManualConnector())
}


def connector_for(kind: str) -> NewsConnector:
    try:
        return CONNECTORS[kind]
    except KeyError as exc:
        raise ValueError(f"no connector for source kind {kind!r}") from exc
