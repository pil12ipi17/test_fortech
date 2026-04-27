from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..tasks.models import Task
from ..api.schemas import CurrentUser

ADMIN_ROLE = "admin"
TEAMLEAD_ROLE = "teamlead"


def can_view_task(*, task: Task, current_user: CurrentUser) -> bool:
    if ADMIN_ROLE in current_user.roles:
        return True
    if TEAMLEAD_ROLE in current_user.roles and task.team_id in current_user.team_ids:
        return True
    return current_user.user_id in {task.owner_id, task.assignee_id}


def can_manage_task(*, task: Task, current_user: CurrentUser) -> bool:
    return can_view_task(task=task, current_user=current_user)


def can_delete_task(*, task: Task, current_user: CurrentUser) -> bool:
    if ADMIN_ROLE in current_user.roles:
        return True
    if TEAMLEAD_ROLE in current_user.roles and task.team_id in current_user.team_ids:
        return True
    return current_user.user_id == task.owner_id


def get_task_if_visible(*, db: Session, task_id: str, current_user: CurrentUser) -> Task:
    task = db.get(Task, task_id)
    if task is None or not can_view_task(task=task, current_user=current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


def apply_visibility_scope(*, statement: Any, current_user: CurrentUser):
    if ADMIN_ROLE in current_user.roles:
        return statement
    if TEAMLEAD_ROLE in current_user.roles and current_user.team_ids:
        return statement.where(Task.team_id.in_(current_user.team_ids))
    return statement.where(or_(Task.owner_id == current_user.user_id, Task.assignee_id == current_user.user_id))
