"""Allow cron-dispatched notification deliveries."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "task_0008_notification_cron"
down_revision = "task_0007_notifications_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notification_deliveries") as batch_op:
        batch_op.alter_column("sent_at", existing_type=sa.DateTime(timezone=True), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("notification_deliveries") as batch_op:
        batch_op.alter_column("sent_at", existing_type=sa.DateTime(timezone=True), nullable=False)
