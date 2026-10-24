"""The collector reaches only the public internet (ADR-036): URL shape, private addresses, redirects."""

import httpx
import pytest

from app.integrations.news.http import PoliteFetcher
from app.integrations.news.safety import BlockedHostError, check_url_shape, ensure_public_host

PUBLIC_IP = "93.184.215.14"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/rss",
        "file:///etc/passwd",
        "https://user:pass@example.com/rss",
        "http://example.com:9000/rss",
        "https:///rss",
        "data/synthetic/news/fixture_news.json",
    ],
)
def test_url_shape_rejects_non_web_urls(url: str) -> None:
    with pytest.raises(ValueError):
        check_url_shape(url)


def test_url_shape_accepts_feeds() -> None:
    assert check_url_shape(" https://feeds.bbci.co.uk/news/business/rss.xml ").startswith(
        "https://feeds.bbci"
    )
    assert check_url_shape("http://example.com:80/rss.xml") == "http://example.com:80/rss.xml"


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1", "localhost"]
)
async def test_private_hosts_are_blocked(host: str) -> None:
    with pytest.raises(BlockedHostError):
        await ensure_public_host(host)


async def test_public_ip_is_allowed() -> None:
    await ensure_public_host(PUBLIC_IP)


def _transport(location: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"Location": location})

    return httpx.MockTransport(handler)


@pytest.mark.parametrize("location", ["http://169.254.169.254/latest/meta-data", "http://127.0.0.1:80/admin"])
async def test_redirect_to_internal_address_is_blocked(location: str) -> None:
    async with PoliteFetcher("test", min_interval_s=0, transport=_transport(location), public_only=True) as f:
        result = await f.fetch(f"http://{PUBLIC_IP}/rss.xml")
    assert result.outcome == "network_error" and "BlockedHostError" in str(result.error)


async def test_internal_source_url_is_refused_before_any_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"no request expected, got {request.url}")

    async with PoliteFetcher("test", transport=httpx.MockTransport(handler), public_only=True) as f:
        result = await f.fetch("http://minio:9000/analytics/secret.pdf")
    assert result.outcome == "network_error" and "BlockedHostError" in str(result.error)


async def test_guard_is_off_by_default_for_tests_and_fixtures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if request.url.path == "/robots.txt" else 200, content=b"<rss/>")

    async with PoliteFetcher("test", min_interval_s=0, transport=httpx.MockTransport(handler)) as f:
        assert (await f.fetch("http://news.internal/rss.xml")).outcome == "ok"
