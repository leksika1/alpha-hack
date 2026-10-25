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
