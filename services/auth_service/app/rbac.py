from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .models import Role, RoleCode, TeamMembership, User, UserRole
from .schemas import CurrentPrincipal
from .security import decode_access_token

ROLE_SORT_ORDER = {
    RoleCode.USER.value: 0,
    RoleCode.TEAMLEAD.value: 1,
    RoleCode.ADMIN.value: 2,
}

bearer_scheme = HTTPBearer(auto_error=False)


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


def _build_forbidden_message(allowed_roles: list[str]) -> str:
    if allowed_roles == [RoleCode.ADMIN.value]:
        return "Admin role required"
    if allowed_roles == normalize_role_codes([RoleCode.ADMIN.value, RoleCode.TEAMLEAD.value]):
        return "Admin or teamlead role required"
    return f"Required one of roles: {', '.join(allowed_roles)}"


def require_roles(*allowed_roles: str):
    normalized_allowed_roles = normalize_role_codes(list(allowed_roles))
    forbidden_message = _build_forbidden_message(normalized_allowed_roles)

    def dependency(principal: CurrentPrincipal = Depends(get_current_principal)) -> CurrentPrincipal:
        if any(role in principal.roles for role in normalized_allowed_roles):
            return principal
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=forbidden_message)

    return dependency
