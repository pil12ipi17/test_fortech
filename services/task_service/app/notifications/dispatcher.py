from sqlalchemy import select
from sqlalchemy.orm import Session

from ..messaging.worker_store import record_worker_error
from ..tasks.events import NotificationEventType, build_event_envelope
from ..tasks.models import NotificationDelivery
from ..tasks.outbox import create_outbox_event
from .sender import EmailMessage, EmailSender, EmailSendResult, MockEmailSender
from .store import (
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    mark_notification_delivery_sent,
    mark_notification_sent_event_published,
    serialize_notification_context,
)

DISPATCHABLE_STATUSES = (DELIVERY_PENDING, DELIVERY_FAILED)
NOTIFICATION_PRODUCER = "notification-service"


def _message_from_delivery(delivery: NotificationDelivery) -> EmailMessage:
    return EmailMessage(
        recipient=delivery.recipient_email,
        subject=delivery.subject,
        body=delivery.body,
    )


def _build_sent_event(delivery: NotificationDelivery) -> dict:
    return build_event_envelope(
        event_type=NotificationEventType.SENT,
        producer=NOTIFICATION_PRODUCER,
        correlation_id=delivery.correlation_id,
        payload={
            "delivery_id": delivery.id,
            "task_id": delivery.task_id,
            "recipient_user_id": delivery.recipient_user_id,
            "recipient_email": delivery.recipient_email,
            "source_event_id": delivery.event_id,
            "source_event_type": delivery.event_type,
        },
    )


def dispatch_pending_notifications(
    *,
    db: Session,
    sender: EmailSender | None = None,
    limit: int = 50,
) -> dict[str, int]:
    sender = sender or MockEmailSender()
    statement = (
        select(NotificationDelivery)
        .where(NotificationDelivery.status.in_(DISPATCHABLE_STATUSES))
        .order_by(NotificationDelivery.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    deliveries = list(db.scalars(statement))
    sent = 0
    failed = 0

    for delivery in deliveries:
        message = _message_from_delivery(delivery)
        try:
            result = sender.send(message)
        except Exception as exc:  # pragma: no cover - defensive path for real sender adapters.
            result = EmailSendResult(success=False, error_message=str(exc))

        mark_notification_delivery_sent(delivery, result)
        if result.success:
            sent += 1
            if delivery.sent_event_published_at is None:
                sent_envelope = _build_sent_event(delivery)
                db.add(
                    create_outbox_event(
                        envelope=sent_envelope,
                        aggregate_type="notification",
                        aggregate_id=delivery.id,
                    )
                )
                mark_notification_sent_event_published(delivery)
            continue

        failed += 1
        record_worker_error(
            db=db,
            consumer_name="notification-cron",
            event_id=delivery.event_id,
            event_type=delivery.event_type,
            correlation_id=delivery.correlation_id,
            error_type="NotificationSendFailed",
            error_message=result.error_message or "Notification sender returned failure",
            retry_count=0,
            payload={
                "delivery_id": delivery.id,
                "recipient_email": delivery.recipient_email,
                "subject": delivery.subject,
                "context": serialize_notification_context({"task_id": delivery.task_id}),
            },
        )

    return {"selected": len(deliveries), "sent": sent, "failed": failed}
