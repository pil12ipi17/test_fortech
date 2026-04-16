import json

from sqlalchemy.orm import Session

from .models import AuditLog


def serialize_details(details: dict | None) -> str:
    return json.dumps(details or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def add_audit_log(
    *,
    db: Session,
    actor_user_id: str | None,
    action: str,
    target_type: str,
    target_id: str | None,
    details: dict | None = None,
    result: str = "success",
) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            result=result,
            details_json=serialize_details(details),
        )
    )