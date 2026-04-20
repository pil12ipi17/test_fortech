import asyncio
import logging

from .worker_runtime import run_task_event_consumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

QUEUE_NAME = "notifications.task-events"
CONSUMER_NAME = "notification-worker"


def build_notification_note(envelope: dict) -> str:
    payload = envelope.get("payload") or {}
    event_type = envelope.get("event_type")
    if event_type == "task.created":
        return f"Notification prepared for assignee={payload.get('assignee_id')} about new task={payload.get('task_id')}"
    if event_type == "task.status_changed":
        return f"Notification prepared for task={payload.get('task_id')} status {payload.get('from_status')} -> {payload.get('to_status')}"
    if event_type == "task.deleted":
        return f"Notification prepared for deleted task={payload.get('task_id')}"
    return f"Notification worker processed event_type={event_type}"


if __name__ == "__main__":
    asyncio.run(
        run_task_event_consumer(
            consumer_name=CONSUMER_NAME,
            queue_name=QUEUE_NAME,
            note_builder=build_notification_note,
        )
    )
