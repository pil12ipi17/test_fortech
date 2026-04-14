from datetime import datetime
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Task, TaskPriority, TaskStatus, TaskStatusHistory
from .schemas import (
    CurrentUser,
    SortOrder,
    TaskCreate,
    TaskListResponse,
    TaskResponse,
    TaskSortBy,
    TaskStatusHistoryResponse,
    TaskStatusUpdate,
    TaskUpdate,
)
from .security import get_current_user

router = APIRouter(tags=["tasks"])

ADMIN_ROLE = "admin"
TEAMLEAD_ROLE = "teamlead"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

ALLOWED_STATUS_TRANSITIONS = {
    TaskStatus.TODO: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.REVIEW, TaskStatus.CANCELLED},
    TaskStatus.REVIEW: {TaskStatus.IN_PROGRESS, TaskStatus.DONE, TaskStatus.CANCELLED},
    TaskStatus.DONE: set(),
    TaskStatus.CANCELLED: set(),
}


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


def apply_visibility_scope(*, statement, current_user: CurrentUser):
    if ADMIN_ROLE in current_user.roles:
        return statement
    if TEAMLEAD_ROLE in current_user.roles and current_user.team_ids:
        return statement.where(Task.team_id.in_(current_user.team_ids))
    return statement.where(or_(Task.owner_id == current_user.user_id, Task.assignee_id == current_user.user_id))


def build_order_clauses(*, sort_by: TaskSortBy, sort_order: SortOrder):
    descending = sort_order == SortOrder.DESC

    if sort_by == TaskSortBy.PRIORITY:
        priority_rank = case(
            (Task.priority == TaskPriority.LOW.value, 1),
            (Task.priority == TaskPriority.MEDIUM.value, 2),
            (Task.priority == TaskPriority.HIGH.value, 3),
            else_=99,
        )
        primary = priority_rank.desc() if descending else priority_rank.asc()
        return [primary, Task.created_at.desc()]

    if sort_by == TaskSortBy.DEADLINE:
        deadline_present = case((Task.deadline.is_(None), 1), else_=0).asc()
        primary = Task.deadline.desc() if descending else Task.deadline.asc()
        return [deadline_present, primary, Task.created_at.desc()]

    if sort_by == TaskSortBy.UPDATED_AT:
        primary = Task.updated_at.desc() if descending else Task.updated_at.asc()
        return [primary, Task.created_at.desc()]

    primary = Task.created_at.desc() if descending else Task.created_at.asc()
    return [primary, Task.id.asc()]


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = Task(
        owner_id=current_user.user_id,
        assignee_id=payload.assignee_id.strip(),
        team_id=payload.team_id.strip(),
        title=payload.title.strip(),
        description=payload.description,
        status=TaskStatus.TODO,
        priority=payload.priority,
        deadline=payload.deadline,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("/tasks", response_model=TaskListResponse)
def list_tasks(
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    priority: TaskPriority | None = None,
    deadline_from: datetime | None = None,
    deadline_to: datetime | None = None,
    assignee_id: str | None = Query(default=None, min_length=1, max_length=36),
    owner_id: str | None = Query(default=None, min_length=1, max_length=36),
    team_id: str | None = Query(default=None, min_length=1, max_length=36),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    sort_by: TaskSortBy = TaskSortBy.CREATED_AT,
    sort_order: SortOrder = SortOrder.DESC,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = apply_visibility_scope(statement=select(Task), current_user=current_user)

    if status_filter is not None:
        statement = statement.where(Task.status == status_filter)
    if priority is not None:
        statement = statement.where(Task.priority == priority)
    if deadline_from is not None:
        statement = statement.where(Task.deadline.is_not(None), Task.deadline >= deadline_from)
    if deadline_to is not None:
        statement = statement.where(Task.deadline.is_not(None), Task.deadline <= deadline_to)
    if assignee_id is not None:
        statement = statement.where(Task.assignee_id == assignee_id.strip())
    if owner_id is not None:
        statement = statement.where(Task.owner_id == owner_id.strip())
    if team_id is not None:
        statement = statement.where(Task.team_id == team_id.strip())

    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    pages = ceil(total / page_size) if total else 0

    tasks = db.scalars(
        statement.order_by(*build_order_clauses(sort_by=sort_by, sort_order=sort_order))
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return TaskListResponse(items=list(tasks), total=total, page=page, page_size=page_size, pages=pages)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_task_if_visible(db=db, task_id=task_id, current_user=current_user)


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: str,
    payload: TaskUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = get_task_if_visible(db=db, task_id=task_id, current_user=current_user)
    if not can_manage_task(task=task, current_user=current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Task update is forbidden")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No task fields provided")

    for field, value in changes.items():
        if field in {"title", "assignee_id", "team_id"} and value is not None:
            value = value.strip()
        setattr(task, field, value)

    db.commit()
    db.refresh(task)
    return task


@router.patch("/tasks/{task_id}/status", response_model=TaskResponse)
def change_task_status(
    task_id: str,
    payload: TaskStatusUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = get_task_if_visible(db=db, task_id=task_id, current_user=current_user)
    if not can_manage_task(task=task, current_user=current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Task status update is forbidden")

    current_status = TaskStatus(task.status)
    target_status = payload.status
    if target_status not in ALLOWED_STATUS_TRANSITIONS[current_status]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invalid task status transition")

    history_entry = TaskStatusHistory(
        task_id=task.id,
        from_status=current_status,
        to_status=target_status,
        changed_by=current_user.user_id,
        comment=payload.comment,
    )
    task.status = target_status
    db.add(history_entry)
    db.commit()
    db.refresh(task)
    return task


@router.get("/tasks/{task_id}/history", response_model=TaskStatusHistoryResponse)
def get_task_history(
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = get_task_if_visible(db=db, task_id=task_id, current_user=current_user)
    history_entries = db.scalars(
        select(TaskStatusHistory)
        .where(TaskStatusHistory.task_id == task.id)
        .order_by(TaskStatusHistory.changed_at.asc())
    ).all()
    return TaskStatusHistoryResponse(items=list(history_entries))


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = get_task_if_visible(db=db, task_id=task_id, current_user=current_user)
    if not can_delete_task(task=task, current_user=current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Task deletion is forbidden")

    db.delete(task)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)