from datetime import datetime

from pydantic import BaseModel, Field

from .models import TaskPriority, TaskStatus


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    assignee_id: str = Field(min_length=1, max_length=36)
    team_id: str = Field(min_length=1, max_length=36)
    priority: TaskPriority
    deadline: datetime | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    assignee_id: str | None = Field(default=None, min_length=1, max_length=36)
    team_id: str | None = Field(default=None, min_length=1, max_length=36)
    priority: TaskPriority | None = None
    deadline: datetime | None = None


class TaskStatusUpdate(BaseModel):
    status: TaskStatus
    comment: str | None = Field(default=None, max_length=1000)


class TaskResponse(BaseModel):
    id: str
    owner_id: str
    assignee_id: str
    team_id: str
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    deadline: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskStatusHistoryEntry(BaseModel):
    id: str
    task_id: str
    from_status: TaskStatus
    to_status: TaskStatus
    changed_by: str
    comment: str | None
    changed_at: datetime

    model_config = {"from_attributes": True}


class TaskStatusHistoryResponse(BaseModel):
    items: list[TaskStatusHistoryEntry]


class CurrentUser(BaseModel):
    user_id: str
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    team_ids: list[str] = Field(default_factory=list)