import json

from .events import EVENT_PRODUCER
from .models import OutboxEvent

OUTBOX_PENDING = "pending"


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
