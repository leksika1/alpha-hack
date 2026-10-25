"""Finding feeds by name or by a site link (ADR-036)."""

import pytest

from app.services.news_discovery import directory, feed_links, search_directory, site_url


@pytest.mark.parametrize(
    ("query", "site"),
    [("BBC", "bbc.co.uk"), ("ведомости", "vedomosti.ru"), ("ЦБ", "cbr.ru"), ("Financial Times", "ft.com")],
)
def test_search_by_name_and_alias(query: str, site: str) -> None:
    assert site in {s["site"] for s in search_directory(query)}


def test_unknown_name_finds_nothing() -> None:
    assert search_directory("совершенно неизвестное издание") == []


def test_every_directory_feed_is_a_web_url() -> None:
    feeds = [f["url"] for s in directory() for f in s["feeds"]]
    assert feeds and all(u.startswith("https://") for u in feeds)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("vedomosti.ru", "https://vedomosti.ru"),
        ("https://www.cnbc.com/world/", "https://www.cnbc.com/world/"),
        ("BBC", None),
        ("Financial Times", None),
    ],
)
def test_site_url_tells_links_from_names(query: str, expected: str | None) -> None:
    assert site_url(query) == expected


def test_feed_links_announced_by_the_page() -> None:
    page = b"""<html><head>
    <link rel="alternate" type="application/rss+xml" title="Business" href="/rss/business.xml">
    <link rel="alternate" type="application/atom+xml" href="https://cdn.example.com/atom">
    <link rel="stylesheet" href="/style.css"></head></html>"""
    assert feed_links(page, "https://news.example.com/world") == [
        ("https://news.example.com/rss/business.xml", "Business"),
        ("https://cdn.example.com/atom", ""),
    ]
