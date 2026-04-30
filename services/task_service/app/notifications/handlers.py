from sqlalchemy.orm import Session

from .sender import EmailMessage
from .store import add_notification_delivery, build_mock_recipient_email

SUPPORTED_EVENTS = {"task.enriched"}


def build_notification_email(envelope: dict) -> tuple[EmailMessage, str | None, str | None] | None:
    payload = envelope.get("payload") or {}
    event_type = envelope.get("event_type")
    task_id = payload.get("task_id")
    recipient_user_id = payload.get("assignee_id")
    recipient_email = build_mock_recipient_email(recipient_user_id)
    source_event_type = payload.get("source_event_type")
    metadata = payload.get("metadata") or {}

    if event_type != "task.enriched":
        return None

    if source_event_type == "task.created":
        title = payload.get("title") or task_id
        message = EmailMessage(
            recipient=recipient_email,
            subject=f"New task assigned: {title}",
            body=(
                f"A new task was created and assigned to you.\n"
                f"Task ID: {task_id}\n"
                f"Title: {title}\n"
                f"Priority: {payload.get('priority')}\n"
                f"Deadline bucket: {metadata.get('deadline_bucket')}\n"
            ),
        )
        return message, task_id, recipient_user_id

    if source_event_type == "task.status_changed":
        message = EmailMessage(
            recipient=recipient_email,
            subject=f"Task status changed: {task_id}",
            body=(
                f"Task status was changed.\n"
                f"Task ID: {task_id}\n"
                f"New status: {payload.get('status') or '-'}\n"
                f"Deadline bucket: {metadata.get('deadline_bucket')}\n"
            ),
        )
        return message, task_id, recipient_user_id

    return None


def handle_notification_event(db: Session, envelope: dict) -> str:
    event_type = str(envelope.get("event_type") or "")
    if event_type not in SUPPORTED_EVENTS:
        return f"Notification skipped for unsupported event_type={event_type}"

    built_email = build_notification_email(envelope)
    if built_email is None:
        return f"Notification skipped for event_type={event_type}"

    message, task_id, recipient_user_id = built_email
    add_notification_delivery(
        db=db,
        event_id=str(envelope["event_id"]),
        event_type=event_type,
        task_id=task_id,
        recipient_user_id=recipient_user_id,
        correlation_id=str(envelope.get("correlation_id") or ""),
        message=message,
    )
    return f"Notification queued for cron delivery to={message.recipient} event_type={event_type}"
