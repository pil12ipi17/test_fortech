from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .models import RefreshTokenSession, User
from .schemas import AuthResponse, RefreshTokenRequest, UserCreate, UserLogin, UserResponse
from .security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
bearer_scheme = HTTPBearer(auto_error=False)


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_auth_response(*, user: User, db: Session, settings: Settings) -> AuthResponse:
    access_token = create_access_token(user_id=user.id, email=user.email, settings=settings)
    refresh_token, refresh_jti, refresh_expires_at = create_refresh_token(
        user_id=user.id,
        email=user.email,
        settings=settings,
    )
    db.add(
        RefreshTokenSession(
            token_jti=refresh_jti,
            token_hash=hash_token(refresh_token),
            user_id=user.id,
            expires_at=refresh_expires_at,
        )
    )
    db.commit()
    return AuthResponse(access_token=access_token, refresh_token=refresh_token, user=user)


def get_refresh_session(*, db: Session, refresh_token: str, payload: dict) -> RefreshTokenSession | None:
    session = db.scalar(
        select(RefreshTokenSession).where(
            RefreshTokenSession.token_jti == payload["jti"],
            RefreshTokenSession.user_id == payload["sub"],
        )
    )
    if session is None:
        return None
    if session.token_hash != hash_token(refresh_token):
        return None
    return session


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    normalized_email = payload.email.strip().lower()
    existing_user = db.scalar(select(User).where(User.email == normalized_email))
    if existing_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")

    user = User(email=normalized_email, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    return build_auth_response(user=user, db=db, settings=settings)


@router.post("/login", response_model=AuthResponse)
def login_user(
    payload: UserLogin,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    normalized_email = payload.email.strip().lower()
    user = db.scalar(select(User).where(User.email == normalized_email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    return build_auth_response(user=user, db=db, settings=settings)


@router.post("/refresh", response_model=AuthResponse)
def refresh_tokens(
    payload: RefreshTokenRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        token_payload = decode_refresh_token(payload.refresh_token, settings)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token") from exc

    session = get_refresh_session(db=db, refresh_token=payload.refresh_token, payload=token_payload)
    now = datetime.now(timezone.utc)
    if session is None or session.revoked_at is not None or ensure_utc(session.expires_at) <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token is not active")

    user = db.get(User, token_payload["sub"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    session.revoked_at = now
    db.flush()
    response = build_auth_response(user=user, db=db, settings=settings)
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_user(
    payload: RefreshTokenRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        token_payload = decode_refresh_token(payload.refresh_token, settings)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token") from exc

    session = get_refresh_session(db=db, refresh_token=payload.refresh_token, payload=token_payload)
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    try:
        payload = decode_access_token(credentials.credentials, settings)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    user = db.get(User, payload["sub"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return user
