"""Polite HTTP fetching for news sources (SPEC FR-N9).

* robots.txt per host, cached; RFC 9309: 4xx -> no restrictions, 5xx / network error -> assume disallow;
* explicit User-Agent; minimal interval between requests to the same host;
* conditional GET (ETag / If-Modified-Since) so unchanged feeds cost one 304;
* response size cap; timeouts.
"""

import asyncio
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

MAX_BYTES = 5 * 1024 * 1024
ROBOTS_TTL_S = 24 * 3600


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    outcome: str  # ok | not_modified | http_error | network_error | robots_disallowed | too_large
    status_code: int | None = None
    body: bytes = b""
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    latency_ms: int = 0
    error: str | None = None


class PoliteFetcher:
    def __init__(
        self,
        user_agent: str,
        min_interval_s: float = 2.0,
        timeout_s: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.min_interval_s = min_interval_s
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": user_agent,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )
        self._robots: dict[str, tuple[float, RobotFileParser | None]] = {}
        self._last_hit: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> "PoliteFetcher":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def _robots_for(self, scheme: str, host: str) -> RobotFileParser | None:
        """None = no restrictions."""
        cached = self._robots.get(host)
        if cached and time.monotonic() - cached[0] < ROBOTS_TTL_S:
            return cached[1]
        rp: RobotFileParser | None
        try:
            resp = await self.client.get(f"{scheme}://{host}/robots.txt")
            if 400 <= resp.status_code < 500:
                rp = None
            elif resp.status_code >= 500:
                rp = RobotFileParser()
                rp.parse(["User-agent: *", "Disallow: /"])
            else:
                rp = RobotFileParser()
                rp.parse(resp.text.splitlines())
        except httpx.HTTPError:
            rp = RobotFileParser()
            rp.parse(["User-agent: *", "Disallow: /"])
        self._robots[host] = (time.monotonic(), rp)
        return rp

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        rp = await self._robots_for(parts.scheme, parts.netloc)
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    async def _throttle(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last_hit.get(host, 0.0) + self.min_interval_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_hit[host] = time.monotonic()

    async def fetch(self, url: str, etag: str | None = None, last_modified: str | None = None) -> FetchResult:
        if not await self.allowed(url):
            return FetchResult(url, "robots_disallowed", error="robots.txt disallows this URL")
        host = urlsplit(url).netloc
        await self._throttle(host)
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        started = time.perf_counter()
        try:
            async with self.client.stream("GET", url, headers=headers) as resp:
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        return FetchResult(url, "too_large", resp.status_code, error=f"> {MAX_BYTES} bytes")
                    chunks.append(chunk)
                latency = int((time.perf_counter() - started) * 1000)
                common = {
                    "status_code": resp.status_code,
                    "content_type": resp.headers.get("content-type"),
                    "etag": resp.headers.get("etag"),
                    "last_modified": resp.headers.get("last-modified"),
                    "latency_ms": latency,
                }
                if resp.status_code == 304:
                    return FetchResult(url, "not_modified", **common)
                if resp.status_code >= 400:
                    return FetchResult(url, "http_error", error=f"HTTP {resp.status_code}", **common)
                return FetchResult(url, "ok", body=b"".join(chunks), **common)
        except httpx.HTTPError as exc:
            latency = int((time.perf_counter() - started) * 1000)
            return FetchResult(
                url, "network_error", latency_ms=latency, error=f"{type(exc).__name__}: {exc}"[:500]
            )
