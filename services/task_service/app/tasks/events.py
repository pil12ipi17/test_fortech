from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


EVENT_PRODUCER = "task-service"
EVENT_VERSION = 1


class TaskEventType(StrEnum):
    CREATED = "task.created"
    STATUS_CHANGED = "task.status_changed"
    DELETED = "task.deleted"


class EnrichmentEventType(StrEnum):
    TASK_ENRICHED = "task.enriched"


class NotificationEventType(StrEnum):
    SENT = "notification.sent"


EventType = TaskEventType | EnrichmentEventType | NotificationEventType | str


def _event_type_value(event_type: EventType) -> str:
    if isinstance(event_type, StrEnum):
        return event_type.value
    return str(event_type)


def build_event_envelope(
    *,
    event_type: EventType,
    payload: dict,
    correlation_id: str | None = None,
    producer: str = EVENT_PRODUCER,
    version: int = EVENT_VERSION,
) -> dict:
    return {
        "event_id": str(uuid4()),
        "event_type": _event_type_value(event_type),
        "version": version,
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": producer,
        "correlation_id": correlation_id or str(uuid4()),
        "payload": payload,
    }
