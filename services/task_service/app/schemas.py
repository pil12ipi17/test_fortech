from datetime import datetime

from pydantic import BaseModel, Field

from .models import TaskStatus


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: TaskStatus = TaskStatus.TODO


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: TaskStatus | None = None


class TaskResponse(BaseModel):
    id: str
    owner_id: str
    title: str
    description: str | None
    status: TaskStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CurrentUser(BaseModel):
    user_id: str
    email: str | None = None

