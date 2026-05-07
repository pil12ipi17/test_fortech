import asyncio
import json
import logging
import time

import aio_pika
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import SpanKind, Status, StatusCode

from shared.metrics import OUTBOX_EVENTS_PUBLISHED_TOTAL, OUTBOX_PUBLISH_DURATION_SECONDS, start_metrics_http_server
from shared.tracing import CORRELATION_ID_HEADER, configure_tracing, get_current_trace_ids, get_tracer

from ..core.config import get_settings
from ..core.db import SessionLocal
from ..tasks.outbox import load_pending_outbox_events, mark_outbox_event_published, mark_outbox_event_retry
from .rabbitmq import build_rabbitmq_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("task-outbox-publisher")
SERVICE_NAME = "outbox-publisher"
tracer = get_tracer(SERVICE_NAME)


def _decode_envelope(payload_json: str) -> dict:
    return json.loads(payload_json)


def _build_publish_headers(*, event, envelope: dict) -> dict[str, object]:
    headers: dict[str, object] = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "event_version": event.version,
        "producer": event.producer,
        CORRELATION_ID_HEADER: event.correlation_id,
    }
    inject(headers)
    return headers


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


async def publish_pending_events_once(*, exchange: aio_pika.Exchange) -> int:
    settings = get_settings()
    rabbitmq = build_rabbitmq_config(settings)
    published = 0

    db = SessionLocal()
    try:
        events = load_pending_outbox_events(db=db, batch_size=settings.outbox_publish_batch_size)
        if not events:
            return 0

        for event in events:
            event_type = event.event_type
            start = time.perf_counter()
            result = "error"
            try:
                envelope = _decode_envelope(event.payload_json)
                parent_context = extract(envelope.get("trace_context") or {})
                with tracer.start_as_current_span("rabbitmq.publish", context=parent_context, kind=SpanKind.PRODUCER) as span:
                    span.set_attribute("messaging.system", "rabbitmq")
                    span.set_attribute("messaging.destination.name", rabbitmq.tasks_exchange)
                    span.set_attribute("messaging.operation", "publish")
                    span.set_attribute("event.type", event_type)
                    span.set_attribute("correlation_id", event.correlation_id)
                    headers = _build_publish_headers(event=event, envelope=envelope)
                    message = aio_pika.Message(
                        body=event.payload_json.encode("utf-8"),
                        content_type="application/json",
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        message_id=event.event_id,
                        correlation_id=event.correlation_id,
                        type=event_type,
                        headers=headers,
                    )
                    await exchange.publish(message, routing_key=event_type)
                    mark_outbox_event_published(event=event)
                    published += 1
                    result = "success"
                    trace_id, _ = get_current_trace_ids()
                    logger.info(
                        "published_event event_type=%s correlation_id=%s trace_id=%s",
                        event_type,
                        event.correlation_id,
                        trace_id,
                    )
            except Exception as exc:
                mark_outbox_event_retry(event=event, error=str(exc))
                OUTBOX_EVENTS_PUBLISHED_TOTAL.labels(service=SERVICE_NAME, event_type=event_type, result="error").inc()
                OUTBOX_PUBLISH_DURATION_SECONDS.labels(
                    service=SERVICE_NAME,
                    event_type=event_type,
                    result="error",
                ).observe(time.perf_counter() - start)
                logger.exception(
                    "failed_to_publish_event event_type=%s correlation_id=%s",
                    event_type,
                    event.correlation_id,
                )
                continue

            OUTBOX_EVENTS_PUBLISHED_TOTAL.labels(service=SERVICE_NAME, event_type=event_type, result=result).inc()
            OUTBOX_PUBLISH_DURATION_SECONDS.labels(
                service=SERVICE_NAME,
                event_type=event_type,
                result=result,
            ).observe(time.perf_counter() - start)
        db.commit()
        return published
    finally:
        db.close()


async def run_forever() -> None:
    settings = get_settings()
    configure_tracing(service_name=SERVICE_NAME, otlp_endpoint=settings.otel_exporter_otlp_endpoint)
    start_metrics_http_server(settings.metrics_port)
    connection, channel, exchange = await create_exchange()
    logger.info("Outbox publisher started")
    try:
        while True:
            try:
                published = await publish_pending_events_once(exchange=exchange)
            except Exception:
                logger.exception("Outbox publisher iteration failed; retrying after poll interval")
                published = 0
            if published == 0:
                await asyncio.sleep(settings.outbox_publish_poll_interval_seconds)
    finally:
        await channel.close()
        await connection.close()


if __name__ == "__main__":
    asyncio.run(run_forever())