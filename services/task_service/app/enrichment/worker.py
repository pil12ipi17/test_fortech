import asyncio
import logging

from .handlers import handle_enrichment_event
from ..messaging.worker_runtime import run_task_event_consumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

QUEUE_NAME = "enrichment.task-events"
CONSUMER_NAME = "enrichment-service"
BINDING_KEYS = ("task.created", "task.status_changed")


if __name__ == "__main__":
    asyncio.run(
        run_task_event_consumer(
            consumer_name=CONSUMER_NAME,
            queue_name=QUEUE_NAME,
            event_handler=handle_enrichment_event,
            binding_keys=BINDING_KEYS,
        )
    )