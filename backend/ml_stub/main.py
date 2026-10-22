"""ml-stub: deterministic implementation of contracts/ml-service.openapi.yaml (ADR-013).

Phase 0: service skeleton. Generation/chat/score are implemented in phase 2.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ml_stub import __version__

app = FastAPI(title="ml-stub", version=__version__)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


def _not_implemented(path: str) -> JSONResponse:
    return JSONResponse(
        {
            "type": "https://alfa-future.local/problems/not-implemented",
            "title": "Not implemented yet",
            "status": 501,
            "instance": path,
        },
        status_code=501,
        media_type="application/problem+json",
    )


@app.post("/v1/insights/generate")
async def generate() -> JSONResponse:
    return _not_implemented("/v1/insights/generate")


@app.post("/v1/chat")
async def chat() -> JSONResponse:
    return _not_implemented("/v1/chat")


@app.post("/v1/score")
async def score() -> JSONResponse:
    return _not_implemented("/v1/score")
