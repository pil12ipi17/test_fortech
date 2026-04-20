import json
import logging
from collections.abc import Callable

import aio_pika

from .db import SessionLocal, run_migrations
from .rabbitmq import build_rabbitmq_config
from .config import get_settings
from .worker_store import add_worker_event_log, has_processed_event, mark_event_processed

logger = logging.getLogger("task-event-worker")

TASK_EVENTS_BINDING_KEY = "task.*"


def _decode_event(message: aio_pika.IncomingMessage) -> dict:
    return json.loads(message.body.decode("utf-8"))


async def _create_queue(*, queue_name: str):
    settings = get_settings()
    rabbitmq = build_rabbitmq_config(settings)
    connection = await aio_pika.connect_robust(rabbitmq.url)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)
    exchange = await channel.declare_exchange(
        rabbitmq.tasks_exchange,
        type=rabbitmq.tasks_exchange_type,
        durable=True,
    )
    queue = await channel.declare_queue(queue_name, durable=True)
    await queue.bind(exchange, routing_key=TASK_EVENTS_BINDING_KEY)
    return connection, channel, queue


async def run_task_event_consumer(
    *,
    consumer_name: str,
    queue_name: str,
    note_builder: Callable[[dict], str],
) -> None:
    run_migrations()
    connection, channel, queue = await _create_queue(queue_name=queue_name)
    logger.info("Worker started consumer=%s queue=%s", consumer_name, queue_name)

    try:
        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process(requeue=True):
                    envelope = _decode_event(message)
                    event_id = str(envelope.get("event_id") or message.message_id or "")
                    event_type = str(envelope.get("event_type") or message.type or "")
                    correlation_id = str(envelope.get("correlation_id") or message.correlation_id or event_id)
                    if not event_id or not event_type:
                        raise ValueError("Incoming task event is missing event_id or event_type")

                    db = SessionLocal()
                    try:
                        if has_processed_event(db=db, event_id=event_id, consumer_name=consumer_name):
                            logger.info(
                                "Skipping duplicate event_id=%s consumer=%s",
                                event_id,
                                consumer_name,
                            )
                            continue

                        note = note_builder(envelope)
                        add_worker_event_log(
                            db=db,
                            consumer_name=consumer_name,
                            event_id=event_id,
                            event_type=event_type,
                            correlation_id=correlation_id,
                            payload=envelope,
                            note=note,
                        )
                        mark_event_processed(
                            db=db,
                            event_id=event_id,
                            consumer_name=consumer_name,
                            event_type=event_type,
                            correlation_id=correlation_id,
                        )
                        db.commit()
                        logger.info(
                            "Processed event_id=%s event_type=%s consumer=%s",
                            event_id,
                            event_type,
                            consumer_name,
                        )
                    except Exception:
                        db.rollback()
                        logger.exception(
                            "Failed to process event_id=%s consumer=%s",
                            event_id,
                            consumer_name,
                        )
                        raise
                    finally:
                        db.close()
    finally:
        await channel.close()
        await connection.close()
