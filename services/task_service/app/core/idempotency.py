import hashlib
import json
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..tasks.models import IdempotencyKey

IDEMPOTENCY_TTL = timedelta(hours=24)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def compute_request_hash(payload: dict) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def get_active_idempotency_record(
    *,
    db: Session,
    idempotency_key: str,
    operation: str,
    actor_user_id: str,
) -> IdempotencyKey | None:
    record = db.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.idempotency_key == idempotency_key,
            IdempotencyKey.operation == operation,
            IdempotencyKey.actor_user_id == actor_user_id,
        )
    )
    if record is None:
        return None

    now = datetime.now(UTC)
    if ensure_utc(record.expires_at) <= now:
        db.delete(record)
        db.flush()
        return None
    return record


def return_stored_response(record: IdempotencyKey) -> JSONResponse:
    return JSONResponse(status_code=record.response_status, content=json.loads(record.response_body))


def create_idempotency_record(
    *,
    idempotency_key: str,
    operation: str,
    actor_user_id: str,
    request_hash: str,
    response_status: int,
    response_body: dict,
    resource_id: str | None,
) -> IdempotencyKey:
    now = datetime.now(UTC)
    return IdempotencyKey(
        idempotency_key=idempotency_key,
        operation=operation,
        actor_user_id=actor_user_id,
        request_hash=request_hash,
        response_status=response_status,
        response_body=json.dumps(response_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        resource_id=resource_id,
        created_at=now,
        expires_at=now + IDEMPOTENCY_TTL,
    )


def maybe_replay_idempotent_response(
    *,
    db: Session,
    idempotency_key: str | None,
    operation: str,
    actor_user_id: str,
    request_hash: str,
) -> JSONResponse | None:
    if not idempotency_key:
        return None

    record = get_active_idempotency_record(
        db=db,
        idempotency_key=idempotency_key,
        operation=operation,
        actor_user_id=actor_user_id,
    )
    if record is None:
        return None
    if record.request_hash != request_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key was already used with a different request payload",
        )
    return return_stored_response(record)
