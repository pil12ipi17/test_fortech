"""Add notification delivery correlation id."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "task_0010_notification_correlation"
down_revision = "task_0009_task_enrichments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notification_deliveries") as batch_op:
        batch_op.add_column(sa.Column("correlation_id", sa.String(length=64), nullable=True))
        batch_op.create_index(op.f("ix_notification_deliveries_correlation_id"), ["correlation_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("notification_deliveries") as batch_op:
        batch_op.drop_index(op.f("ix_notification_deliveries_correlation_id"))
        batch_op.drop_column("correlation_id")