import logging
from datetime import datetime, timezone
from math import ceil

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from shared.rate_limit import check_fixed_window_rate_limit
from shared.tracing import get_request_correlation_id, get_tracer

from services.audit_service.app.report import generate_audit_report as build_audit_report
from ..tasks.audit_log import add_audit_log
from ..tasks.events import TaskEventType, build_event_envelope
from ..core.idempotency import compute_request_hash, create_idempotency_record, maybe_replay_idempotent_response
from ..core.db import get_db
from ..tasks.models import OutboxEvent, ProcessedEvent, Task, TaskPriority, TaskStatus, TaskStatusHistory, WorkerError, WorkerEventLog
from ..tasks.outbox import create_outbox_event
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
from ..core.rbac import apply_visibility_scope, can_delete_task, can_manage_task, get_task_if_visible
from ..core.security import get_current_user
from ..core.config import Settings, get_settings
from ..cache.redis_client import get_redis_client
from ..cache.task_cache import build_task_item_cache_key, get_cached_json, invalidate_task_cache, set_cached_json

logger = logging.getLogger("task-service.rate-limit")
tracer = get_tracer("task-service")

router = APIRouter(tags=["tasks"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
CREATE_TASK_OPERATION = "create_task"
CHANGE_TASK_STATUS_OPERATION = "change_task_status"
ADMIN_ROLE = "admin"


def _datetime_age_seconds(value: datetime | None) -> int | None:
    if value is None:
        return None
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - normalized).total_seconds()))


def _count_grouped_by(db: Session, column) -> dict[str, int]:
    rows = db.execute(select(column, func.count()).group_by(column)).all()
    return {str(key): int(count) for key, count in rows}

ALLOWED_STATUS_TRANSITIONS = {
    TaskStatus.TODO: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.REVIEW, TaskStatus.CANCELLED},
    TaskStatus.REVIEW: {TaskStatus.IN_PROGRESS, TaskStatus.DONE, TaskStatus.CANCELLED},
    TaskStatus.DONE: set(),
    TaskStatus.CANCELLED: set(),
}



def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client is None:
        return "unknown"
    return request.client.host


def enforce_task_write_rate_limit(*, request: Request, current_user: CurrentUser, settings: Settings) -> None:
    client_ip = get_client_ip(request)
    key = f"rl:tasks:write:{current_user.user_id}:{client_ip}"
    result = check_fixed_window_rate_limit(
        redis_client=get_redis_client(settings),
        key=key,
        limit=settings.task_write_rate_limit_requests,
        window_seconds=settings.task_write_rate_limit_window_seconds,
    )
    if result.allowed:
        return

    logger.warning(
        "rate_limit_exceeded scope=tasks.write key=%s retry_after=%s",
        key,
        result.retry_after_seconds,
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many task write requests",
        headers={"Retry-After": str(result.retry_after_seconds)},
    )


def add_task_outbox_event(
    *,
    db: Session,
    event_type: TaskEventType,
    aggregate_id: str,
    payload: dict,
    correlation_id: str | None = None,
) -> None:
    with tracer.start_as_current_span("task.outbox_event.create") as span:
        span.set_attribute("event.type", event_type.value)
        span.set_attribute("outbox.aggregate_type", "task")
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
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", convert_underscores=False),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    enforce_task_write_rate_limit(request=request, current_user=current_user, settings=settings)
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

    correlation_id = get_request_correlation_id(request)
    with tracer.start_as_current_span("task.create.persist") as span:
        span.set_attribute("task.priority", payload.priority.value)
        span.set_attribute("task.has_deadline", payload.deadline is not None)
        span.set_attribute("task.has_assignee", bool(payload.assignee_id.strip()))
        span.set_attribute("task.has_team", bool(payload.team_id.strip()))
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
        correlation_id=correlation_id,
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
    invalidate_task_cache(get_redis_client(settings))
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



@router.post("/audit/report", tags=["audit"])
def generate_audit_report_endpoint(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if ADMIN_ROLE not in current_user.roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Audit report generation is forbidden")

    output_path = build_audit_report(
        db=db,
        output_path=settings.audit_report_path,
        auth_database_url=settings.auth_database_url,
    )
    return {
        "filename": output_path.name,
        "path": str(output_path),
        "size_bytes": output_path.stat().st_size,
    }



@router.get("/events/metrics", tags=["events"])
def get_event_pipeline_metrics(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if ADMIN_ROLE not in current_user.roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Event metrics are forbidden")

    oldest_pending_created_at = db.scalar(
        select(func.min(OutboxEvent.created_at)).where(OutboxEvent.status == "pending")
    )
    dlq_approx_count = db.scalar(
        select(func.count()).where(WorkerError.retry_count >= settings.worker_max_retry_attempts)
    ) or 0
    retry_count = db.scalar(select(func.coalesce(func.sum(WorkerError.retry_count), 0))) or 0

    return {
        "outbox": {
            "by_status": _count_grouped_by(db, OutboxEvent.status),
            "by_event_type": _count_grouped_by(db, OutboxEvent.event_type),
            "pending_lag_seconds": _datetime_age_seconds(oldest_pending_created_at),
        },
        "workers": {
            "processed_by_consumer": _count_grouped_by(db, ProcessedEvent.consumer_name),
            "logs_by_event_type": _count_grouped_by(db, WorkerEventLog.event_type),
            "errors_by_consumer": _count_grouped_by(db, WorkerError.consumer_name),
            "retry_count": int(retry_count),
            "dlq_approx_count": int(dlq_approx_count),
        },
    }

@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    redis_client = get_redis_client(settings)
    cache_key = build_task_item_cache_key(current_user=current_user, task_id=task_id)
    cached = get_cached_json(redis_client=redis_client, key=cache_key)
    if cached is not None:
        return cached

    task = get_task_if_visible(db=db, task_id=task_id, current_user=current_user)
    response = TaskResponse.model_validate(task)
    set_cached_json(
        redis_client=redis_client,
        key=cache_key,
        value=response.model_dump(mode="json"),
        ttl_seconds=settings.task_cache_ttl_seconds,
    )
    return response

@router.patch("/tasks/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: str,
    payload: TaskUpdate,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    enforce_task_write_rate_limit(request=request, current_user=current_user, settings=settings)
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
    invalidate_task_cache(get_redis_client(settings))
    return task


@router.patch("/tasks/{task_id}/status", response_model=TaskResponse)
def change_task_status(
    task_id: str,
    payload: TaskStatusUpdate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", convert_underscores=False),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    enforce_task_write_rate_limit(request=request, current_user=current_user, settings=settings)
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

    correlation_id = get_request_correlation_id(request)
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
        correlation_id=correlation_id,
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
    invalidate_task_cache(get_redis_client(settings))
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
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    enforce_task_write_rate_limit(request=request, current_user=current_user, settings=settings)
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
    invalidate_task_cache(get_redis_client(settings))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
