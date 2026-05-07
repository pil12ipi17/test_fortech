"""Add notification delivery correlation id."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "task_0010_notification_correlation"
down_revision = "task_0009_task_enrichments"
branch_labels = None
depends_on = None


def _expand_alembic_version_column() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=128),
        existing_nullable=False,
    )


def _shrink_alembic_version_column() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=128),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def upgrade() -> None:
    _expand_alembic_version_column()
    with op.batch_alter_table("notification_deliveries") as batch_op:
        batch_op.add_column(sa.Column("correlation_id", sa.String(length=64), nullable=True))
        batch_op.create_index(op.f("ix_notification_deliveries_correlation_id"), ["correlation_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("notification_deliveries") as batch_op:
        batch_op.drop_index(op.f("ix_notification_deliveries_correlation_id"))
        batch_op.drop_column("correlation_id")
    _shrink_alembic_version_column()
