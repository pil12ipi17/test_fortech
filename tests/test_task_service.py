import os
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ["TASK_DATABASE_URL"] = f"sqlite+pysqlite:///{Path.cwd() / 'test_task_service.db'}"
os.environ["TASK_JWT_SECRET"] = "test-secret"

from services.task_service.app.core.db import SessionLocal, run_migrations  # noqa: E402
from services.task_service.app.main import app  # noqa: E402
from services.task_service.app.tasks.models import (  # noqa: E402
    AuditLog,
    IdempotencyKey,
    NotificationDelivery,
    OutboxEvent,
    ProcessedEvent,
    Task,
    TaskEnrichment,
    TaskStatusHistory,
    WorkerError,
    WorkerEventLog,
)
from services.task_service.app.audit.report import generate_audit_report  # noqa: E402
from services.task_service.app.tasks.events import (  # noqa: E402
    EnrichmentEventType,
    NotificationEventType,
    TaskEventType,
    build_event_envelope,
)
from services.task_service.app.notifications.dispatcher import dispatch_pending_notifications  # noqa: E402
from services.task_service.app.enrichment.handlers import handle_enrichment_event  # noqa: E402
from services.task_service.app.notifications.handlers import handle_notification_event  # noqa: E402
from services.task_service.app.tasks.outbox import create_outbox_event  # noqa: E402
from services.task_service.app.messaging.worker_store import claim_event_for_processing, record_worker_error  # noqa: E402
from services.task_service.app.core.config import Settings, get_settings  # noqa: E402
from services.task_service.app.api import routers as task_routers  # noqa: E402
from tests.fake_redis import FakeRedis  # noqa: E402


@pytest.fixture(autouse=True)
def clear_task_tables():
    run_migrations()
    db = SessionLocal()
    try:
        db.query(AuditLog).delete()
        db.query(NotificationDelivery).delete()
        db.query(TaskEnrichment).delete()
        db.query(WorkerError).delete()
        db.query(WorkerEventLog).delete()
        db.query(ProcessedEvent).delete()
        db.query(OutboxEvent).delete()
        db.query(IdempotencyKey).delete()
        db.query(TaskStatusHistory).delete()
        db.query(Task).delete()
        db.commit()
    finally:
        db.close()


def make_token(
    user_id: str,
    email: str,
    *,
    roles: list[str] | None = None,
    team_ids: list[str] | None = None,
) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "iss": "auth-service",
        "type": "access",
        "roles": roles or [],
        "team_ids": team_ids or [],
    }
    return jwt.encode(payload, "test-secret", algorithm="HS256")



def test_stage7_event_contract_envelopes_keep_trace_metadata():
    enriched = build_event_envelope(
        event_type=EnrichmentEventType.TASK_ENRICHED,
        producer="enrichment-service",
        correlation_id="correlation-stage-7",
        payload={"task_id": "task-stage-7", "metadata": {"deadline_bucket": "due_soon"}},
    )
    sent = build_event_envelope(
        event_type=NotificationEventType.SENT,
        producer="notification-worker",
        correlation_id=enriched["correlation_id"],
        payload={"task_id": "task-stage-7", "recipient_user_id": "user-stage-7"},
    )

    assert enriched["event_type"] == "task.enriched"
    assert enriched["version"] == 1
    assert enriched["producer"] == "enrichment-service"
    assert enriched["correlation_id"] == "correlation-stage-7"
    assert sent["event_type"] == "notification.sent"
    assert sent["version"] == 1
    assert sent["producer"] == "notification-worker"
    assert sent["correlation_id"] == enriched["correlation_id"]

