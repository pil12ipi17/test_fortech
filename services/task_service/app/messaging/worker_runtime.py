import asyncio
import json
import logging
import time
from collections.abc import Callable

import aio_pika
from aio_pika.abc import HeadersType
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import SpanKind, Status, StatusCode
from sqlalchemy.orm import Session

from shared.metrics import (
    EVENT_PIPELINE_DLQ_MESSAGES,
    WORKER_ACTIVE_HANDLERS,
    WORKER_ERRORS_TOTAL,
    WORKER_EVENT_PROCESSING_DURATION_SECONDS,
    WORKER_EVENTS_TOTAL,
    start_metrics_http_server,
)
from shared.tracing import CORRELATION_ID_HEADER, configure_tracing, get_current_trace_ids, get_tracer

from ..core.config import get_settings
from ..core.db import SessionLocal
from .rabbitmq import build_rabbitmq_config
from .worker_store import add_worker_event_log, claim_event_for_processing, record_worker_error

logger = logging.getLogger("task-event-worker")

DEFAULT_BINDING_KEYS = ("task.*",)
RETRY_HEADER = "x-retry-count"
EventHandler = Callable[[Session, dict], str]


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


def _retry_delay_seconds(*, retry_count: int) -> float:
    settings = get_settings()
    delay = settings.worker_retry_backoff_base_seconds * (2**retry_count)
    return min(delay, settings.worker_retry_backoff_max_seconds)


