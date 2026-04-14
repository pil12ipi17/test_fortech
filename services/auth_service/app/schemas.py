from datetime import datetime

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)


class AdminUserCreate(UserCreate):
    roles: list[str] = Field(min_length=1)
    team_ids: list[str] = Field(default_factory=list)


class UserLogin(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    is_active: bool
    roles: list[str]
    team_ids: list[str]
    created_at: datetime
    updated_at: datetime


class PaginatedUsersResponse(BaseModel):
    items: list[UserResponse]
    total: int
    page: int
    page_size: int
    pages: int


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthResponse(TokenPairResponse):
    user: UserResponse


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class RoleUpdateRequest(BaseModel):
    roles: list[str] = Field(min_length=1)


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TeamResponse(BaseModel):
    id: str
    name: str
    member_count: int
    created_at: datetime


class PaginatedTeamsResponse(BaseModel):
    items: list[TeamResponse]
    total: int
    page: int
    page_size: int
    pages: int


class TeamMembershipCreateRequest(BaseModel):
    user_id: str


class TeamMembershipResponse(BaseModel):
    team_id: str
    user_id: str
    added_at: datetime


class CurrentPrincipal(BaseModel):
    user_id: str
    email: str
    roles: list[str]
    team_ids: list[str]