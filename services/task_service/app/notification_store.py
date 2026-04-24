import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import NotificationDelivery
from .notification_sender import EmailMessage, EmailSendResult


DELIVERY_SUCCESS = "success"
DELIVERY_FAILED = "failed"


def build_mock_recipient_email(user_id: str | None) -> str:
    suffix = user_id or "unknown"
    return f"user-{suffix}@example.local"


def add_notification_delivery(
    *,
    db: Session,
    event_id: str,
    event_type: str,
    task_id: str | None,
    recipient_user_id: str | None,
    message: EmailMessage,
    result: EmailSendResult,
) -> None:
    db.add(
        NotificationDelivery(
            event_id=event_id,
            event_type=event_type,
            task_id=task_id,
            recipient_user_id=recipient_user_id,
            recipient_email=message.recipient,
            subject=message.subject,
            body=message.body,
            status=DELIVERY_SUCCESS if result.success else DELIVERY_FAILED,
            error_message=result.error_message,
            sent_at=datetime.now(timezone.utc),
        )
    )


def serialize_notification_context(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))