def test_enrichment_worker_creates_enrichment_and_outbox_event():
    envelope = build_event_envelope(
        event_type=TaskEventType.CREATED,
        correlation_id="correlation-enrichment-1",
        payload={
            "task_id": "task-enrichment-1",
            "title": "Enrich me",
            "assignee_id": "user-enrichment-1",
            "team_id": "team-enrichment-1",
            "priority": "high",
            "deadline": "2026-05-01T10:00:00Z",
        },
    )
    db = SessionLocal()
    try:
        note = handle_enrichment_event(db, envelope)
        db.commit()

        enrichment = db.query(TaskEnrichment).one()
        outbox_event = db.query(OutboxEvent).filter(OutboxEvent.event_type == "task.enriched").one()
        assert enrichment.source_event_id == envelope["event_id"]
        assert enrichment.source_event_type == "task.created"
        assert enrichment.task_id == "task-enrichment-1"
        assert enrichment.correlation_id == envelope["correlation_id"]
        assert '"is_high_priority": true' in enrichment.metadata_json
        assert outbox_event.producer == "enrichment-service"
        assert outbox_event.correlation_id == envelope["correlation_id"]
        assert '"source_event_type":"task.created"' in outbox_event.payload_json
        assert "Enriched task_id=task-enrichment-1" in note
    finally:
        db.close()


def test_worker_event_claim_is_idempotent_per_consumer():
    db = SessionLocal()
    try:
        first_claim = claim_event_for_processing(
            db=db,
            event_id="event-1",
            consumer_name="notification-worker",
            event_type="task.created",
            correlation_id="correlation-1",
        )
        second_claim = claim_event_for_processing(
            db=db,
            event_id="event-1",
            consumer_name="notification-worker",
            event_type="task.created",
            correlation_id="correlation-1",
        )
        other_consumer_claim = claim_event_for_processing(
            db=db,
            event_id="event-1",
            consumer_name="audit-worker",
            event_type="task.created",
            correlation_id="correlation-1",
        )
        db.commit()

        assert first_claim is True
        assert second_claim is False
        assert other_consumer_claim is True
        assert db.query(ProcessedEvent).count() == 2
    finally:
        db.close()


def test_notification_worker_queues_delivery_for_cron():
    envelope = build_event_envelope(
        event_type=EnrichmentEventType.TASK_ENRICHED,
        correlation_id="correlation-notification-1",
        producer="enrichment-service",
        payload={
            "task_id": "task-notification-1",
            "source_event_id": "source-task-event-1",
            "source_event_type": "task.created",
            "assignee_id": "user-notification-1",
            "title": "Notify assignee",
            "priority": "high",
            "metadata": {"deadline_bucket": "later"},
        },
    )
    db = SessionLocal()
    try:
        note = handle_notification_event(db, envelope)
        db.commit()

        delivery = db.query(NotificationDelivery).one()
        assert delivery.event_id == envelope["event_id"]
        assert delivery.event_type == "task.enriched"
        assert delivery.task_id == "task-notification-1"
        assert delivery.recipient_user_id == "user-notification-1"
        assert delivery.correlation_id == envelope["correlation_id"]
        assert delivery.recipient_email == "user-user-notification-1@example.local"
        assert delivery.status == "pending"
        assert delivery.sent_at is None
        assert "Notification queued" in note
    finally:
        db.close()


def test_notification_cron_dispatches_pending_delivery_and_publishes_sent_event():
    envelope = build_event_envelope(
        event_type=EnrichmentEventType.TASK_ENRICHED,
        correlation_id="correlation-notification-cron-1",
        producer="enrichment-service",
        payload={
            "task_id": "task-notification-cron-1",
            "source_event_id": "source-task-event-cron-1",
            "source_event_type": "task.created",
            "assignee_id": "user-notification-cron-1",
            "title": "Cron delivery",
            "priority": "medium",
            "metadata": {"deadline_bucket": "no_deadline"},
        },
    )
    db = SessionLocal()
    try:
        handle_notification_event(db, envelope)
        db.commit()

        result = dispatch_pending_notifications(db=db)
        db.commit()

        delivery = db.query(NotificationDelivery).one()
        sent_event = db.query(OutboxEvent).filter(OutboxEvent.event_type == "notification.sent").one()
        assert result == {"selected": 1, "sent": 1, "failed": 0}
        assert delivery.status == "success"
        assert delivery.sent_at is not None
        assert sent_event.producer == "notification-service"
        assert sent_event.aggregate_type == "notification"
        assert sent_event.aggregate_id == delivery.id
        assert sent_event.correlation_id == envelope["correlation_id"]
        assert '"source_event_type":"task.enriched"' in sent_event.payload_json
    finally:
        db.close()

