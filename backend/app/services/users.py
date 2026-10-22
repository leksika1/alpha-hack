"""Users, demo accounts and per-company roles."""

import uuid
from dataclasses import dataclass
from typing import Any

from app.core.security import hash_password, verify_password


@dataclass(frozen=True, slots=True)
class DemoAccess:
    company_id: str
    role: str
    is_default: bool = False


@dataclass(frozen=True, slots=True)
class DemoUser:
    login: str
    password: str
    display_name: str
    platform_role: str
    access: tuple[DemoAccess, ...]


async def authenticate(conn: Any, login: str, password: str) -> Any | None:
    row = await conn.fetchrow("SELECT * FROM core.users WHERE login = $1 AND is_active", login)
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    return row


async def upsert_user(conn: Any, user: DemoUser) -> uuid.UUID:
    """Idempotent: re-seeding resets the demo password and access list."""
    user_id: uuid.UUID = await conn.fetchval(
        """INSERT INTO core.users (login, password_hash, display_name, platform_role)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (login) DO UPDATE SET password_hash = EXCLUDED.password_hash,
               display_name = EXCLUDED.display_name, platform_role = EXCLUDED.platform_role, is_active = true
           RETURNING id""",
        user.login,
        hash_password(user.password),
        user.display_name,
        user.platform_role,
    )
    await conn.execute("DELETE FROM core.user_company_access WHERE user_id = $1", user_id)
    await conn.executemany(
        """INSERT INTO core.user_company_access (user_id, company_id, role, is_default) VALUES ($1, $2, $3,
               $4)""",
        [(user_id, a.company_id, a.role, a.is_default) for a in user.access],
    )
    return user_id


def company_title(okved_name: str | None, region: str | None) -> str:
    parts = [p for p in (okved_name, region) if p]
    return " · ".join(parts) if parts else "Компания"


async def user_companies(conn: Any, user_id: uuid.UUID, eligible_types: list[str]) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT a.company_id, a.role, a.is_default, c.okved_code, c.okved_name, c.region, c.segment
           FROM core.user_company_access a JOIN core.companies c ON c.id = a.company_id
           WHERE a.user_id = $1 AND c.is_valid AND c.client_type = ANY($2::text[])
           ORDER BY a.is_default DESC, a.company_id""",
        user_id,
        eligible_types,
    )
    return [
        {
            "company_id": r["company_id"],
            "title": company_title(r["okved_name"], r["region"]),
            "okved_code": r["okved_code"],
            "okved_name": r["okved_name"],
            "region": r["region"],
            "segment": r["segment"],
            "role": r["role"],
            "is_default": r["is_default"],
        }
        for r in rows
    ]


async def set_role(conn: Any, user_id: uuid.UUID, company_id: str, role: str) -> None:
    await conn.execute(
        "UPDATE core.user_company_access SET role = $3, updated_at = now() WHERE user_id = $1 AND "
        "company_id = $2",
        user_id,
        company_id,
        role,
    )
