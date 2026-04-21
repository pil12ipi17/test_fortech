import asyncio
import logging

import aio_pika

from .config import get_settings
from .db import SessionLocal
from .outbox import load_pending_outbox_events, mark_outbox_event_published, mark_outbox_event_retry
from .rabbitmq import build_rabbitmq_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("task-outbox-publisher")


async def create_exchange():
    settings = get_settings()
    rabbitmq = build_rabbitmq_config(settings)
    connection = await aio_pika.connect_robust(rabbitmq.url)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(
        rabbitmq.tasks_exchange,
        type=rabbitmq.tasks_exchange_type,
        durable=True,
    )
    return connection, channel, exchange


async def publish_pending_events_once() -> int:
    settings = get_settings()
    connection, channel, exchange = await create_exchange()
    published = 0

    try:
        db = SessionLocal()
        try:
            events = load_pending_outbox_events(db=db, batch_size=settings.outbox_publish_batch_size)
            if not events:
                return 0

            for event in events:
                try:
                    message = aio_pika.Message(
                        body=event.payload_json.encode("utf-8"),
                        content_type="application/json",
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        message_id=event.event_id,
                        correlation_id=event.correlation_id,
                        type=event.event_type,
                        headers={
                            "event_id": event.event_id,
                            "event_type": event.event_type,
                            "event_version": event.version,
                            "producer": event.producer,
                        },
                    )
                    await exchange.publish(message, routing_key=event.event_type)
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
        await channel.close()
        await connection.close()


async def run_forever() -> None:
    settings = get_settings()
    logger.info("Outbox publisher started")
    while True:
        published = await publish_pending_events_once()
        if published == 0:
            await asyncio.sleep(settings.outbox_publish_poll_interval_seconds)


if __name__ == "__main__":
    asyncio.run(run_forever())