def _save_worker_error(
    *,
    consumer_name: str,
    event_id: str | None,
    event_type: str | None,
    correlation_id: str | None,
    payload: dict,
    error: Exception,
    retry_count: int,
) -> None:
    db = SessionLocal()
    try:
        record_worker_error(
            db=db,
            consumer_name=consumer_name,
            event_id=event_id,
            event_type=event_type,
            correlation_id=correlation_id,
            payload=payload,
            error=error,
            retry_count=retry_count,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to persist worker error consumer=%s event_id=%s", consumer_name, event_id)
    finally:
        db.close()


async def _create_queue(*, queue_name: str, binding_keys: tuple[str, ...]):
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
    for binding_key in binding_keys:
        await queue.bind(exchange, routing_key=binding_key)
    return connection, channel, exchange, queue


async def _republish_for_retry(
    *,
    exchange: aio_pika.Exchange,
    message: aio_pika.IncomingMessage,
    retry_count: int,
) -> None:
    headers = _message_headers(message)
    headers[RETRY_HEADER] = retry_count + 1
    inject(headers)
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
    event_handler: EventHandler,
    binding_keys: tuple[str, ...] = DEFAULT_BINDING_KEYS,
) -> None:
    settings = get_settings()
    configure_tracing(service_name=consumer_name, otlp_endpoint=settings.otel_exporter_otlp_endpoint)
    tracer = get_tracer(consumer_name)
    start_metrics_http_server(settings.metrics_port)
    connection, channel, exchange, queue = await _create_queue(queue_name=queue_name, binding_keys=binding_keys)
    logger.info("Worker started consumer=%s queue=%s binding_keys=%s", consumer_name, queue_name, binding_keys)

    try:
        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                envelope: dict = {}
                event_id = str(message.message_id or "")
                event_type = str(message.type or "unknown")
                correlation_id = str(message.correlation_id or event_id)
                result = "error"
                start = time.perf_counter()
                WORKER_ACTIVE_HANDLERS.labels(consumer=consumer_name).inc()
                parent_context = extract(_message_headers(message))
                try:
                    with tracer.start_as_current_span("rabbitmq.consume", context=parent_context, kind=SpanKind.CONSUMER) as span:
                        span.set_attribute("messaging.system", "rabbitmq")
                        span.set_attribute("messaging.operation", "process")
                        span.set_attribute("messaging.destination.name", queue_name)
                        span.set_attribute("consumer.name", consumer_name)
                        envelope = _decode_event(message)
                        event_id = str(envelope.get("event_id") or event_id)
                        event_type = str(envelope.get("event_type") or event_type)
                        correlation_id = str(envelope.get("correlation_id") or correlation_id or event_id)
                        span.set_attribute("event.type", event_type)
                        span.set_attribute("correlation_id", correlation_id)
                        if not event_id or not event_type:
                            raise ValueError("Incoming task event is missing event_id or event_type")

                        db = SessionLocal()
                        try:
                            claimed = claim_event_for_processing(
                                db=db,
                                event_id=event_id,
                                consumer_name=consumer_name,
                                event_type=event_type,
                                correlation_id=correlation_id,
                            )
                            if not claimed:
                                result = "duplicate"
                                WORKER_EVENTS_TOTAL.labels(
                                    consumer=consumer_name,
                                    event_type=event_type,
                                    result=result,
                                ).inc()
                                logger.info(
                                    "skipped_duplicate_event event_type=%s consumer=%s correlation_id=%s",
                                    event_type,
                                    consumer_name,
                                    correlation_id,
                                )
                                await message.ack()
                                continue

                            with tracer.start_as_current_span("worker.handler") as handler_span:
                                handler_span.set_attribute("consumer.name", consumer_name)
                                handler_span.set_attribute("event.type", event_type)
                                note = event_handler(db, envelope)
                            add_worker_event_log(
                                db=db,
                                consumer_name=consumer_name,
                                event_id=event_id,
                                event_type=event_type,
                                correlation_id=correlation_id,
                                payload=envelope,
                                note=note,
                            )
                            db.commit()
                            result = "processed"
                            WORKER_EVENTS_TOTAL.labels(
                                consumer=consumer_name,
                                event_type=event_type,
                                result=result,
                            ).inc()
                            trace_id, _ = get_current_trace_ids()
                            logger.info(
                                "processed_event event_type=%s consumer=%s correlation_id=%s trace_id=%s",
                                event_type,
                                consumer_name,
                                correlation_id,
                                trace_id,
                            )
                            await message.ack()
                        except Exception:
                            db.rollback()
                            logger.exception(
                                "failed_to_process_event event_type=%s consumer=%s correlation_id=%s",
                                event_type,
                                consumer_name,
                                correlation_id,
                            )
                            raise
                        finally:
                            db.close()
                except Exception as exc:
                    WORKER_ERRORS_TOTAL.labels(consumer=consumer_name, event_type=event_type).inc()
                    retry_count = _retry_count(message)
                    _save_worker_error(
                        consumer_name=consumer_name,
                        event_id=event_id,
                        event_type=event_type,
                        correlation_id=correlation_id,
                        payload=envelope,
                        error=exc,
                        retry_count=retry_count,
                    )
                    if retry_count < settings.worker_max_retry_attempts:
                        retry_delay_seconds = _retry_delay_seconds(retry_count=retry_count)
                        await asyncio.sleep(retry_delay_seconds)
                        with tracer.start_as_current_span("rabbitmq.retry_publish", kind=SpanKind.PRODUCER) as retry_span:
                            retry_span.set_attribute("event.type", event_type)
                            retry_span.set_attribute("consumer.name", consumer_name)
                            await _republish_for_retry(
                                exchange=exchange,
                                message=message,
                                retry_count=retry_count,
                            )
                        result = "retry"
                        WORKER_EVENTS_TOTAL.labels(consumer=consumer_name, event_type=event_type, result=result).inc()
                        await message.ack()
                        logger.warning(
                            "republished_event_for_retry consumer=%s event_type=%s retry=%s max_retry=%s delay_seconds=%s correlation_id=%s",
                            consumer_name,
                            event_type,
                            retry_count + 1,
                            settings.worker_max_retry_attempts,
                            retry_delay_seconds,
                            correlation_id,
                        )
                    else:
                        result = "dlq"
                        WORKER_EVENTS_TOTAL.labels(consumer=consumer_name, event_type=event_type, result=result).inc()
                        EVENT_PIPELINE_DLQ_MESSAGES.labels(consumer=consumer_name, event_type=event_type).inc()
                        await message.nack(requeue=False)
                        logger.error(
                            "moved_event_to_dlq consumer=%s event_type=%s retries=%s max_retry=%s correlation_id=%s",
                            consumer_name,
                            event_type,
                            retry_count,
                            settings.worker_max_retry_attempts,
                            correlation_id,
                        )
                finally:
                    WORKER_ACTIVE_HANDLERS.labels(consumer=consumer_name).dec()
                    WORKER_EVENT_PROCESSING_DURATION_SECONDS.labels(
                        consumer=consumer_name,
                        event_type=event_type,
                        result=result,
                    ).observe(time.perf_counter() - start)
    finally:
        await channel.close()
        await connection.close()