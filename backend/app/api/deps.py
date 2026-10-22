"""FastAPI dependencies: DB connection, current user, company access, run scope (ADR-010)."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.errors import ProblemError
from app.core.security import TokenError, decode_token

bearer = HTTPBearer(auto_error=False)


async def get_conn(request: Request) -> AsyncIterator[Any]:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise ProblemError(503, "db-unavailable", "Database is unavailable")
    async with pool.acquire() as conn:
        yield conn


Conn = Annotated[Any, Depends(get_conn)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: uuid.UUID
    login: str
    display_name: str
    platform_role: str

    @property
    def is_admin(self) -> bool:
        return self.platform_role == "admin"


def _bearer_token(creds: HTTPAuthorizationCredentials | None) -> str:
    if creds is None or creds.scheme.lower() != "bearer":
        raise ProblemError(401, "unauthorized", "Missing bearer token")
    return creds.credentials


async def current_user(
    conn: Conn,
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> CurrentUser:
    try:
        claims = decode_token(settings, _bearer_token(creds), "access")
    except TokenError as exc:
        raise ProblemError(401, "unauthorized", "Invalid or expired token", str(exc)) from exc
    row = await conn.fetchrow(
        "SELECT id, login, display_name, platform_role, is_active FROM core.users WHERE id = $1",
        uuid.UUID(claims["sub"]),
    )
    if row is None or not row["is_active"]:
        raise ProblemError(401, "unauthorized", "User is not active")
    return CurrentUser(row["id"], row["login"], row["display_name"], row["platform_role"])


User = Annotated[CurrentUser, Depends(current_user)]


async def require_admin(user: User) -> CurrentUser:
    if not user.is_admin:
        raise ProblemError(403, "forbidden", "Admin role required")
    return user


Admin = Annotated[CurrentUser, Depends(require_admin)]


@dataclass(frozen=True, slots=True)
class CompanyAccess:
    company_id: str
    role: str


async def company_access(conn: Any, user: CurrentUser, company_id: str, settings: Settings) -> CompanyAccess:
    """Every request to company data is checked against user_company_access (SPEC §13)."""
    row = await conn.fetchrow(
        """SELECT a.role, c.client_type, c.is_valid
           FROM core.user_company_access a JOIN core.companies c ON c.id = a.company_id
           WHERE a.user_id = $1 AND a.company_id = $2""",
        user.id,
        company_id,
    )
    if row is None or not row["is_valid"] or row["client_type"] not in settings.eligible_client_types:
        raise ProblemError(403, "forbidden", "No access to this company", f"company_id={company_id}")
    return CompanyAccess(company_id, row["role"])


@dataclass(frozen=True, slots=True)
class RunScope:
    run_id: str
    company_id: str


async def run_scope(
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    x_run_id: Annotated[str | None, Header()] = None,
) -> RunScope:
    """Tools API: run-scoped service token + X-Run-Id must match the token's run."""
    try:
        claims = decode_token(settings, _bearer_token(creds), "run")
    except TokenError as exc:
        raise ProblemError(401, "unauthorized", "Invalid run token", str(exc)) from exc
    if not x_run_id or x_run_id != claims.get("run_id"):
        raise ProblemError(403, "run-mismatch", "X-Run-Id does not match the run token")
    return RunScope(run_id=claims["run_id"], company_id=claims["company_id"])


Scope = Annotated[RunScope, Depends(run_scope)]
