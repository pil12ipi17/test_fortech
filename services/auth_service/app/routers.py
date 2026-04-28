import logging
import math

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .models import Role, RoleCode, Team, TeamMembership, User, UserRole
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
from .rbac import ROLE_SORT_ORDER, get_current_principal, get_user_role_codes, require_roles
from .audit import add_audit_log
from .auth_flow import (
    add_user_to_teams,
    build_auth_response,
    build_user_response,
    ensure_teams_exist,
    login_user_account,
    logout_user_session,
    refresh_user_tokens,
    register_user_account,
    resolve_roles,
    set_user_roles,
)
from .security import hash_password
from .redis_client import get_redis_client
from shared.rate_limit import check_fixed_window_rate_limit

logger = logging.getLogger("auth-service.rate-limit")

router = APIRouter()
auth_router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])
teams_router = APIRouter(prefix="/teams", tags=["teams"])



def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client is None:
        return "unknown"
    return request.client.host


def enforce_login_rate_limit(*, request: Request, payload: UserLogin, settings: Settings) -> None:
    normalized_email = payload.email.strip().lower()
    client_ip = get_client_ip(request)
    key = f"rl:login:{client_ip}:{normalized_email}"
    result = check_fixed_window_rate_limit(
        redis_client=get_redis_client(settings),
        key=key,
        limit=settings.login_rate_limit_requests,
        window_seconds=settings.login_rate_limit_window_seconds,
    )
    if result.allowed:
        return

    logger.warning(
        "rate_limit_exceeded scope=auth.login key=%s retry_after=%s",
        key,
        result.retry_after_seconds,
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many login attempts",
        headers={"Retry-After": str(result.retry_after_seconds)},
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


@auth_router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return register_user_account(email=payload.email, password=payload.password, db=db, settings=settings)


@auth_router.post("/login", response_model=AuthResponse)
def login_user(
    payload: UserLogin,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    enforce_login_rate_limit(request=request, payload=payload, settings=settings)
    return login_user_account(email=payload.email, password=payload.password, db=db, settings=settings)


@auth_router.post("/refresh", response_model=AuthResponse)
def refresh_tokens(
    payload: RefreshTokenRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return refresh_user_tokens(refresh_token=payload.refresh_token, db=db, settings=settings)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_user(
    payload: RefreshTokenRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    logout_user_session(refresh_token=payload.refresh_token, db=db, settings=settings)
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
    principal: CurrentPrincipal = Depends(require_roles(RoleCode.ADMIN.value)),
    db: Session = Depends(get_db),
):

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
    principal: CurrentPrincipal = Depends(require_roles(RoleCode.ADMIN.value)),
    db: Session = Depends(get_db),
):

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
    add_audit_log(
        db=db,
        actor_user_id=principal.user_id,
        action="user.created",
        target_type="user",
        target_id=user.id,
        details={"email": normalized_email, "roles": payload.roles, "team_ids": payload.team_ids},
    )
    db.commit()
    db.refresh(user)
    return build_user_response(user=user, db=db)


@users_router.patch("/{user_id}/roles", response_model=UserResponse)
def replace_user_roles(
    user_id: str,
    payload: RoleUpdateRequest,
    principal: CurrentPrincipal = Depends(require_roles(RoleCode.ADMIN.value)),
    db: Session = Depends(get_db),
):

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    previous_roles = get_user_role_codes(db, user.id)
    roles = resolve_roles(db=db, role_codes=payload.roles)
    set_user_roles(db=db, user=user, roles=roles, assigned_by=principal.user_id)
    add_audit_log(
        db=db,
        actor_user_id=principal.user_id,
        action="user.roles_updated",
        target_type="user",
        target_id=user.id,
        details={"before": previous_roles, "after": payload.roles},
    )
    db.commit()
    db.refresh(user)
    return build_user_response(user=user, db=db)


@teams_router.get("", response_model=PaginatedTeamsResponse)
def list_teams(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_roles(RoleCode.ADMIN.value, RoleCode.TEAMLEAD.value)),
    db: Session = Depends(get_db),
):

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
    principal: CurrentPrincipal = Depends(require_roles(RoleCode.ADMIN.value)),
    db: Session = Depends(get_db),
):

    normalized_name = payload.name.strip()
    if db.scalar(select(Team).where(Team.name == normalized_name)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Team already exists")

    team = Team(name=normalized_name)
    db.add(team)
    db.flush()
    add_audit_log(
        db=db,
        actor_user_id=principal.user_id,
        action="team.created",
        target_type="team",
        target_id=team.id,
        details={"name": normalized_name},
    )
    db.commit()
    db.refresh(team)
    return build_team_response(team=team, db=db)


@teams_router.post("/{team_id}/members", response_model=TeamMembershipResponse, status_code=status.HTTP_201_CREATED)
def add_team_member(
    team_id: str,
    payload: TeamMembershipCreateRequest,
    principal: CurrentPrincipal = Depends(require_roles(RoleCode.ADMIN.value)),
    db: Session = Depends(get_db),
):

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
    db.flush()
    add_audit_log(
        db=db,
        actor_user_id=principal.user_id,
        action="team.member_added",
        target_type="team_membership",
        target_id=membership.id,
        details={"team_id": team_id, "user_id": payload.user_id},
    )
    db.commit()
    db.refresh(membership)
    return TeamMembershipResponse(team_id=team_id, user_id=payload.user_id, added_at=membership.created_at)


@teams_router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_team_member(
    team_id: str,
    user_id: str,
    principal: CurrentPrincipal = Depends(require_roles(RoleCode.ADMIN.value)),
    db: Session = Depends(get_db),
):

    membership = db.scalar(
        select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team membership not found")

    add_audit_log(
        db=db,
        actor_user_id=principal.user_id,
        action="team.member_removed",
        target_type="team_membership",
        target_id=membership.id,
        details={"team_id": team_id, "user_id": user_id},
    )
    db.delete(membership)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


for child_router in (auth_router, users_router, teams_router):
    router.include_router(child_router)