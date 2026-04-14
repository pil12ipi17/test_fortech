from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Task, TaskPriority, TaskStatus, TaskStatusHistory
from .schemas import (
    CurrentUser,
    TaskCreate,
    TaskResponse,
    TaskStatusHistoryResponse,
    TaskStatusUpdate,
    TaskUpdate,
)
from .security import get_current_user

router = APIRouter(tags=["tasks"])

ALLOWED_STATUS_TRANSITIONS = {
    TaskStatus.TODO: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.REVIEW, TaskStatus.CANCELLED},
    TaskStatus.REVIEW: {TaskStatus.IN_PROGRESS, TaskStatus.DONE, TaskStatus.CANCELLED},
    TaskStatus.DONE: set(),
    TaskStatus.CANCELLED: set(),
}


def get_owned_task(*, db: Session, task_id: str, user_id: str) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.owner_id == user_id))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


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


@router.get("/tasks", response_model=list[TaskResponse])
def list_tasks(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tasks = db.scalars(select(Task).where(Task.owner_id == current_user.user_id).order_by(Task.created_at.desc())).all()
    return list(tasks)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_owned_task(db=db, task_id=task_id, user_id=current_user.user_id)


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: str,
    payload: TaskUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = get_owned_task(db=db, task_id=task_id, user_id=current_user.user_id)

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
    task = get_owned_task(db=db, task_id=task_id, user_id=current_user.user_id)

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
    get_owned_task(db=db, task_id=task_id, user_id=current_user.user_id)
    history_entries = db.scalars(
        select(TaskStatusHistory)
        .where(TaskStatusHistory.task_id == task_id)
        .order_by(TaskStatusHistory.changed_at.asc())
    ).all()
    return TaskStatusHistoryResponse(items=list(history_entries))


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = get_owned_task(db=db, task_id=task_id, user_id=current_user.user_id)

    db.delete(task)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)