def test_audit_report_csv_contains_task_metrics_and_errors():
    envelope = build_event_envelope(
        event_type=TaskEventType.CREATED,
        payload={"task_id": "task-report-1"},
    )
    db = SessionLocal()
    try:
        db.add(create_outbox_event(envelope=envelope, aggregate_type="task", aggregate_id="task-report-1"))
        record_worker_error(
            db=db,
            consumer_name="audit-worker",
            event_id=envelope["event_id"],
            event_type=envelope["event_type"],
            correlation_id=envelope["correlation_id"],
            payload=envelope,
            error=RuntimeError("report test error"),
            retry_count=1,
        )
        db.commit()

        output = Path.cwd() / "test_audit_report.csv"
        generated = generate_audit_report(db=db, output_path=str(output))
        content = generated.read_text(encoding="utf-8")
        assert "date,metric_name,metric_value,errors_count,notes" in content
        assert "task.created,1,1,source=outbox_events" in content
        assert "worker.errors,1,1,source=worker_errors" in content
    finally:
        if "output" in locals() and output.exists():
            output.unlink()
        db.close()



def test_audit_report_csv_contains_stage7_event_chain_metrics():
    enriched = build_event_envelope(
        event_type=EnrichmentEventType.TASK_ENRICHED,
        producer="enrichment-service",
        correlation_id="correlation-audit-chain-1",
        payload={"task_id": "task-audit-chain-1", "metadata": {"deadline_bucket": "later"}},
    )
    sent = build_event_envelope(
        event_type=NotificationEventType.SENT,
        producer="notification-service",
        correlation_id=enriched["correlation_id"],
        payload={"task_id": "task-audit-chain-1", "source_event_id": enriched["event_id"]},
    )
    db = SessionLocal()
    try:
        db.add(create_outbox_event(envelope=enriched, aggregate_type="task", aggregate_id="task-audit-chain-1"))
        db.add(create_outbox_event(envelope=sent, aggregate_type="notification", aggregate_id="delivery-audit-chain-1"))
        db.commit()

        output = Path.cwd() / "test_stage7_audit_report.csv"
        generated = generate_audit_report(db=db, output_path=str(output))
        content = generated.read_text(encoding="utf-8")
        assert "task.enriched,1,0,source=outbox_events" in content
        assert "notification.sent,1,0,source=outbox_events" in content
    finally:
        if "output" in locals() and output.exists():
            output.unlink()
        db.close()

