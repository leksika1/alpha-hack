import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.integrations.news.connectors import (
    FixtureConnector,
    RssConnector,
    SourceInfo,
    connector_for,
    html_to_text,
)
from app.integrations.news.http import MAX_BYTES, PoliteFetcher

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
SRC = SourceInfo("test", "Тестовая лента", "rss", "https://feed.test/rss")

RSS_1251 = """<?xml version="1.0" encoding="windows-1251"?>
<rss version="2.0"><channel><title>Лента</title>
<item><title>Банк России сохранил ключевую ставку</title><link>https://feed.test/a?utm_source=x</link>
<description><![CDATA[<p>Совет директоров <b>сохранил</b> ставку 14%.</p>
<script>alert(1)</script>]]></description>
<pubDate>Wed, 22 Oct 2026 09:00:00 +0300</pubDate><guid>a-1</guid></item>
<item><title>Без ссылки</title><description>skip me</description></item>
<item><title>Из будущего</title><link>https://feed.test/b</link><pubDate>Wed,
    22 Oct 2036 09:00:00 GMT</pubDate></item>
</channel></rss>""".encode("cp1251")

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title>
<entry><title>Atom news</title><link href="https://feed.test/atom/1"/><id>urn:1</id>
<updated>2026-10-22T08:00:00Z</updated><content type="html">&lt;p&gt;Body text&lt;/p&gt;</content></entry>
</feed>"""


def test_rss_windows_1251_html_and_dates() -> None:
    drafts = RssConnector().parse(RSS_1251, SRC, NOW)
    assert [d.title for d in drafts] == ["Банк России сохранил ключевую ставку", "Из будущего"]
    first = drafts[0]
    assert first.body == "Совет директоров сохранил ставку 14%."  # html stripped, script dropped
    assert first.published_at == datetime(2026, 10, 22, 6, tzinfo=UTC)
    assert first.external_id == "a-1" and first.publisher == "Тестовая лента"
    assert drafts[1].published_at == NOW  # future timestamps are clamped


def test_atom_feed() -> None:
    (d,) = RssConnector().parse(ATOM, SRC, NOW)
    assert d.title == "Atom news" and d.body == "Body text" and d.url == "https://feed.test/atom/1"


def test_garbage_does_not_crash() -> None:
    assert RssConnector().parse(b"<html>not a feed</html>", SRC, NOW) == []
    assert RssConnector().parse(b"", SRC, NOW) == []


def test_fixture_connector_and_registry() -> None:
    raw = json.dumps({"publisher": "Демо", "items": [{"id": "x1", "title": "T", "body": "B", "age_hours": 2,
                                                         "cluster": "c"}]}).encode()  # fmt: skip
    (d,) = connector_for("fixture").parse(raw, SourceInfo("fixture", "f", "fixture", None), NOW)
    assert d.url == "https://fixture.local/news/x1" and d.cluster_key == "c" and d.publisher == "Демо"
    assert isinstance(connector_for("rss"), RssConnector) and isinstance(
        connector_for("fixture"), FixtureConnector
    )
    with pytest.raises(ValueError):
        connector_for("telegram")


def test_html_to_text() -> None:
    assert html_to_text("<p>A&nbsp;&amp;&nbsp;B</p><style>x{}</style><br>C") == "A & B C"
    assert html_to_text(None) == ""


@respx.mock
async def test_robots_disallow_is_respected() -> None:
    respx.get("https://blocked.test/robots.txt").respond(200, text="User-agent: *\nDisallow: /")
    feed = respx.get("https://blocked.test/rss").respond(200, content=b"<rss/>")
    async with PoliteFetcher("TestBot/1", min_interval_s=0) as f:
        res = await f.fetch("https://blocked.test/rss")
    assert res.outcome == "robots_disallowed" and not feed.called


@respx.mock
@pytest.mark.parametrize(("robots_status", "allowed"), [(404, True), (403, True), (500, False)])
async def test_robots_status_codes_rfc9309(robots_status: int, allowed: bool) -> None:
    respx.get("https://site.test/robots.txt").respond(robots_status)
    respx.get("https://site.test/rss").respond(200, content=b"<rss/>")
    async with PoliteFetcher("TestBot/1", min_interval_s=0) as f:
        res = await f.fetch("https://site.test/rss")
    assert (res.outcome == "ok") is allowed


@respx.mock
async def test_conditional_get_and_errors() -> None:
    respx.get("https://site.test/robots.txt").respond(404)
    route = respx.get("https://site.test/rss")
    route.side_effect = [
        httpx.Response(200, content=b"<rss/>", headers={"ETag": '"v1"', "Last-Modified": "Wed, 22 Oct 2026"}),
        httpx.Response(304),
        httpx.Response(503),
        httpx.ConnectError("boom"),
    ]
    async with PoliteFetcher("TestBot/1", min_interval_s=0) as f:
        first = await f.fetch("https://site.test/rss")
        second = await f.fetch("https://site.test/rss", first.etag, first.last_modified)
        third = await f.fetch("https://site.test/rss")
        fourth = await f.fetch("https://site.test/rss")
    assert first.outcome == "ok" and first.etag == '"v1"' and first.body == b"<rss/>"
    assert route.calls[1].request.headers["If-None-Match"] == '"v1"'
    assert second.outcome == "not_modified"
    assert third.outcome == "http_error" and third.status_code == 503
    assert fourth.outcome == "network_error" and "ConnectError" in (fourth.error or "")
    assert route.calls[0].request.headers["User-Agent"] == "TestBot/1"


@respx.mock
async def test_oversized_response_is_rejected() -> None:
    respx.get("https://big.test/robots.txt").respond(404)
    respx.get("https://big.test/rss").respond(200, content=b"x" * (MAX_BYTES + 1))
    async with PoliteFetcher("TestBot/1", min_interval_s=0) as f:
        res = await f.fetch("https://big.test/rss")
    assert res.outcome == "too_large" and res.body == b""


def test_manual_connector_keeps_absolute_dates_and_strips_html() -> None:
    items = [
        {"id": "m1", "title": "<b>Пресс-релиз</b>", "body": "<p>Текст <script>x()</script>новости</p>",
         "published_at": "2026-10-20T10:00:00+03:00", "publisher": "Аналитик банка"},
        {"id": "m2", "title": "Без даты", "body": "Текст", "url": "https://bank.test/news/2"},
    ]  # fmt: skip
    src = SourceInfo("manual", "Ручной ввод", "manual", None)
    first, second = connector_for("manual").parse(json.dumps({"items": items}).encode(), src, NOW)
    assert (first.title, first.body) == ("Пресс-релиз", "Текст новости")
    assert first.published_at == datetime(2026, 10, 20, 7, tzinfo=UTC) and first.publisher == "Аналитик банка"
    assert first.url == "https://manual.local/news/m1" and first.external_id == "m1"
    assert (
        second.published_at == NOW
        and second.url == "https://bank.test/news/2"
        and second.publisher == "Ручной ввод"
    )


LISTING = """<html><body><nav><a href="/press-center/news">Все новости министерства транспорта</a></nav>
<div class="item"><a href="/press-center/news/2"><img src="x.jpg"></a>
<span class="date">21 Октября 2026</span>
<a href="/press-center/news/2" class="title"> Минтранс утвердил новые правила перевозки опасных грузов
</a></div>
<div class="item"><a href="/press-center/news/1"><img src="y.jpg"></a>
<div class="time">20.10.2026 18:00</div>
<a href="/press-center/news/1"> Ставки на грузовые перевозки по железной дороге проиндексируют </a></div>
<div class="item"><a href="https://other.test/press-center/news/3">Чужой сайт с подходящим адресом ссылки</a>
<a href="/press-center/news/4">Коротко</a>
<a href="/press-center/news/5#comments">Новость без даты рядом, обсуждение на форуме</a></div>
<script>var s = "<a href='/press-center/news/9'>Скрипт не новость, хотя длинный</a>";</script>
</body></html>"""
HTML_SRC = SourceInfo(
    "mintrans",
    "Минтранс",
    "html",
    "https://min.test/press-center/news",
    {"link_pattern": r"^/press-center/news/\d+$"},
)


def test_html_listing_takes_titles_and_dates_next_to_links() -> None:
    drafts = connector_for("html").parse(LISTING.encode(), HTML_SRC, NOW)
    by_url = {d.url: d for d in drafts}
    assert list(by_url) == [
        "https://min.test/press-center/news/2",
        "https://min.test/press-center/news/1",
        "https://min.test/press-center/news/5",
    ]  # other host, a short text, the nav link and the script are not news
    first = by_url["https://min.test/press-center/news/2"]
    assert first.title == "Минтранс утвердил новые правила перевозки опасных грузов"
    assert first.published_at == datetime(2026, 10, 20, 21, tzinfo=UTC)  # 21 Oct, Moscow midnight
    assert first.publisher == "Минтранс" and first.external_id == first.url
    second = by_url["https://min.test/press-center/news/1"]
    assert second.published_at == datetime(2026, 10, 20, 15, tzinfo=UTC)  # numeric format
    assert by_url["https://min.test/press-center/news/5"].published_at == NOW  # no date: the fetch time


def test_html_listing_respects_max_items() -> None:
    src = SourceInfo("m", "M", "html", HTML_SRC.url, {**HTML_SRC.config, "max_items": 1})
    assert len(connector_for("html").parse(LISTING.encode(), src, NOW)) == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("23.10.2026 18:00", datetime(2026, 10, 23, 15, tzinfo=UTC)),
        ("23 октября 2026 12:39", datetime(2026, 10, 23, 9, 39, tzinfo=UTC)),
        ("1 мая 2026", datetime(2026, 4, 30, 21, tzinfo=UTC)),
        ("5 марта 2026", datetime(2026, 3, 4, 21, tzinfo=UTC)),
        ("31.02.2026", None),
        ("Новости 2026 года", None),
    ],
)
def test_russian_dates_of_listings(text: str, expected: datetime | None) -> None:
    from app.integrations.news.connectors import parse_ru_date

    parsed = parse_ru_date(text)
    assert (parsed.astimezone(UTC) if parsed else None) == expected


def test_html_listing_with_dates_after_titles() -> None:
    page = """<li><a href="/press-center/news/7">Правительство продлило льготы для перевозчиков</a>
    <time>19.10.2026</time></li>
    <li><a href="/press-center/news/8">Новые требования к тахографам грузовиков</a>
    <time>18.10.2026</time></li>"""
    drafts = connector_for("html").parse(page.encode(), HTML_SRC, NOW)
    assert [d.published_at.day for d in drafts] == [18, 17]  # Moscow midnight is the previous day in UTC
