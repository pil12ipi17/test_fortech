"""Add worker event processing tables."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "task_0006_worker_event_processing"
down_revision = "task_0005_outbox_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processed_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("consumer_name", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "consumer_name", name="uq_processed_events_event_consumer"),
    )
    op.create_index(op.f("ix_processed_events_event_id"), "processed_events", ["event_id"], unique=False)
    op.create_index(op.f("ix_processed_events_consumer_name"), "processed_events", ["consumer_name"], unique=False)
    op.create_index(op.f("ix_processed_events_event_type"), "processed_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_processed_events_correlation_id"), "processed_events", ["correlation_id"], unique=False)
    op.create_index(op.f("ix_processed_events_processed_at"), "processed_events", ["processed_at"], unique=False)

    op.create_table(
        "worker_event_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("consumer_name", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_worker_event_logs_consumer_name"), "worker_event_logs", ["consumer_name"], unique=False)
    op.create_index(op.f("ix_worker_event_logs_event_id"), "worker_event_logs", ["event_id"], unique=False)
    op.create_index(op.f("ix_worker_event_logs_event_type"), "worker_event_logs", ["event_type"], unique=False)
    op.create_index(op.f("ix_worker_event_logs_correlation_id"), "worker_event_logs", ["correlation_id"], unique=False)
    op.create_index(op.f("ix_worker_event_logs_created_at"), "worker_event_logs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_worker_event_logs_created_at"), table_name="worker_event_logs")
    op.drop_index(op.f("ix_worker_event_logs_correlation_id"), table_name="worker_event_logs")
    op.drop_index(op.f("ix_worker_event_logs_event_type"), table_name="worker_event_logs")
    op.drop_index(op.f("ix_worker_event_logs_event_id"), table_name="worker_event_logs")
    op.drop_index(op.f("ix_worker_event_logs_consumer_name"), table_name="worker_event_logs")
    op.drop_table("worker_event_logs")

    op.drop_index(op.f("ix_processed_events_processed_at"), table_name="processed_events")
    op.drop_index(op.f("ix_processed_events_correlation_id"), table_name="processed_events")
    op.drop_index(op.f("ix_processed_events_event_type"), table_name="processed_events")
    op.drop_index(op.f("ix_processed_events_consumer_name"), table_name="processed_events")
    op.drop_index(op.f("ix_processed_events_event_id"), table_name="processed_events")
    op.drop_table("processed_events")
