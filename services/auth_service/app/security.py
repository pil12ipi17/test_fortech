import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt

from .config import Settings

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


def hash_password(password: str) -> str:
    iterations = 310_000
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    encoded = base64.b64encode(digest).decode("utf-8")
    return f"pbkdf2_sha256${iterations}${salt}${encoded}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, raw_iterations, salt, encoded_digest = password_hash.split("$", maxsplit=3)
    except ValueError:
        return False

    if algorithm != "pbkdf2_sha256":
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        int(raw_iterations),
    )
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, encoded_digest)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_token(
    *,
    user_id: str,
    email: str,
    settings: Settings,
    token_type: str,
    expires_delta: timedelta,
    jti: str | None = None,
    roles: list[str] | None = None,
    team_ids: list[str] | None = None,
) -> tuple[str, datetime, str | None]:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iss": settings.token_issuer,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if roles is not None:
        payload["roles"] = roles
    if team_ids is not None:
        payload["team_ids"] = team_ids
    if jti is not None:
        payload["jti"] = jti
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), payload["exp"], jti


def create_access_token(
    *,
    user_id: str,
    email: str,
    roles: list[str],
    team_ids: list[str],
    settings: Settings,
) -> str:
    token, _, _ = _create_token(
        user_id=user_id,
        email=email,
        settings=settings,
        token_type=ACCESS_TOKEN_TYPE,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        roles=roles,
        team_ids=team_ids,
    )
    return token


def create_refresh_token(*, user_id: str, email: str, settings: Settings) -> tuple[str, str, datetime]:
    token_jti = str(uuid4())
    token, expires_at, _ = _create_token(
        user_id=user_id,
        email=email,
        settings=settings,
        token_type=REFRESH_TOKEN_TYPE,
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
        jti=token_jti,
    )
    return token, token_jti, expires_at


def _decode_token(token: str, settings: Settings, *, expected_type: str) -> dict:
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.token_issuer,
    )
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("Invalid token type")
    return payload


def decode_access_token(token: str, settings: Settings) -> dict:
    return _decode_token(token, settings, expected_type=ACCESS_TOKEN_TYPE)


def decode_refresh_token(token: str, settings: Settings) -> dict:
    payload = _decode_token(token, settings, expected_type=REFRESH_TOKEN_TYPE)
    if "jti" not in payload:
        raise jwt.InvalidTokenError("Missing refresh token identifier")
    return payload