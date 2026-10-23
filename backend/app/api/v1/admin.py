"""Admin API (platform role `admin`). Phase 5: LLM spend; the rest of SPEC §10 Admin arrives in phase 6."""

from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from app.api.deps import Admin, Conn, SettingsDep
from app.api.v1.auth import ERR_401
from app.services import llm_gateway

router = APIRouter(prefix="/admin", tags=["admin"])


class LlmUsageRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    model: str
    purpose: str
    status: str
    calls: int
    tokens_in: int
    tokens_out: int
    cost_rub: float
    avg_latency_ms: float | None


class LlmBudgetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    limit_rub: float
    spent_rub: float
    remaining_rub: float
    tokens_in: int
    tokens_out: int
    exhausted: bool


class LlmUsageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget: LlmBudgetOut
    rows: list[LlmUsageRow]


@router.get(
    "/llm-usage",
    response_model=LlmUsageOut,
    responses={**ERR_401, 403: {"description": "Нужна роль admin"}},
    summary="Расход LLM: бюджет дня и вызовы по дням, моделям, целям и статусам (ok, cached, refused, error)",
)
async def llm_usage(
    admin: Admin, conn: Conn, settings: SettingsDep, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> Any:
    return await llm_gateway.usage_summary(conn, settings, days)
