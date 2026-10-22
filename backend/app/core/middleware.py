"""Request-id / trace-id propagation and Prometheus HTTP metrics."""

import time
import uuid
from collections.abc import Awaitable, Callable

from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger, request_id_var, trace_id_var

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "route"])

log = get_logger("http")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        trace_id = request.headers.get("x-trace-id") or request.headers.get("x-run-id") or request_id
        rid_token = request_id_var.set(request_id)
        tid_token = trace_id_var.set(trace_id)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-Id"] = request_id
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            elapsed = time.perf_counter() - started
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            HTTP_REQUESTS.labels(request.method, route_path, str(status)).inc()
            HTTP_LATENCY.labels(request.method, route_path).observe(elapsed)
            if route_path not in ("/health", "/metrics"):
                log.info(
                    "http_request",
                    method=request.method,
                    path=route_path,
                    status=status,
                    ms=round(elapsed * 1000, 1),
                )
            request_id_var.reset(rid_token)
            trace_id_var.reset(tid_token)
