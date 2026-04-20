import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ProcessedEvent, WorkerEventLog


def serialize_worker_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def has_processed_event(*, db: Session, event_id: str, consumer_name: str) -> bool:
    return (
        db.scalar(
            select(ProcessedEvent.id).where(
                ProcessedEvent.event_id == event_id,
                ProcessedEvent.consumer_name == consumer_name,
            )
        )
        is not None
    )


def mark_event_processed(
    *,
    db: Session,
    event_id: str,
    consumer_name: str,
    event_type: str,
    correlation_id: str,
) -> None:
    db.add(
        ProcessedEvent(
            event_id=event_id,
            consumer_name=consumer_name,
            event_type=event_type,
            correlation_id=correlation_id,
        )
    )


def add_worker_event_log(
    *,
    db: Session,
    consumer_name: str,
    event_id: str,
    event_type: str,
    correlation_id: str,
    payload: dict,
    note: str,
) -> None:
    db.add(
        WorkerEventLog(
            consumer_name=consumer_name,
            event_id=event_id,
            event_type=event_type,
            correlation_id=correlation_id,
            payload_json=serialize_worker_payload(payload),
            note=note,
        )
    )
