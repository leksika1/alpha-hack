"""Auth and the current user (demo accounts; real bank login is out of scope — FAQ #32, #63)."""

import uuid
from typing import Any

from fastapi import APIRouter, Request

from app.api.deps import Conn, SettingsDep, User, company_access
from app.api.v1.schemas import (
    CompanyAddIn,
    CompanyAddOut,
    CompanyBrief,
    LoginIn,
    MeOut,
    RefreshIn,
    RoleIn,
    RoleOut,
    TokenOut,
)
from app.core.errors import ProblemError
from app.core.security import TokenError, decode_token, issue_user_tokens
from app.services import users
from app.services.interactions import log_action
from app.services.pipeline import rematch_company

router = APIRouter(tags=["auth"])

Responses = dict[int | str, dict[str, Any]]

ERR_401: Responses = {401: {"description": "Неверные учётные данные или токен (problem+json)"}}
ERR_403: Responses = {403: {"description": "Нет доступа к компании (problem+json)"}}


@router.post("/auth/login", response_model=TokenOut, responses=ERR_401, summary="Вход по демо-учётке")
async def login(body: LoginIn, conn: Conn, settings: SettingsDep) -> TokenOut:
    row = await users.authenticate(conn, body.login, body.password)
    if row is None:
        raise ProblemError(401, "unauthorized", "Invalid login or password")
    pair = issue_user_tokens(settings, str(row["id"]), row["platform_role"])
    return TokenOut(
        access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in
    )


@router.post("/auth/refresh", response_model=TokenOut, responses=ERR_401, summary="Обновить access-токен")
async def refresh(body: RefreshIn, conn: Conn, settings: SettingsDep) -> TokenOut:
    try:
        claims = decode_token(settings, body.refresh_token, "refresh")
    except TokenError as exc:
        raise ProblemError(401, "unauthorized", "Invalid refresh token", str(exc)) from exc
    row = await conn.fetchrow(
        "SELECT id, platform_role FROM core.users WHERE id = $1 AND is_active", uuid.UUID(claims["sub"])
    )
    if row is None:
        raise ProblemError(401, "unauthorized", "User is not active")
    pair = issue_user_tokens(settings, str(row["id"]), row["platform_role"])
    return TokenOut(
        access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in
    )


@router.get(
    "/me", response_model=MeOut, responses=ERR_401, summary="Текущий пользователь, его компании и роли"
)
async def me(user: User, conn: Conn, settings: SettingsDep) -> MeOut:
    companies = await users.user_companies(conn, user.id, settings.eligible_client_types)
    return MeOut(
        user_id=user.id,
        login=user.login,
        display_name=user.display_name,
        platform_role="admin" if user.is_admin else "user",
        companies=[CompanyBrief(**c) for c in companies],
    )


@router.put(
    "/me/companies/{company_id}/role",
    response_model=RoleOut,
    responses={**ERR_401, **ERR_403},
    summary="Ручное переключение роли в компании (FAQ п.31)",
)
async def switch_role(
    company_id: str, body: RoleIn, request: Request, user: User, conn: Conn, settings: SettingsDep
) -> RoleOut:
    access = await company_access(conn, user, company_id, settings)
    await users.set_role(conn, user.id, company_id, body.role.value)
    await log_action(
        conn, user.id, company_id, None, "role_switch", {"from": access.role, "to": body.role.value}
    )
    pending = await rematch_company(conn, settings, company_id)
    if pending:
        await request.app.state.enqueue([company_id])
    return RoleOut(
        company_id=company_id,
        role=body.role,
        candidates_pending=pending,
        regeneration="queued" if pending else "not_needed",
    )


def _require_picker(settings: Any) -> None:
    if not settings.demo_company_picker:
        raise ProblemError(403, "forbidden", "Company picker is off", "DEMO_COMPANY_PICKER=false")


@router.post(
    "/me/companies",
    response_model=CompanyAddOut,
    responses={
        **ERR_401,
        403: {"description": "Выбор компаний выключен"},
        404: {"description": "Нет такой компании"},
    },
    summary="Демо: открыть любую компанию клиентской базы по ID (ADR-048); инсайты генерируются сразу",
)
async def add_company(
    body: CompanyAddIn, request: Request, user: User, conn: Conn, settings: SettingsDep
) -> CompanyAddOut:
    _require_picker(settings)
    added = await users.add_company(
        conn, user.id, body.company_id, body.role.value, settings.eligible_client_types
    )
    if not added:
        raise ProblemError(404, "not-found", "No such client company", f"company_id={body.company_id}")
    await log_action(conn, user.id, body.company_id, None, "company_opened", {"role": body.role.value})
    pending = await rematch_company(conn, settings, body.company_id)
    if pending:
        await request.app.state.enqueue([body.company_id])
    brief = next(
        c
        for c in await users.user_companies(conn, user.id, settings.eligible_client_types)
        if c["company_id"] == body.company_id
    )
    return CompanyAddOut(
        company=CompanyBrief(**brief),
        candidates_pending=pending,
        regeneration="queued" if pending else "not_needed",
    )


@router.delete(
    "/me/companies/{company_id}",
    status_code=204,
    responses={**ERR_401, 404: {"description": "Компания не открыта пользователем"}},
    summary="Убрать компанию, открытую по ID (доступ от банка не убирается)",
)
async def remove_company(company_id: str, user: User, conn: Conn, settings: SettingsDep) -> None:
    _require_picker(settings)
    if not await users.remove_company(conn, user.id, company_id):
        raise ProblemError(404, "not-found", "Company was not opened by the user", f"company_id={company_id}")
