"""RFC 7807 problem+json errors for all APIs."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, trace_id_var

PROBLEM_BASE = "https://alfa-future.local/problems/"
PROBLEM_MEDIA_TYPE = "application/problem+json"

log = get_logger(__name__)


class ProblemError(Exception):
    """Raise anywhere in request handling to return a problem+json response."""

    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.extra = extra or {}


def problem_body(
    status: int, code: str, title: str, detail: str | None, instance: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": PROBLEM_BASE + code,
        "title": title,
        "status": status,
        "instance": instance,
        "trace_id": trace_id_var.get(),
    }
    if detail:
        body["detail"] = detail
    if extra:
        body.update(extra)
    return body


def _response(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(body, status_code=body["status"], media_type=PROBLEM_MEDIA_TYPE)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        return _response(
            problem_body(exc.status, exc.code, exc.title, exc.detail, request.url.path, exc.extra)
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not-found", 405: "method-not-allowed"}.get(
            exc.status_code, "http-error"
        )
        return _response(problem_body(exc.status_code, code, str(exc.detail), None, request.url.path))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return _response(
            problem_body(422, "validation", "Validation error", None, request.url.path, {"errors": errors})
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return _response(problem_body(500, "internal", "Internal server error", None, request.url.path))
