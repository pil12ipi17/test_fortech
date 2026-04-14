import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .models import RefreshTokenSession, Role, RoleCode, Team, TeamMembership, User, UserRole
from .schemas import (
    AdminUserCreate,
    AuthResponse,
    CurrentPrincipal,
    PaginatedTeamsResponse,
    PaginatedUsersResponse,
    RefreshTokenRequest,
    RoleUpdateRequest,
    TeamCreateRequest,
    TeamMembershipCreateRequest,
    TeamMembershipResponse,
    TeamResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from .security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)

ROLE_SORT_ORDER = {
    RoleCode.USER.value: 0,
    RoleCode.TEAMLEAD.value: 1,
    RoleCode.ADMIN.value: 2,
}

router = APIRouter()
auth_router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])
teams_router = APIRouter(prefix="/teams", tags=["teams"])
bearer_scheme = HTTPBearer(auto_error=False)




def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_role_codes(role_codes: list[str]) -> list[str]:
    normalized = []
    seen: set[str] = set()
    for role_code in role_codes:
        cleaned = role_code.strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return sorted(normalized, key=lambda code: ROLE_SORT_ORDER.get(code, 999))


def get_user_role_codes(db: Session, user_id: str) -> list[str]:
    role_codes = db.scalars(
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    ).all()
    return normalize_role_codes(list(role_codes))


def get_user_team_ids(db: Session, user_id: str) -> list[str]:
    team_ids = db.scalars(
        select(TeamMembership.team_id)
        .where(TeamMembership.user_id == user_id)
        .order_by(TeamMembership.created_at.asc())
    ).all()
    return list(team_ids)


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


def build_team_response(*, team: Team, db: Session) -> TeamResponse:
    member_count = db.scalar(
        select(func.count()).select_from(TeamMembership).where(TeamMembership.team_id == team.id)
    ) or 0
    return TeamResponse(
        id=team.id,
        name=team.name,
        member_count=member_count,
        created_at=team.created_at,
    )


def paginate(*, total: int, page: int, page_size: int) -> tuple[int, int]:
    pages = math.ceil(total / page_size) if total else 0
    offset = (page - 1) * page_size
    return pages, offset


def resolve_roles(*, db: Session, role_codes: list[str]) -> list[Role]:
    normalized_codes = normalize_role_codes(role_codes)
    roles = db.scalars(select(Role).where(Role.code.in_(normalized_codes))).all() if normalized_codes else []
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
    teams = db.scalars(select(Team).where(Team.id.in_(team_ids))).all()
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
    existing_team_ids = set(
        db.scalars(select(TeamMembership.team_id).where(TeamMembership.user_id == user.id)).all()
    )
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


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CurrentPrincipal:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    try:
        payload = decode_access_token(credentials.credentials, settings)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    user = db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is not active")

    roles = normalize_role_codes(list(payload.get("roles") or get_user_role_codes(db, user.id)))
    team_ids = list(payload.get("team_ids") or get_user_team_ids(db, user.id))
    return CurrentPrincipal(user_id=user.id, email=user.email, roles=roles, team_ids=team_ids)


def require_admin(principal: CurrentPrincipal) -> None:
    if RoleCode.ADMIN.value not in principal.roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")


def require_admin_or_teamlead(principal: CurrentPrincipal) -> None:
    if RoleCode.ADMIN.value in principal.roles or RoleCode.TEAMLEAD.value in principal.roles:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin or teamlead role required")


@auth_router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    normalized_email = payload.email.strip().lower()
    existing_user = db.scalar(select(User).where(User.email == normalized_email))
    if existing_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")

    is_first_user = (db.scalar(select(func.count()).select_from(User)) or 0) == 0
    role_codes = [RoleCode.ADMIN.value, RoleCode.USER.value] if is_first_user else [RoleCode.USER.value]
    roles = resolve_roles(db=db, role_codes=role_codes)

    user = User(email=normalized_email, password_hash=hash_password(payload.password))
    db.add(user)
    db.flush()
    set_user_roles(db=db, user=user, roles=roles, assigned_by=user.id if is_first_user else None)
    db.commit()
    db.refresh(user)

    return build_auth_response(user=user, db=db, settings=settings)


