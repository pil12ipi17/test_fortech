"""Add notification deliveries and worker errors."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "task_0007_notifications_audit"
down_revision = "task_0006_worker_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("recipient_user_id", sa.String(length=36), nullable=True),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_notification_deliveries_event"),
    )
    op.create_index(op.f("ix_notification_deliveries_event_id"), "notification_deliveries", ["event_id"], unique=False)
    op.create_index(op.f("ix_notification_deliveries_event_type"), "notification_deliveries", ["event_type"], unique=False)
    op.create_index(op.f("ix_notification_deliveries_task_id"), "notification_deliveries", ["task_id"], unique=False)
    op.create_index(op.f("ix_notification_deliveries_recipient_user_id"), "notification_deliveries", ["recipient_user_id"], unique=False)
    op.create_index(op.f("ix_notification_deliveries_status"), "notification_deliveries", ["status"], unique=False)
    op.create_index(op.f("ix_notification_deliveries_sent_at"), "notification_deliveries", ["sent_at"], unique=False)

    op.create_table(
        "worker_errors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("consumer_name", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_worker_errors_consumer_name"), "worker_errors", ["consumer_name"], unique=False)
    op.create_index(op.f("ix_worker_errors_event_id"), "worker_errors", ["event_id"], unique=False)
    op.create_index(op.f("ix_worker_errors_event_type"), "worker_errors", ["event_type"], unique=False)
    op.create_index(op.f("ix_worker_errors_correlation_id"), "worker_errors", ["correlation_id"], unique=False)
    op.create_index(op.f("ix_worker_errors_error_type"), "worker_errors", ["error_type"], unique=False)
    op.create_index(op.f("ix_worker_errors_created_at"), "worker_errors", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_worker_errors_created_at"), table_name="worker_errors")
    op.drop_index(op.f("ix_worker_errors_error_type"), table_name="worker_errors")
    op.drop_index(op.f("ix_worker_errors_correlation_id"), table_name="worker_errors")
    op.drop_index(op.f("ix_worker_errors_event_type"), table_name="worker_errors")
    op.drop_index(op.f("ix_worker_errors_event_id"), table_name="worker_errors")
    op.drop_index(op.f("ix_worker_errors_consumer_name"), table_name="worker_errors")
    op.drop_table("worker_errors")

    op.drop_index(op.f("ix_notification_deliveries_sent_at"), table_name="notification_deliveries")
    op.drop_index(op.f("ix_notification_deliveries_status"), table_name="notification_deliveries")
    op.drop_index(op.f("ix_notification_deliveries_recipient_user_id"), table_name="notification_deliveries")
    op.drop_index(op.f("ix_notification_deliveries_task_id"), table_name="notification_deliveries")
    op.drop_index(op.f("ix_notification_deliveries_event_type"), table_name="notification_deliveries")
    op.drop_index(op.f("ix_notification_deliveries_event_id"), table_name="notification_deliveries")
    op.drop_table("notification_deliveries")