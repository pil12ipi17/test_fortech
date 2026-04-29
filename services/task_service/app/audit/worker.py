import asyncio
import logging

from sqlalchemy.orm import Session

from .report import generate_audit_report
from ..core.config import get_settings
from ..messaging.worker_runtime import run_task_event_consumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

QUEUE_NAME = "audit.task-events"
CONSUMER_NAME = "audit-worker"
BINDING_KEYS = ("task.*",)


def handle_audit_event(db: Session, envelope: dict) -> str:
    payload = envelope.get("payload") or {}
    event_type = envelope.get("event_type")
    task_id = payload.get("task_id")
    settings = get_settings()
    output = generate_audit_report(
        db=db,
        output_path=settings.audit_report_path,
        auth_database_url=settings.auth_database_url,
    )
    return f"Archived task event task_id={task_id} event_type={event_type}; report={output}"


if __name__ == "__main__":
    asyncio.run(
        run_task_event_consumer(
            consumer_name=CONSUMER_NAME,
            queue_name=QUEUE_NAME,
            event_handler=handle_audit_event,
            binding_keys=BINDING_KEYS,
        )
    )
