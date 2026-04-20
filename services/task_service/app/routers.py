from datetime import datetime
from math import ceil

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .audit import add_audit_log
from .events import TaskEventType, build_event_envelope
from .idempotency import compute_request_hash, create_idempotency_record, maybe_replay_idempotent_response
from .db import get_db
from .models import Task, TaskPriority, TaskStatus, TaskStatusHistory
from .outbox import create_outbox_event
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
from .rbac import apply_visibility_scope, can_delete_task, can_manage_task, get_task_if_visible
from .security import get_current_user

router = APIRouter(tags=["tasks"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
CREATE_TASK_OPERATION = "create_task"
CHANGE_TASK_STATUS_OPERATION = "change_task_status"

ALLOWED_STATUS_TRANSITIONS = {
    TaskStatus.TODO: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.REVIEW, TaskStatus.CANCELLED},
    TaskStatus.REVIEW: {TaskStatus.IN_PROGRESS, TaskStatus.DONE, TaskStatus.CANCELLED},
    TaskStatus.DONE: set(),
    TaskStatus.CANCELLED: set(),
}


def add_task_outbox_event(
    *,
    db: Session,
    event_type: TaskEventType,
    aggregate_id: str,
    payload: dict,
    correlation_id: str | None = None,
) -> None:
    envelope = build_event_envelope(
        event_type=event_type,
        payload=payload,
        correlation_id=correlation_id,
    )
    db.add(create_outbox_event(envelope=envelope, aggregate_type="task", aggregate_id=aggregate_id))



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
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", convert_underscores=False),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_hash = compute_request_hash(payload.model_dump(mode="json"))
    replay = maybe_replay_idempotent_response(
        db=db,
        idempotency_key=idempotency_key,
        operation=CREATE_TASK_OPERATION,
        actor_user_id=current_user.user_id,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay

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
    db.flush()
    db.refresh(task)

    response_payload = TaskResponse.model_validate(task).model_dump(mode="json")
    if idempotency_key:
        db.add(
            create_idempotency_record(
                idempotency_key=idempotency_key,
                operation=CREATE_TASK_OPERATION,
                actor_user_id=current_user.user_id,
                request_hash=request_hash,
                response_status=status.HTTP_201_CREATED,
                response_body=response_payload,
                resource_id=task.id,
            )
        )

    add_task_outbox_event(
        db=db,
        event_type=TaskEventType.CREATED,
        aggregate_id=task.id,
        correlation_id=idempotency_key,
        payload={
            "task_id": task.id,
            "owner_id": task.owner_id,
            "assignee_id": task.assignee_id,
            "team_id": task.team_id,
            "title": task.title,
            "status": task.status,
            "priority": task.priority,
            "deadline": task.deadline.isoformat() if task.deadline else None,
        },
    )
    add_audit_log(
        db=db,
        actor_user_id=current_user.user_id,
        action="task.created",
        target_type="task",
        target_id=task.id,
        details={
            "assignee_id": task.assignee_id,
            "team_id": task.team_id,
            "status": task.status,
            "priority": task.priority,
        },
    )
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

    normalized_changes: dict[str, object] = {}
    for field, value in changes.items():
        if field in {"title", "assignee_id", "team_id"} and value is not None:
            value = value.strip()
        setattr(task, field, value)
        normalized_changes[field] = value

    add_audit_log(
        db=db,
        actor_user_id=current_user.user_id,
        action="task.updated",
        target_type="task",
        target_id=task.id,
        details={"changes": normalized_changes},
    )
    db.commit()
    db.refresh(task)
    return task


@router.patch("/tasks/{task_id}/status", response_model=TaskResponse)
def change_task_status(
    task_id: str,
    payload: TaskStatusUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", convert_underscores=False),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_hash = compute_request_hash({"task_id": task_id, **payload.model_dump(mode="json")})
    replay = maybe_replay_idempotent_response(
        db=db,
        idempotency_key=idempotency_key,
        operation=CHANGE_TASK_STATUS_OPERATION,
        actor_user_id=current_user.user_id,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay

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
    db.flush()
    db.refresh(task)

    response_payload = TaskResponse.model_validate(task).model_dump(mode="json")
    if idempotency_key:
        db.add(
            create_idempotency_record(
                idempotency_key=idempotency_key,
                operation=CHANGE_TASK_STATUS_OPERATION,
                actor_user_id=current_user.user_id,
                request_hash=request_hash,
                response_status=status.HTTP_200_OK,
                response_body=response_payload,
                resource_id=task.id,
            )
        )

    add_task_outbox_event(
        db=db,
        event_type=TaskEventType.STATUS_CHANGED,
        aggregate_id=task.id,
        correlation_id=idempotency_key,
        payload={
            "task_id": task.id,
            "owner_id": task.owner_id,
            "assignee_id": task.assignee_id,
            "team_id": task.team_id,
            "from_status": current_status.value,
            "to_status": target_status.value,
            "comment": payload.comment,
        },
    )
    add_audit_log(
        db=db,
        actor_user_id=current_user.user_id,
        action="task.status_changed",
        target_type="task",
        target_id=task.id,
        details={"from": current_status.value, "to": target_status.value, "comment": payload.comment},
    )
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

    add_audit_log(
        db=db,
        actor_user_id=current_user.user_id,
        action="task.deleted",
        target_type="task",
        target_id=task.id,
        details={"team_id": task.team_id, "owner_id": task.owner_id, "assignee_id": task.assignee_id},
    )
    add_task_outbox_event(
        db=db,
        event_type=TaskEventType.DELETED,
        aggregate_id=task.id,
        payload={
            "task_id": task.id,
            "owner_id": task.owner_id,
            "assignee_id": task.assignee_id,
            "team_id": task.team_id,
            "status": task.status,
        },
    )
    db.delete(task)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
