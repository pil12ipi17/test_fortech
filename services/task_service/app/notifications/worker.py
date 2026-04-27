import asyncio
import logging

from .handlers import handle_notification_event
from ..messaging.worker_runtime import run_task_event_consumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

QUEUE_NAME = "notifications.task-events"
CONSUMER_NAME = "notification-worker"


if __name__ == "__main__":
    asyncio.run(
        run_task_event_consumer(
            consumer_name=CONSUMER_NAME,
            queue_name=QUEUE_NAME,
            event_handler=handle_notification_event,
        )
    )
