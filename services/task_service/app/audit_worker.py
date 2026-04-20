import asyncio
import logging

from .worker_runtime import run_task_event_consumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

QUEUE_NAME = "audit.task-events"
CONSUMER_NAME = "audit-worker"


def build_audit_note(envelope: dict) -> str:
    payload = envelope.get("payload") or {}
    event_type = envelope.get("event_type")
    task_id = payload.get("task_id")
    return f"Archived task event task_id={task_id} event_type={event_type}"


if __name__ == "__main__":
    asyncio.run(
        run_task_event_consumer(
            consumer_name=CONSUMER_NAME,
            queue_name=QUEUE_NAME,
            note_builder=build_audit_note,
        )
    )
