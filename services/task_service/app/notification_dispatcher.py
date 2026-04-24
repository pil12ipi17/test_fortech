from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import NotificationDelivery
from .notification_sender import EmailMessage, EmailSender, EmailSendResult, MockEmailSender
from .notification_store import (
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    mark_notification_delivery_sent,
    serialize_notification_context,
)
from .worker_store import record_worker_error

DISPATCHABLE_STATUSES = (DELIVERY_PENDING, DELIVERY_FAILED)


def _message_from_delivery(delivery: NotificationDelivery) -> EmailMessage:
    return EmailMessage(
        recipient=delivery.recipient_email,
        subject=delivery.subject,
        body=delivery.body,
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
            continue

        failed += 1
        record_worker_error(
            db=db,
            consumer_name="notification-cron",
            event_id=delivery.event_id,
            event_type=delivery.event_type,
            correlation_id=None,
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