def test_task_rbac_audit_readiness_and_error_format():
    owner_token = make_token("user-1", "owner@example.com", roles=["user"], team_ids=["team-1"])
    assignee_token = make_token("user-2", "assignee@example.com", roles=["user"], team_ids=["team-1"])
    teamlead_token = make_token("user-3", "lead@example.com", roles=["teamlead"], team_ids=["team-1"])
    admin_token = make_token("user-4", "admin@example.com", roles=["admin"], team_ids=[])
    outsider_token = make_token("user-5", "other@example.com", roles=["user"], team_ids=["team-2"])

    with TestClient(app) as client:
        readiness_response = client.get("/readiness")
        assert readiness_response.status_code == 200
        assert readiness_response.json()["database"] == "ok"

        create_response = client.post(
            "/tasks",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "title": "Build RBAC branch",
                "description": "Extend task-service access control",
                "assignee_id": "user-2",
                "team_id": "team-1",
                "priority": "high",
                "deadline": "2026-04-30T18:00:00Z",
            },
        )
        assert create_response.status_code == 201
        task_id = create_response.json()["id"]

        owner_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {owner_token}"})
        assert owner_list_response.status_code == 200
        assert owner_list_response.json()["total"] == 1
        assert len(owner_list_response.json()["items"]) == 1

        assignee_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {assignee_token}"})
        assert assignee_list_response.status_code == 200
        assert assignee_list_response.json()["total"] == 1
        assert len(assignee_list_response.json()["items"]) == 1

        teamlead_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {teamlead_token}"})
        assert teamlead_list_response.status_code == 200
        assert teamlead_list_response.json()["total"] == 1
        assert len(teamlead_list_response.json()["items"]) == 1

        admin_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {admin_token}"})
        assert admin_list_response.status_code == 200
        assert admin_list_response.json()["total"] == 1
        assert len(admin_list_response.json()["items"]) == 1

        outsider_list_response = client.get("/tasks", headers={"Authorization": f"Bearer {outsider_token}"})
        assert outsider_list_response.status_code == 200
        assert outsider_list_response.json()["total"] == 0
        assert outsider_list_response.json()["items"] == []

        assignee_read = client.get(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {assignee_token}"})
        assert assignee_read.status_code == 200

        teamlead_update = client.patch(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {teamlead_token}"},
            json={"priority": "medium"},
        )
        assert teamlead_update.status_code == 200
        assert teamlead_update.json()["priority"] == "medium"

        assignee_status = client.patch(
            f"/tasks/{task_id}/status",
            headers={"Authorization": f"Bearer {assignee_token}"},
            json={"status": "in_progress", "comment": "Assignee started work"},
        )
        assert assignee_status.status_code == 200
        assert assignee_status.json()["status"] == "in_progress"

        teamlead_history = client.get(
            f"/tasks/{task_id}/history",
            headers={"Authorization": f"Bearer {teamlead_token}"},
        )
        assert teamlead_history.status_code == 200
        assert len(teamlead_history.json()["items"]) == 1

        assignee_delete = client.delete(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {assignee_token}"})
        assert assignee_delete.status_code == 403
        assert assignee_delete.json()["error"]["code"] == "forbidden"

        outsider_read = client.get(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {outsider_token}"})
        assert outsider_read.status_code == 404

        admin_delete = client.delete(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {admin_token}"})
        assert admin_delete.status_code == 204

        deleted_task_response = client.get(f"/tasks/{task_id}", headers={"Authorization": f"Bearer {owner_token}"})
        assert deleted_task_response.status_code == 404
    db = SessionLocal()
    try:
        actions = db.query(AuditLog.action).order_by(AuditLog.created_at.asc()).all()
        action_list = [action for (action,) in actions]
        assert action_list.count("task.created") == 1
        assert action_list.count("task.updated") == 1
        assert action_list.count("task.status_changed") == 1
        assert action_list.count("task.deleted") == 1

        outbox_event_types = db.query(OutboxEvent.event_type).order_by(OutboxEvent.created_at.asc()).all()
        outbox_type_list = [event_type for (event_type,) in outbox_event_types]
        assert outbox_type_list.count("task.created") == 1
        assert outbox_type_list.count("task.status_changed") == 1
        assert outbox_type_list.count("task.deleted") == 1
    finally:
        db.close()


def test_task_list_supports_filters_pagination_and_sorting():
    owner_token = make_token("user-11", "owner2@example.com", roles=["user"], team_ids=["team-11"])
    teamlead_token = make_token("user-13", "lead2@example.com", roles=["teamlead"], team_ids=["team-11"])
    outsider_token = make_token("user-15", "other2@example.com", roles=["user"], team_ids=["team-22"])
    admin_token = make_token("user-14", "admin2@example.com", roles=["admin"], team_ids=[])

    with TestClient(app) as client:
        high_priority = client.post(
            "/tasks",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "title": "High priority",
                "description": "Visible to teamlead",
                "assignee_id": "user-12",
                "team_id": "team-11",
                "priority": "high",
                "deadline": "2026-04-30T18:00:00Z",
            },
        )
        assert high_priority.status_code == 201
        high_task_id = high_priority.json()["id"]

        low_priority = client.post(
            "/tasks",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "title": "Low priority",
                "description": "No deadline",
                "assignee_id": "user-11",
                "team_id": "team-11",
                "priority": "low"
            },
        )
        assert low_priority.status_code == 201
        low_task_id = low_priority.json()["id"]

        external_task = client.post(
            "/tasks",
            headers={"Authorization": f"Bearer {outsider_token}"},
            json={
                "title": "External team",
                "description": "Must stay invisible for teamlead",
                "assignee_id": "user-15",
                "team_id": "team-22",
                "priority": "medium",
                "deadline": "2026-05-05T18:00:00Z",
            },
        )
        assert external_task.status_code == 201

        move_high_task = client.patch(
            f"/tasks/{high_task_id}/status",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"status": "in_progress", "comment": "Started work"},
        )
        assert move_high_task.status_code == 200

        filtered_by_status = client.get(
            "/tasks?status=in_progress",
            headers={"Authorization": f"Bearer {teamlead_token}"},
        )
        assert filtered_by_status.status_code == 200
        assert filtered_by_status.json()["total"] == 1
        assert filtered_by_status.json()["items"][0]["id"] == high_task_id

        filtered_by_owner = client.get(
            "/tasks?owner_id=user-11",
            headers={"Authorization": f"Bearer {teamlead_token}"},
        )
        assert filtered_by_owner.status_code == 200
        assert filtered_by_owner.json()["total"] == 2

        paged_sorted = client.get(
            "/tasks?page=1&page_size=1&sort_by=priority&sort_order=desc",
            headers={"Authorization": f"Bearer {teamlead_token}"},
        )
        assert paged_sorted.status_code == 200
        assert paged_sorted.json()["total"] == 2
        assert paged_sorted.json()["page"] == 1
        assert paged_sorted.json()["page_size"] == 1
        assert paged_sorted.json()["pages"] == 2
        assert len(paged_sorted.json()["items"]) == 1
        assert paged_sorted.json()["items"][0]["id"] == high_task_id

        second_page = client.get(
            "/tasks?page=2&page_size=1&sort_by=priority&sort_order=desc",
            headers={"Authorization": f"Bearer {teamlead_token}"},
        )
        assert second_page.status_code == 200
        assert len(second_page.json()["items"]) == 1
        assert second_page.json()["items"][0]["id"] == low_task_id

        deadline_sorted = client.get(
            "/tasks?sort_by=deadline&sort_order=asc",
            headers={"Authorization": f"Bearer {teamlead_token}"},
        )
        assert deadline_sorted.status_code == 200
        assert deadline_sorted.json()["total"] == 2
        assert deadline_sorted.json()["items"][0]["id"] == high_task_id
        assert deadline_sorted.json()["items"][1]["id"] == low_task_id
        assert deadline_sorted.json()["items"][1]["deadline"] is None

        admin_all_tasks = client.get(
            "/tasks?team_id=team-22",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert admin_all_tasks.status_code == 200
        assert admin_all_tasks.json()["total"] == 1
        assert admin_all_tasks.json()["items"][0]["team_id"] == "team-22"

def test_idempotency_replays_create_and_status_change_without_duplicate_audit():
    owner_token = make_token("user-21", "owner3@example.com", roles=["user"], team_ids=["team-31"])

    with TestClient(app) as client:
        create_payload = {
            "title": "Idempotent task",
            "description": "Create must not duplicate",
            "assignee_id": "user-22",
            "team_id": "team-31",
            "priority": "high",
            "deadline": "2026-06-01T12:00:00Z",
        }
        create_headers = {
            "Authorization": f"Bearer {owner_token}",
            "Idempotency-Key": "create-key-1",
        }

        first_create = client.post("/tasks", headers=create_headers, json=create_payload)
        assert first_create.status_code == 201
        task_id = first_create.json()["id"]

        repeated_create = client.post("/tasks", headers=create_headers, json=create_payload)
        assert repeated_create.status_code == 201
        assert repeated_create.json() == first_create.json()

        conflicting_create = client.post(
            "/tasks",
            headers=create_headers,
            json={**create_payload, "title": "Different payload"},
        )
        assert conflicting_create.status_code == 409
        assert conflicting_create.json()["error"]["code"] == "conflict"

        owner_tasks = client.get(
            "/tasks?owner_id=user-21",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert owner_tasks.status_code == 200
        assert owner_tasks.json()["total"] == 1
        assert owner_tasks.json()["items"][0]["id"] == task_id

        status_headers = {
            "Authorization": f"Bearer {owner_token}",
            "Idempotency-Key": "status-key-1",
        }
        status_payload = {"status": "in_progress", "comment": "Start once"}

        first_status = client.patch(f"/tasks/{task_id}/status", headers=status_headers, json=status_payload)
        assert first_status.status_code == 200
        assert first_status.json()["status"] == "in_progress"

        repeated_status = client.patch(f"/tasks/{task_id}/status", headers=status_headers, json=status_payload)
        assert repeated_status.status_code == 200
        assert repeated_status.json() == first_status.json()

        conflicting_status = client.patch(
            f"/tasks/{task_id}/status",
            headers=status_headers,
            json={"status": "review", "comment": "Different payload"},
        )
        assert conflicting_status.status_code == 409
        assert conflicting_status.json()["error"]["code"] == "conflict"

        history = client.get(
            f"/tasks/{task_id}/history",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert history.status_code == 200
        assert len(history.json()["items"]) == 1
        assert history.json()["items"][0]["to_status"] == "in_progress"

    db = SessionLocal()
    try:
        create_count = db.query(AuditLog).filter(AuditLog.action == "task.created").count()
        status_count = db.query(AuditLog).filter(AuditLog.action == "task.status_changed").count()
        create_outbox_count = db.query(OutboxEvent).filter(OutboxEvent.event_type == "task.created").count()
        status_outbox_count = db.query(OutboxEvent).filter(OutboxEvent.event_type == "task.status_changed").count()
        assert create_count == 1
        assert status_count == 1
        assert create_outbox_count == 1
        assert status_outbox_count == 1
    finally:
        db.close()


def test_audit_report_endpoint_requires_admin_and_writes_csv():
    report_path = Path.cwd() / "test_endpoint_audit_report.csv"
    if report_path.exists():
        report_path.unlink()
    settings = Settings(
        database_url=os.environ["TASK_DATABASE_URL"],
        jwt_secret="test-secret",
        audit_report_path=str(report_path),
        auth_database_url=None,
    )
    app.dependency_overrides[get_settings] = lambda: settings

    user_token = make_token("report-user-1", "report-user@example.com", roles=["user"], team_ids=[])
    admin_token = make_token("report-admin-1", "report-admin@example.com", roles=["admin"], team_ids=[])

    try:
        with TestClient(app) as client:
            forbidden = client.post("/audit/report", headers={"Authorization": f"Bearer {user_token}"})
            assert forbidden.status_code == 403

            response = client.post("/audit/report", headers={"Authorization": f"Bearer {admin_token}"})
            assert response.status_code == 200
            assert response.json()["filename"] == report_path.name
            assert response.json()["size_bytes"] > 0
            assert report_path.exists()
            assert report_path.read_text(encoding="utf-8").startswith("date,metric_name,metric_value")
    finally:
        app.dependency_overrides.pop(get_settings, None)
        if report_path.exists():
            report_path.unlink()


def test_task_item_cache_hit_and_invalidation_after_update(monkeypatch):
    fake_redis = FakeRedis()
    cache_settings = Settings(
        database_url=os.environ["TASK_DATABASE_URL"],
        jwt_secret="test-secret",
        redis_url="redis://fake",
        task_cache_ttl_seconds=60,
        task_write_rate_limit_requests=100,
        task_write_rate_limit_window_seconds=60,
    )
    app.dependency_overrides[get_settings] = lambda: cache_settings
    monkeypatch.setattr(task_routers, "get_redis_client", lambda settings: fake_redis)

    owner_token = make_token("cache-user-1", "cache@example.com", roles=["user"], team_ids=["cache-team-1"])
    headers = {"Authorization": f"Bearer {owner_token}"}

    try:
        with TestClient(app) as client:
            create_response = client.post(
                "/tasks",
                headers=headers,
                json={
                    "title": "Cache me",
                    "description": "Read endpoint should be cached",
                    "assignee_id": "cache-user-1",
                    "team_id": "cache-team-1",
                    "priority": "high",
                },
            )
            assert create_response.status_code == 201
            task_id = create_response.json()["id"]

            first_read = client.get(f"/tasks/{task_id}", headers=headers)
            second_read = client.get(f"/tasks/{task_id}", headers=headers)
            assert first_read.status_code == 200
            assert second_read.status_code == 200
            assert second_read.json() == first_read.json()
            assert fake_redis.setex_calls >= 1
            assert list(fake_redis.scan_iter("tasks:*"))

            update_response = client.patch(
                f"/tasks/{task_id}",
                headers=headers,
                json={"priority": "medium"},
            )
            assert update_response.status_code == 200
            assert list(fake_redis.scan_iter("tasks:*")) == []
    finally:
        app.dependency_overrides.pop(get_settings, None)
