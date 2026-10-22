"""Companies available to the user: profile with provenance and completeness, questionnaire, graph."""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.deps import Conn, SettingsDep, User, company_access
from app.api.v1.auth import ERR_401, ERR_403, Responses
from app.api.v1.schemas import CompanyBrief, GraphOut, ProfileOut, ProfileUpdateIn, ProfileUpdateOut
from app.core.errors import ProblemError
from app.services import companies as svc
from app.services import users
from app.services.interactions import log_action
from app.services.pipeline import rematch_company

router = APIRouter(prefix="/companies", tags=["companies"])
ERRS: Responses = {**ERR_401, **ERR_403, 404: {"description": "Профиль не найден (problem+json)"}}


@router.get(
    "", response_model=list[CompanyBrief], responses=ERR_401, summary="Компании, доступные пользователю"
)
async def list_companies(user: User, conn: Conn, settings: SettingsDep) -> list[CompanyBrief]:
    return [
        CompanyBrief(**c) for c in await users.user_companies(conn, user.id, settings.eligible_client_types)
    ]


@router.get(
    "/{company_id}/profile",
    response_model=ProfileOut,
    responses=ERRS,
    summary="Что сервис знает о компании и откуда (признаки, источник каждого, полнота данных)",
)
async def get_profile(company_id: str, user: User, conn: Conn, settings: SettingsDep) -> ProfileOut:
    await company_access(conn, user, company_id, settings)
    prof = await svc.profile(conn, company_id)
    if prof is None:
        raise ProblemError(404, "not-found", "Company profile not found")
    return ProfileOut(**prof)


@router.patch(
    "/{company_id}/profile",
    response_model=ProfileUpdateOut,
    responses=ERRS,
    summary=(
        "Дополнить профиль (поставщики, покупатели, сырьё, валюты, планы по кредитам); пересчёт и "
        "перегенерация"
    ),
)
async def patch_profile(
    company_id: str, body: ProfileUpdateIn, request: Request, user: User, conn: Conn, settings: SettingsDep
) -> ProfileUpdateOut:
    await company_access(conn, user, company_id, settings)
    changes = body.model_dump(exclude_unset=True)
    await svc.update_user_provided(conn, company_id, changes)
    await log_action(conn, user.id, company_id, None, "profile_update", {"fields": sorted(changes)})
    pending = await rematch_company(conn, settings, company_id, rebuild_context=True)
    if pending:
        await request.app.state.enqueue([company_id])
    prof = await svc.profile(conn, company_id)
    if prof is None:
        raise ProblemError(404, "not-found", "Company profile not found")
    return ProfileUpdateOut(
        profile=ProfileOut(**prof),
        candidates_pending=pending,
        regeneration="queued" if pending else "not_needed",
    )


@router.get(
    "/{company_id}/graph",
    response_model=GraphOut,
    responses=ERRS,
    summary="Группа, контрагенты и связи; гипотезы помечены is_hypothesis с уверенностью и обоснованием",
)
async def get_graph(
    company_id: str,
    user: User,
    conn: Conn,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=100, description="Рёбер каждого типа")] = 15,
) -> GraphOut:
    await company_access(conn, user, company_id, settings)
    return GraphOut(**await svc.graph(conn, company_id, limit))
