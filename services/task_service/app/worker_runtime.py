import json
import logging
from collections.abc import Callable

import aio_pika
from aio_pika.abc import HeadersType

from .db import SessionLocal
from .rabbitmq import build_rabbitmq_config
from .config import get_settings
from .worker_store import add_worker_event_log, has_processed_event, mark_event_processed

logger = logging.getLogger("task-event-worker")

TASK_EVENTS_BINDING_KEY = "task.*"
RETRY_HEADER = "x-retry-count"


def _decode_event(message: aio_pika.IncomingMessage) -> dict:
    return json.loads(message.body.decode("utf-8"))


def _message_headers(message: aio_pika.IncomingMessage) -> HeadersType:
    return dict(message.headers or {})


def _retry_count(message: aio_pika.IncomingMessage) -> int:
    value = _message_headers(message).get(RETRY_HEADER, 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
    dead_letter_exchange = await channel.declare_exchange(
        rabbitmq.tasks_dead_letter_exchange,
        type=rabbitmq.tasks_exchange_type,
        durable=True,
    )
    dlq_name = f"{queue_name}.dlq"
    dlq = await channel.declare_queue(dlq_name, durable=True)
    await dlq.bind(dead_letter_exchange, routing_key=dlq_name)
    queue = await channel.declare_queue(
        queue_name,
        durable=True,
        arguments={
            "x-dead-letter-exchange": rabbitmq.tasks_dead_letter_exchange,
            "x-dead-letter-routing-key": dlq_name,
        },
    )
    await queue.bind(exchange, routing_key=TASK_EVENTS_BINDING_KEY)
    return connection, channel, exchange, queue


async def _republish_for_retry(
    *,
    exchange: aio_pika.Exchange,
    message: aio_pika.IncomingMessage,
    retry_count: int,
) -> None:
    headers = _message_headers(message)
    headers[RETRY_HEADER] = retry_count + 1
    retry_message = aio_pika.Message(
        body=message.body,
        content_type=message.content_type,
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        message_id=message.message_id,
        correlation_id=message.correlation_id,
        type=message.type,
        headers=headers,
    )
    await exchange.publish(retry_message, routing_key=message.routing_key or str(message.type or "task.unknown"))


async def run_task_event_consumer(
    *,
    consumer_name: str,
    queue_name: str,
    note_builder: Callable[[dict], str],
) -> None:
    settings = get_settings()
    connection, channel, exchange, queue = await _create_queue(queue_name=queue_name)
    logger.info("Worker started consumer=%s queue=%s", consumer_name, queue_name)

    try:
        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                try:
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
                            await message.ack()
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
                        await message.ack()
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
                except Exception:
                    retry_count = _retry_count(message)
                    if retry_count < settings.worker_max_retry_attempts:
                        await _republish_for_retry(
                            exchange=exchange,
                            message=message,
                            retry_count=retry_count,
                        )
                        await message.ack()
                        logger.warning(
                            "Republished event for retry consumer=%s retry=%s max_retry=%s",
                            consumer_name,
                            retry_count + 1,
                            settings.worker_max_retry_attempts,
                        )
                    else:
                        await message.nack(requeue=False)
                        logger.error(
                            "Moved event to DLQ consumer=%s retries=%s max_retry=%s",
                            consumer_name,
                            retry_count,
                            settings.worker_max_retry_attempts,
                        )
    finally:
        await channel.close()
        await connection.close()
