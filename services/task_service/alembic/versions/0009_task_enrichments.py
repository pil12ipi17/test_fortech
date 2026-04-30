"""Add task enrichments."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "task_0009_task_enrichments"
down_revision = "task_0008_notification_cron"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_enrichments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_event_id", sa.String(length=36), nullable=False),
        sa.Column("source_event_type", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_event_id", name="uq_task_enrichments_source_event"),
    )
    op.create_index(op.f("ix_task_enrichments_source_event_id"), "task_enrichments", ["source_event_id"], unique=False)
    op.create_index(
        op.f("ix_task_enrichments_source_event_type"),
        "task_enrichments",
        ["source_event_type"],
        unique=False,
    )
    op.create_index(op.f("ix_task_enrichments_task_id"), "task_enrichments", ["task_id"], unique=False)
    op.create_index(op.f("ix_task_enrichments_correlation_id"), "task_enrichments", ["correlation_id"], unique=False)
    op.create_index(op.f("ix_task_enrichments_created_at"), "task_enrichments", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_task_enrichments_created_at"), table_name="task_enrichments")
    op.drop_index(op.f("ix_task_enrichments_correlation_id"), table_name="task_enrichments")
    op.drop_index(op.f("ix_task_enrichments_task_id"), table_name="task_enrichments")
    op.drop_index(op.f("ix_task_enrichments_source_event_type"), table_name="task_enrichments")
    op.drop_index(op.f("ix_task_enrichments_source_event_id"), table_name="task_enrichments")
    op.drop_table("task_enrichments")