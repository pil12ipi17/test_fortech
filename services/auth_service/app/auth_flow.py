from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import add_audit_log
from .config import Settings
from .models import RefreshTokenSession, Role, RoleCode, Team, TeamMembership, User, UserRole
from .rbac import ROLE_SORT_ORDER, get_user_role_codes, get_user_team_ids, normalize_role_codes
from .schemas import AuthResponse, UserResponse
from .security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_user_response(*, user: User, db: Session) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        roles=get_user_role_codes(db, user.id),
        team_ids=get_user_team_ids(db, user.id),
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def resolve_roles(*, db: Session, role_codes: list[str]) -> list[Role]:
    normalized_codes = normalize_role_codes(role_codes)
    roles = list(db.scalars(select(Role).where(Role.code.in_(normalized_codes))).all()) if normalized_codes else []
    if len(roles) != len(normalized_codes):
        existing_codes = {role.code for role in roles}
        missing = [role_code for role_code in normalized_codes if role_code not in existing_codes]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown roles: {', '.join(missing)}",
        )
    return sorted(roles, key=lambda role: ROLE_SORT_ORDER.get(role.code, 999))


def ensure_teams_exist(*, db: Session, team_ids: list[str]) -> list[Team]:
    if not team_ids:
        return []
    teams = list(db.scalars(select(Team).where(Team.id.in_(team_ids))).all())
    found_ids = {team.id for team in teams}
    missing = [team_id for team_id in team_ids if team_id not in found_ids]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown teams: {', '.join(missing)}",
        )
    return teams


def set_user_roles(*, db: Session, user: User, roles: list[Role], assigned_by: str | None) -> None:
    existing_roles = db.scalars(select(UserRole).where(UserRole.user_id == user.id)).all()
    for existing_role in existing_roles:
        db.delete(existing_role)
    db.flush()
    for role in roles:
        db.add(UserRole(user_id=user.id, role_id=role.id, assigned_by=assigned_by))


def add_user_to_teams(*, db: Session, user: User, teams: list[Team]) -> None:
    if not teams:
        return
    existing_team_ids = set(db.scalars(select(TeamMembership.team_id).where(TeamMembership.user_id == user.id)).all())
    for team in teams:
        if team.id in existing_team_ids:
            continue
        db.add(TeamMembership(user_id=user.id, team_id=team.id))


def build_auth_response(*, user: User, db: Session, settings: Settings) -> AuthResponse:
    roles = get_user_role_codes(db, user.id)
    team_ids = get_user_team_ids(db, user.id)
    access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        roles=roles,
        team_ids=team_ids,
        settings=settings,
    )
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
    db.refresh(user)
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=build_user_response(user=user, db=db),
    )


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


def register_user_account(*, email: str, password: str, db: Session, settings: Settings) -> AuthResponse:
    normalized_email = email.strip().lower()
    existing_user = db.scalar(select(User).where(User.email == normalized_email))
    if existing_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")

    is_first_user = (db.scalar(select(func.count()).select_from(User)) or 0) == 0
    role_codes = [RoleCode.ADMIN.value, RoleCode.USER.value] if is_first_user else [RoleCode.USER.value]
    roles = resolve_roles(db=db, role_codes=role_codes)

    user = User(email=normalized_email, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    set_user_roles(db=db, user=user, roles=roles, assigned_by=user.id if is_first_user else None)
    db.commit()
    db.refresh(user)

    response = build_auth_response(user=user, db=db, settings=settings)
    add_audit_log(
        db=db,
        actor_user_id=user.id,
        action="auth.register",
        target_type="user",
        target_id=user.id,
        details={"email": user.email, "roles": response.user.roles},
    )
    db.commit()
    return response


def login_user_account(*, email: str, password: str, db: Session, settings: Settings) -> AuthResponse:
    normalized_email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == normalized_email))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    response = build_auth_response(user=user, db=db, settings=settings)
    add_audit_log(
        db=db,
        actor_user_id=user.id,
        action="auth.login",
        target_type="user",
        target_id=user.id,
        details={"email": user.email},
    )
    db.commit()
    return response


def refresh_user_tokens(*, refresh_token: str, db: Session, settings: Settings) -> AuthResponse:
    try:
        token_payload = decode_refresh_token(refresh_token, settings)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token") from exc

    session = get_refresh_session(db=db, refresh_token=refresh_token, payload=token_payload)
    now = datetime.now(timezone.utc)
    if session is None or session.revoked_at is not None or ensure_utc(session.expires_at) <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token is not active")

    user = db.get(User, token_payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    session.revoked_at = now
    db.flush()
    response = build_auth_response(user=user, db=db, settings=settings)
    add_audit_log(
        db=db,
        actor_user_id=user.id,
        action="auth.refresh",
        target_type="user",
        target_id=user.id,
        details={"email": user.email},
    )
    db.commit()
    return response


def logout_user_session(*, refresh_token: str, db: Session, settings: Settings) -> None:
    try:
        token_payload = decode_refresh_token(refresh_token, settings)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token") from exc

    session = get_refresh_session(db=db, refresh_token=refresh_token, payload=token_payload)
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        add_audit_log(
            db=db,
            actor_user_id=token_payload.get("sub"),
            action="auth.logout",
            target_type="refresh_session",
            target_id=session.id,
            details={"user_id": token_payload.get("sub")},
        )
        db.commit()
