import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import ProcessedEvent, WorkerEventLog


def serialize_worker_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def claim_event_for_processing(
    *,
    db: Session,
    event_id: str,
    consumer_name: str,
    event_type: str,
    correlation_id: str,
) -> bool:
    values = {
        "id": str(uuid4()),
        "event_id": event_id,
        "consumer_name": consumer_name,
        "event_type": event_type,
        "correlation_id": correlation_id,
        "processed_at": datetime.now(timezone.utc),
    }
    dialect_name = db.bind.dialect.name if db.bind is not None else ""

    if dialect_name == "postgresql":
        statement = (
            postgresql_insert(ProcessedEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["event_id", "consumer_name"])
        )
        result = db.execute(statement)
        return result.rowcount == 1

    if dialect_name == "sqlite":
        statement = (
            sqlite_insert(ProcessedEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["event_id", "consumer_name"])
        )
        result = db.execute(statement)
        return result.rowcount == 1

    db.add(ProcessedEvent(**values))
    try:
        db.flush()
        return True
    except IntegrityError:
        db.rollback()
        return False


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
