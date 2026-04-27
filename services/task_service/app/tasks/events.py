from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


EVENT_PRODUCER = "task-service"
EVENT_VERSION = 1


class TaskEventType(StrEnum):
    CREATED = "task.created"
    STATUS_CHANGED = "task.status_changed"
    DELETED = "task.deleted"


def build_event_envelope(
    *,
    event_type: TaskEventType,
    payload: dict,
    correlation_id: str | None = None,
) -> dict:
    return {
        "event_id": str(uuid4()),
        "event_type": event_type.value,
        "version": EVENT_VERSION,
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": EVENT_PRODUCER,
        "correlation_id": correlation_id or str(uuid4()),
        "payload": payload,
    }
