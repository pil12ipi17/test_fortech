import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..tasks.events import EnrichmentEventType, build_event_envelope
from ..tasks.models import TaskEnrichment
from ..tasks.outbox import create_outbox_event

ENRICHMENT_PRODUCER = "enrichment-service"
SUPPORTED_EVENTS = {"task.created", "task.status_changed"}


def _parse_deadline(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _deadline_bucket(deadline: str | None) -> str:
    parsed = _parse_deadline(deadline)
    if parsed is None:
        return "no_deadline"
    now = datetime.now(UTC)
    seconds_left = (parsed - now).total_seconds()
    if seconds_left < 0:
        return "overdue"
    if seconds_left <= 24 * 60 * 60:
        return "due_soon"
    return "later"


def _build_metadata(payload: dict) -> dict:
    priority = str(payload.get("priority") or "unknown")
    return {
        "deadline_bucket": _deadline_bucket(payload.get("deadline")),
        "is_high_priority": priority == "high",
        "has_assignee": bool(payload.get("assignee_id")),
    }


def _find_existing_enrichment(db: Session, source_event_id: str) -> TaskEnrichment | None:
    return db.query(TaskEnrichment).filter(TaskEnrichment.source_event_id == source_event_id).one_or_none()


def handle_enrichment_event(db: Session, envelope: dict) -> str:
    event_type = str(envelope.get("event_type") or "")
    if event_type not in SUPPORTED_EVENTS:
        return f"Enrichment skipped for unsupported event_type={event_type}"

    payload = envelope.get("payload") or {}
    task_id = str(payload.get("task_id") or "")
    if not task_id:
        raise ValueError("Task enrichment event is missing payload.task_id")

    source_event_id = str(envelope["event_id"])
    existing = _find_existing_enrichment(db, source_event_id)
    if existing is not None:
        return f"Enrichment already exists for source_event_id={source_event_id}"

    metadata = _build_metadata(payload)
    enriched_payload = {
        "task_id": task_id,
        "source_event_id": source_event_id,
        "source_event_type": event_type,
        "title": payload.get("title"),
        "assignee_id": payload.get("assignee_id"),
        "team_id": payload.get("team_id"),
        "priority": payload.get("priority"),
        "status": payload.get("status") or payload.get("to_status"),
        "metadata": metadata,
    }
    enrichment = TaskEnrichment(
        source_event_id=source_event_id,
        source_event_type=event_type,
        task_id=task_id,
        correlation_id=str(envelope["correlation_id"]),
        metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
    )
    enriched_envelope = build_event_envelope(
        event_type=EnrichmentEventType.TASK_ENRICHED,
        producer=ENRICHMENT_PRODUCER,
        correlation_id=str(envelope["correlation_id"]),
        payload=enriched_payload,
    )
    db.add(enrichment)
    db.add(create_outbox_event(envelope=enriched_envelope, aggregate_type="task", aggregate_id=task_id))
    return f"Enriched task_id={task_id} from event_type={event_type}"