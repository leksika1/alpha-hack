"""Questions to the analytics (CJM 06 "уточнить сценарий"; SPEC FR-D): the backend proxies the agent's SSE
answer (ML service or ml-stub, contracts/ml-service.openapi.yaml /v1/chat) and keeps the thread.

* the agent gets the run-scoped Tools API access, the insight (when asked from a card) and the last messages
  of the thread — the answer itself is the agent's, the backend writes no text;
* `step`, `token` and `source` events are relayed live; the agent's `done` is held back until it passes the
  contract check (app/domain/chat_validator.py), then sent as `done` or replaced by an `error`;
* every question is an agent run (`agent_runs.kind = chat`) with steps and tool calls, like an insight.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.chat_validator import check_chat_final
from app.integrations.ml_client import HttpInsightGenerator, MlServiceError
from app.services.generation import add_step, new_run_id, tools_access
from app.services.interactions import log_action

log = get_logger(__name__)

RELAYED_EVENTS = {"step", "token", "source"}


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def problem(code: str, status: int, title: str, detail: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": f"https://alfa-future.local/problems/{code}",
        "title": title,
        "status": status,
    }
    if detail:
        body["detail"] = detail[:300]
    return body


async def history(
    conn: Any, user_id: uuid.UUID, company_id: str, insight_id: uuid.UUID | None, limit: int = 50
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT * FROM (
               SELECT * FROM core.qa_messages
               WHERE user_id = $1 AND company_id = $2 AND insight_id IS NOT DISTINCT FROM $3
               ORDER BY id DESC LIMIT $4) t
           ORDER BY id""",
        user_id,
        company_id,
        insight_id,
        limit,
    )
    return [
        {
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "intent": r["intent"],
            "status": r["status"],
            "run_id": r["run_id"],
            "sources": json.loads(r["sources"]),
            "statements": json.loads(r["statements"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


async def _parse_sse(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, Any]]:
    event, data = "message", list[str]()
    async for line in lines:
        if line == "":
            if data:
                raw = "\n".join(data)
                try:
                    yield event, json.loads(raw)
                except json.JSONDecodeError:
                    yield event, raw
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, json.loads("\n".join(data))


async def ask_stream(
    pool: Any,
    settings: Settings,
    client: HttpInsightGenerator,
    user_id: uuid.UUID,
    company_id: str,
    role: str,
    insight: Any | None,
    question: str,
    intent: str,
) -> AsyncIterator[str]:
    insight_id = insight["id"] if insight is not None else None
    run_id = new_run_id()
    async with pool.acquire() as conn:
        previous = await history(conn, user_id, company_id, insight_id, settings.qa_history_messages)
        await conn.execute(
            """INSERT INTO core.agent_runs (id, kind, request_id, company_id, event_id, role, generator)
               VALUES ($1, 'chat', $2, $3, $4, $5, $6)""",
            run_id,
            f"chat:{uuid.uuid4().hex}",
            company_id,
            insight["event_id"] if insight is not None else None,
            role,
            f"{settings.insight_generator}:{client.name}",
        )
        question_id = await conn.fetchval(
            """INSERT INTO core.qa_messages (user_id, company_id, insight_id, run_id, role, intent, content)
               VALUES ($1, $2, $3, $4, 'user', $5, $6) RETURNING id""",
            user_id,
            company_id,
            insight_id,
            run_id,
            intent,
            question,
        )
        await log_action(conn, user_id, company_id, insight_id, "ask", {"intent": intent, "run_id": run_id})
    yield sse("run", {"run_id": run_id, "question_id": question_id})

    payload = json.loads(insight["payload"]) if insight is not None else None
    body = {
        "request_id": run_id,
        "company_id": company_id,
        "role": role,
        "insight_id": str(insight_id) if insight_id else None,
        "insight": payload,
        "intent": intent,
        "messages": [{"role": m["role"], "content": m["content"]} for m in previous if m["status"] == "ok"]
        + [{"role": "user", "content": question}],
        "tools": tools_access(settings, run_id, company_id),
    }
    started = time.perf_counter()
    final: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    tokens: list[str] = []
    try:
        async with asyncio.timeout(settings.qa_timeout_s):
            async for event, data in _parse_sse(client.chat_stream(body)):
                if event == "done" and isinstance(data, dict):
                    final = data
                elif event == "error":
                    error = (
                        data
                        if isinstance(data, dict)
                        else problem("agent-error", 502, "Agent error", str(data))
                    )
                elif event in RELAYED_EVENTS:
                    if event == "token" and isinstance(data, dict):
                        tokens.append(str(data.get("delta", "")))
                    yield sse(event, data)
    except TimeoutError:
        error = problem("agent-timeout", 504, "The agent did not answer in time")
    except (MlServiceError, httpx.HTTPError) as exc:
        error = problem("ml-unavailable", 503, "Analytics assistant is unavailable", str(exc))
        log.warning("qa_ml_unavailable", run_id=run_id, error=str(exc)[:300])

    status, answer, sources, statements = "error", "".join(tokens), [], []
    if error is None and final is None:
        error = problem("agent-no-answer", 502, "The agent finished without a final answer")
    if error is None and final is not None:
        check = check_chat_final(final)
        if check.ok:
            status = "ok"
            answer = check.final["answer"]
            sources, statements = check.final["sources"], check.final["statements"]
            yield sse("done", check.final)
        else:
            error = problem(
                "invalid-answer",
                502,
                "The answer did not pass the check",
                "; ".join(p["detail"] for p in check.problems),
            )
    if error is not None:
        yield sse("error", error)

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO core.qa_messages
                   (user_id, company_id, insight_id, run_id, role, intent, content, sources, statements,
                    status)
               VALUES ($1, $2, $3, $4, 'assistant', $5, $6, $7::jsonb, $8::jsonb, $9)""",
            user_id,
            company_id,
            insight_id,
            run_id,
            intent,
            answer,
            json.dumps(sources, ensure_ascii=False),
            json.dumps(statements, ensure_ascii=False),
            status,
        )
        await add_step(
            conn,
            run_id,
            "self_check",
            "backend.check_answer",
            "ok" if status == "ok" else "error",
            output_preview="passed" if status == "ok" else (error or {}).get("detail", "")[:300],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        await conn.execute(
            """UPDATE core.agent_runs SET status = $2, outcome = $3, error = $4, finished_at = now()
               WHERE id = $1""",
            run_id,
            "ok" if status == "ok" else "error",
            "answered" if status == "ok" else "failed",
            None if error is None else error.get("detail") or error.get("title"),
        )
