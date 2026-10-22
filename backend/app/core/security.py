"""Passwords (stdlib scrypt) and JWTs: user access/refresh tokens and run-scoped agent tokens (ADR-010)."""

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import Settings

ALGORITHM = "HS256"
ISSUER = "alfa-future"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


class TokenError(Exception):
    pass


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    expected = base64.b64decode(digest_b64)
    actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
    return hmac.compare_digest(expected, actual)


def _encode(settings: Settings, claims: dict[str, Any], ttl: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {"iss": ISSUER, "iat": now, "exp": now + ttl, "jti": uuid.uuid4().hex, **claims}
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def decode_token(settings: Settings, token: str, expected_type: str) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token, settings.jwt_secret.get_secret_value(), algorithms=[ALGORITHM], issuer=ISSUER
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc
    if claims.get("typ") != expected_type:
        raise TokenError(f"expected {expected_type} token")
    return claims


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


def issue_user_tokens(settings: Settings, user_id: str, platform_role: str) -> TokenPair:
    access_ttl = timedelta(minutes=settings.jwt_access_ttl_min)
    claims = {"sub": user_id, "role": platform_role}
    return TokenPair(
        access_token=_encode(settings, {**claims, "typ": "access"}, access_ttl),
        refresh_token=_encode(
            settings, {**claims, "typ": "refresh"}, timedelta(days=settings.jwt_refresh_ttl_days)
        ),
        expires_in=int(access_ttl.total_seconds()),
    )


def issue_run_token(settings: Settings, run_id: str, company_id: str, ttl_s: int) -> str:
    """Agent token scoped to one run and one company (Tools API, LLM Gateway)."""
    claims = {"sub": "service:ml", "typ": "run", "run_id": run_id, "company_id": company_id}
    return _encode(settings, claims, timedelta(seconds=ttl_s))
