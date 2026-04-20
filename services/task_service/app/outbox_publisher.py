import json
import logging
import time

import pika

from .config import get_settings
from .db import SessionLocal, run_migrations
from .outbox import load_pending_outbox_events, mark_outbox_event_published, mark_outbox_event_retry
from .rabbitmq import build_rabbitmq_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("task-outbox-publisher")


def create_channel():
    settings = get_settings()
    rabbitmq = build_rabbitmq_config(settings)
    parameters = pika.URLParameters(rabbitmq.url)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.exchange_declare(
        exchange=rabbitmq.tasks_exchange,
        exchange_type=rabbitmq.tasks_exchange_type,
        durable=True,
    )
    return connection, channel, rabbitmq


def publish_pending_events_once() -> int:
    settings = get_settings()
    connection, channel, rabbitmq = create_channel()
    published = 0

    try:
        db = SessionLocal()
        try:
            events = load_pending_outbox_events(db=db, batch_size=settings.outbox_publish_batch_size)
            if not events:
                return 0

            for event in events:
                try:
                    channel.basic_publish(
                        exchange=rabbitmq.tasks_exchange,
                        routing_key=event.event_type,
                        body=event.payload_json.encode("utf-8"),
                        properties=pika.BasicProperties(
                            delivery_mode=2,
                            content_type="application/json",
                            message_id=event.event_id,
                            correlation_id=event.correlation_id,
                            type=event.event_type,
                            headers={
                                "event_id": event.event_id,
                                "event_type": event.event_type,
                                "event_version": event.version,
                                "producer": event.producer,
                            },
                        ),
                    )
                    mark_outbox_event_published(event=event)
                    published += 1
                    logger.info(
                        "Published event_id=%s event_type=%s aggregate_id=%s",
                        event.event_id,
                        event.event_type,
                        event.aggregate_id,
                    )
                except Exception as exc:
                    mark_outbox_event_retry(event=event, error=str(exc))
                    logger.exception(
                        "Failed to publish event_id=%s event_type=%s",
                        event.event_id,
                        event.event_type,
                    )
            db.commit()
            return published
        finally:
            db.close()
    finally:
        connection.close()


def run_forever() -> None:
    settings = get_settings()
    run_migrations()
    logger.info("Outbox publisher started")
    while True:
        published = publish_pending_events_once()
        if published == 0:
            time.sleep(settings.outbox_publish_poll_interval_seconds)


if __name__ == "__main__":
    run_forever()
