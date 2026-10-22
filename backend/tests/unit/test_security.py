import pytest

from app.core.config import Settings
from app.core.security import (
    TokenError,
    decode_token,
    hash_password,
    issue_run_token,
    issue_user_tokens,
    verify_password,
)

SETTINGS = Settings(jwt_secret="test-secret-test-secret-test-secret-123")  # type: ignore[arg-type]


def test_password_hash_roundtrip_and_salt() -> None:
    h1, h2 = hash_password("demo12345"), hash_password("demo12345")
    assert h1 != h2 and h1.startswith("scrypt$")
    assert verify_password("demo12345", h1) and not verify_password("wrong", h1)
    assert not verify_password("demo12345", "garbage")


def test_token_types_are_not_interchangeable() -> None:
    pair = issue_user_tokens(SETTINGS, "00000000-0000-4000-8000-000000000001", "user")
    assert decode_token(SETTINGS, pair.access_token, "access")["role"] == "user"
    with pytest.raises(TokenError):
        decode_token(SETTINGS, pair.access_token, "refresh")
    with pytest.raises(TokenError):
        decode_token(SETTINGS, pair.refresh_token, "access")


def test_run_token_is_scoped_to_run_and_company() -> None:
    token = issue_run_token(SETTINGS, "run_1", "U_1", ttl_s=60)
    claims = decode_token(SETTINGS, token, "run")
    assert claims["run_id"] == "run_1" and claims["company_id"] == "U_1"
    with pytest.raises(TokenError):
        decode_token(SETTINGS, token, "access")


def test_foreign_signature_rejected() -> None:
    other = Settings(jwt_secret="another-secret-another-secret-12345")  # type: ignore[arg-type]
    token = issue_run_token(other, "run_1", "U_1", ttl_s=60)
    with pytest.raises(TokenError):
        decode_token(SETTINGS, token, "run")


def test_expired_token_rejected() -> None:
    token = issue_run_token(SETTINGS, "run_1", "U_1", ttl_s=-1)
    with pytest.raises(TokenError, match="expired"):
        decode_token(SETTINGS, token, "run")
