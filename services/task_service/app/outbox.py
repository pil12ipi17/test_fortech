import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .events import EVENT_PRODUCER
from .models import OutboxEvent

OUTBOX_PENDING = "pending"
OUTBOX_PUBLISHED = "published"


def serialize_event_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def create_outbox_event(*, envelope: dict, aggregate_type: str, aggregate_id: str) -> OutboxEvent:
    return OutboxEvent(
        event_id=envelope["event_id"],
        event_type=envelope["event_type"],
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        producer=envelope.get("producer", EVENT_PRODUCER),
        version=int(envelope.get("version", 1)),
        correlation_id=str(envelope["correlation_id"]),
        payload_json=serialize_event_payload(envelope),
        status=OUTBOX_PENDING,
        attempts=0,
    )


def load_pending_outbox_events(*, db: Session, batch_size: int) -> list[OutboxEvent]:
    return list(
        db.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.status == OUTBOX_PENDING)
            .order_by(OutboxEvent.created_at.asc())
            .limit(batch_size)
        ).all()
    )


def mark_outbox_event_published(*, event: OutboxEvent) -> None:
    event.status = OUTBOX_PUBLISHED
    event.published_at = datetime.now(UTC)
    event.last_error = None


def mark_outbox_event_retry(*, event: OutboxEvent, error: str) -> None:
    event.attempts += 1
    event.last_error = error
