"""Rate limiting (SPEC §13): Redis token bucket per caller for /api/v1 (public) and /internal (agent).

The caller is the bearer token (hashed) or, without a token, the client address (protects /auth/login).
Pure ASGI middleware, so SSE responses are not buffered. If Redis is unavailable, requests pass (fail open)
for REDIS_RETRY_S and a warning is logged at most once a minute: availability matters more than the limit.
"""

import hashlib
import json
import math
import time
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings
from app.core.errors import PROBLEM_BASE, PROBLEM_MEDIA_TYPE
from app.core.logging import get_logger, trace_id_var

log = get_logger(__name__)

# KEYS[1] bucket; ARGV: capacity, refill tokens per ms, now ms. Returns {allowed, tokens_left}.
BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1]) or capacity
local ts = tonumber(data[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / rate) + 1000)
return {allowed, tostring(tokens)}
"""

REDIS_RETRY_S = 30.0
EXEMPT_PREFIXES = ("/health", "/ready", "/metrics", "/docs", "/redoc", "/api/v1/openapi.json")


def bucket_for(path: str, settings: Settings) -> tuple[str, int] | None:
    if path.startswith(EXEMPT_PREFIXES):
        return None
    if path.startswith("/internal/"):
        return "internal", settings.rate_limit_internal_per_min
    if path.startswith("/api/"):
        return "public", settings.rate_limit_public_per_min
    return None


def caller_key(scope: Scope) -> str:
    headers = dict(scope.get("headers") or [])
    auth = headers.get(b"authorization", b"")
    if auth.lower().startswith(b"bearer ") and len(auth) > 7:
        return "t:" + hashlib.sha256(auth[7:]).hexdigest()[:24]
    client = scope.get("client")
    return "ip:" + (client[0] if client else "unknown")


class RateLimiter:
    def __init__(self, redis: Any) -> None:
        self.redis = redis
        self._script = redis.register_script(BUCKET_LUA)

    async def hit(self, key: str, per_min: int, now_ms: int | None = None) -> tuple[bool, int]:
        """Returns (allowed, retry_after_s)."""
        rate = per_min / 60_000  # tokens per ms
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        allowed, left = await self._script(keys=[f"rl:{key}"], args=[per_min, rate, now])
        if int(allowed):
            return True, 0
        return False, max(1, math.ceil((1 - float(left)) / rate / 1000))


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self._warned_at = 0.0
        self._down_until = 0.0  # circuit breaker: skip Redis for a while after a failure

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.settings.rate_limit_enabled:
            await self.app(scope, receive, send)
            return
        bucket = bucket_for(scope["path"], self.settings)
        limiter: RateLimiter | None = getattr(scope["app"].state, "rate_limiter", None)
        if bucket is None or limiter is None or time.monotonic() < self._down_until:
            await self.app(scope, receive, send)
            return
        name, per_min = bucket
        try:
            allowed, retry_after = await limiter.hit(f"{name}:{caller_key(scope)}", per_min)
        except Exception as exc:  # Redis down -> fail open
            self._down_until = time.monotonic() + REDIS_RETRY_S
            if time.monotonic() - self._warned_at > 60:
                self._warned_at = time.monotonic()
                log.warning("rate_limit_unavailable", error=type(exc).__name__)
            allowed, retry_after = True, 0
        if allowed:
            await self.app(scope, receive, send)
            return
        body = json.dumps(
            {
                "type": PROBLEM_BASE + "rate-limited",
                "title": "Too many requests",
                "status": 429,
                "detail": f"Limit {per_min} requests per minute ({name}); retry in {retry_after} s",
                "instance": scope["path"],
                "trace_id": trace_id_var.get(),
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", PROBLEM_MEDIA_TYPE.encode()),
                    (b"retry-after", str(retry_after).encode()),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
