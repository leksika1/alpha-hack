"""Client for the backend Tools API (contracts/tools-api.openapi.yaml) with built-in tracing via log_step."""

import contextlib
import json
import time
from typing import Any

import httpx

from alfa_ml.log import get_logger

log = get_logger(__name__)

# Tests route Tools API calls to an in-process fake by setting this transport.
TRANSPORT: httpx.AsyncBaseTransport | None = None


class ToolError(RuntimeError):
    def __init__(self, tool: str, status: int, detail: str) -> None:
        super().__init__(f"{tool}: HTTP {status} {detail[:200]}")
        self.tool = tool
        self.status = status


def _preview(value: Any, n: int = 500) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:n]


class Tools:
    """Run-scoped access to the backend tools. Every call is audited by the backend; we add our own trace steps."""

    def __init__(self, access: dict[str, Any], timeout_s: float = 20.0) -> None:
        self.run_id: str = access["run_id"]
        self.allowed: set[str] = set(access.get("allowed_tools") or [])
        self.llm_base_url: str | None = access.get("llm_base_url")
        self.token: str = access["token"]
        self.calls = 0
        self.client = httpx.AsyncClient(
            base_url=access["base_url"].rstrip("/"),
            headers={"Authorization": f"Bearer {self.token}", "X-Run-Id": self.run_id},
            timeout=timeout_s,
            transport=TRANSPORT,
        )

    def can(self, tool: str) -> bool:
        return not self.allowed or tool in self.allowed

    async def call(self, tool: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self.can(tool):
            raise ToolError(tool, 403, "tool not allowed for this run")
        self.calls += 1
        resp = await self.client.post(f"/{tool}", json=body)
        if resp.status_code >= 400:
            raise ToolError(tool, resp.status_code, resp.text)
        data: dict[str, Any] = resp.json()
        return data

    async def step(self, step_type: str, name: str, status: str = "ok", **kw: Any) -> None:
        body: dict[str, Any] = {"step_type": step_type, "name": name[:120], "status": status}
        for key, value in kw.items():
            if value is None:
                continue
            if key in {"input_preview", "output_preview"}:
                value = _preview(value, 4000)
            body[key] = value
        with contextlib.suppress(httpx.HTTPError):  # tracing must never break generation
            await self.client.post("/log_step", json=body)

    async def timed(self, tool: str, body: dict[str, Any], step_type: str = "tool_call") -> dict[str, Any]:
        started = time.perf_counter()
        try:
            result = await self.call(tool, body)
        except Exception as exc:
            await self.step(
                step_type,
                tool,
                "error",
                input_preview=_preview(body),
                error=str(exc)[:500],
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            raise
        await self.step(
            step_type,
            tool,
            input_preview=_preview(body),
            output_preview=_preview(result),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return result

    async def safe(self, tool: str, body: dict[str, Any], default: Any, step_type: str = "tool_call") -> Any:
        """Optional data: a failing tool degrades the analysis instead of failing the run."""
        try:
            return await self.timed(tool, body, step_type)
        except Exception as exc:
            log.warning("tool_degraded", tool=tool, error=str(exc)[:200], run_id=self.run_id)
            return default

    async def close(self) -> None:
        await self.client.aclose()