@auth_router.post("/login", response_model=AuthResponse)
def login_user(
    payload: UserLogin,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    normalized_email = payload.email.strip().lower()
    user = db.scalar(select(User).where(User.email == normalized_email))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    return build_auth_response(user=user, db=db, settings=settings)


@auth_router.post("/refresh", response_model=AuthResponse)
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
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    session.revoked_at = now
    db.flush()
    return build_auth_response(user=user, db=db, settings=settings)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
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


@auth_router.get("/me", response_model=UserResponse)
def me(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    user = db.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return build_user_response(user=user, db=db)


@users_router.get("", response_model=PaginatedUsersResponse)
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    role: str | None = Query(default=None),
    team_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    require_admin(principal)

    statement = select(User).distinct().order_by(User.created_at.desc())
    if role:
        normalized_role = role.strip().lower()
        if normalized_role not in ROLE_SORT_ORDER:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown role filter")
        statement = (
            statement.join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.code == normalized_role)
        )
    if team_id:
        statement = statement.join(TeamMembership, TeamMembership.user_id == User.id).where(TeamMembership.team_id == team_id)

    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    pages, offset = paginate(total=total, page=page, page_size=page_size)
    users = db.scalars(statement.offset(offset).limit(page_size)).all()
    return PaginatedUsersResponse(
        items=[build_user_response(user=user, db=db) for user in users],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@users_router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user_by_admin(
    payload: AdminUserCreate,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    require_admin(principal)

    normalized_email = payload.email.strip().lower()
    if db.scalar(select(User).where(User.email == normalized_email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")

    roles = resolve_roles(db=db, role_codes=payload.roles)
    teams = ensure_teams_exist(db=db, team_ids=payload.team_ids)

    user = User(email=normalized_email, password_hash=hash_password(payload.password))
    db.add(user)
    db.flush()
    set_user_roles(db=db, user=user, roles=roles, assigned_by=principal.user_id)
    add_user_to_teams(db=db, user=user, teams=teams)
    db.commit()
    db.refresh(user)
    return build_user_response(user=user, db=db)


@users_router.patch("/{user_id}/roles", response_model=UserResponse)
def replace_user_roles(
    user_id: str,
    payload: RoleUpdateRequest,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    require_admin(principal)

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    roles = resolve_roles(db=db, role_codes=payload.roles)
    set_user_roles(db=db, user=user, roles=roles, assigned_by=principal.user_id)
    db.commit()
    db.refresh(user)
    return build_user_response(user=user, db=db)


@teams_router.get("", response_model=PaginatedTeamsResponse)
def list_teams(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    require_admin_or_teamlead(principal)

    statement = select(Team).order_by(Team.created_at.desc())
    if RoleCode.ADMIN.value not in principal.roles:
        if not principal.team_ids:
            return PaginatedTeamsResponse(items=[], total=0, page=page, page_size=page_size, pages=0)
        statement = statement.where(Team.id.in_(principal.team_ids))

    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    pages, offset = paginate(total=total, page=page, page_size=page_size)
    teams = db.scalars(statement.offset(offset).limit(page_size)).all()
    return PaginatedTeamsResponse(
        items=[build_team_response(team=team, db=db) for team in teams],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@teams_router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
def create_team(
    payload: TeamCreateRequest,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    require_admin(principal)

    normalized_name = payload.name.strip()
    if db.scalar(select(Team).where(Team.name == normalized_name)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Team already exists")

    team = Team(name=normalized_name)
    db.add(team)
    db.commit()
    db.refresh(team)
    return build_team_response(team=team, db=db)


@teams_router.post("/{team_id}/members", response_model=TeamMembershipResponse, status_code=status.HTTP_201_CREATED)
def add_team_member(
    team_id: str,
    payload: TeamMembershipCreateRequest,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    require_admin(principal)

    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    user = db.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    existing_membership = db.scalar(
        select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == payload.user_id)
    )
    if existing_membership is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is already in the team")

    membership = TeamMembership(user_id=payload.user_id, team_id=team_id)
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return TeamMembershipResponse(team_id=team_id, user_id=payload.user_id, added_at=membership.created_at)


@teams_router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_team_member(
    team_id: str,
    user_id: str,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    require_admin(principal)

    membership = db.scalar(
        select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team membership not found")

    db.delete(membership)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

for child_router in (auth_router, users_router, teams_router):
    router.include_router(child_router)
