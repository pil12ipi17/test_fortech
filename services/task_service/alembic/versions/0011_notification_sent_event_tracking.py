"""Track notification sent event publication."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "task_0011_notification_sent_event_tracking"
down_revision = "task_0010_notification_correlation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notification_deliveries") as batch_op:
        batch_op.add_column(sa.Column("sent_event_published_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(
            op.f("ix_notification_deliveries_sent_event_published_at"),
            ["sent_event_published_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_deliveries") as batch_op:
        batch_op.drop_index(op.f("ix_notification_deliveries_sent_event_published_at"))
        batch_op.drop_column("sent_event_published_at")
