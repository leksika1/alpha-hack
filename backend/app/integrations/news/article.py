"""A news article from an HTML page a user sends (ADR-038): title, text, publication date, publisher.

Standard library only: Open Graph / meta tags for the title, date and site name; the text is the paragraphs
inside `<article>` (or `<main>`), else all paragraphs of the page, without scripts, navigation, headers and
footers.
"""

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser

MAX_TEXT_CHARS = 20_000
MIN_PARAGRAPH_CHARS = 40
SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "button"}
DATE_META = ("article:published_time", "og:published_time", "datePublished", "pubdate", "date")


@dataclass(frozen=True, slots=True)
class Article:
    title: str
    text: str
    published_at: datetime | None
    publisher: str | None


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.skip = 0
        self.scope = 0  # inside <article>/<main>
        self.in_title = False
        self.in_p = False
        self.current: list[str] = []
        self.paragraphs: list[tuple[bool, str]] = []  # (inside article/main, text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "meta":
            key = a.get("property") or a.get("name") or a.get("itemprop")
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag in SKIP_TAGS:
            self.skip += 1
        elif tag in {"article", "main"}:
            self.scope += 1
        elif tag == "title":
            self.in_title = True
        elif tag in {"p", "h2", "h3", "li", "blockquote"} and not self.skip:
            self.in_p, self.current = True, []
        elif tag == "time" and a.get("datetime"):
            self.meta.setdefault("time", a["datetime"])

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag in {"article", "main"} and self.scope:
            self.scope -= 1
        elif tag == "title":
            self.in_title = False
        elif tag in {"p", "h2", "h3", "li", "blockquote"} and self.in_p:
            text = re.sub(r"\s+", " ", "".join(self.current)).strip()
            if len(text) >= MIN_PARAGRAPH_CHARS:
                self.paragraphs.append((self.scope > 0, text))
            self.in_p = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        elif self.in_p and not self.skip:
            self.current.append(data)


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def extract_article(page: bytes | str) -> Article:
    """The article of an HTML page; empty title or text when the page has none."""
    text = page.decode("utf-8", errors="replace") if isinstance(page, bytes) else page
    parser = _Parser()
    parser.feed(text)
    parser.close()
    meta = parser.meta
    title = html.unescape(meta.get("og:title") or meta.get("twitter:title") or parser.title).strip()
    scoped = [t for inside, t in parser.paragraphs if inside]
    paragraphs = scoped or [t for _, t in parser.paragraphs]
    body = "\n".join(paragraphs)[:MAX_TEXT_CHARS]
    if not body:
        body = (meta.get("og:description") or meta.get("description") or "").strip()
    published = next((d for key in (*DATE_META, "time") if (d := _date(meta.get(key)))), None)
    return Article(title=title[:500], text=body, published_at=published, publisher=meta.get("og:site_name"))


def looks_like_feed(body: bytes, content_type: str | None) -> bool:
    head = body[:500].lstrip().lower()
    return bool(content_type and ("rss" in content_type or "atom" in content_type)) or head.startswith(
        (b"<?xml", b"<rss", b"<feed")
    )
