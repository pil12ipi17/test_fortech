"""Add task lifecycle fields and history."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "task_0002_task_lifecycle"
down_revision = "task_0001_initial_mvp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("assignee_id", sa.String(length=36), nullable=True))
    op.add_column("tasks", sa.Column("team_id", sa.String(length=36), nullable=True))
    op.add_column("tasks", sa.Column("priority", sa.String(length=16), nullable=True))
    op.add_column("tasks", sa.Column("deadline", sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE tasks SET assignee_id = owner_id WHERE assignee_id IS NULL")
    op.execute("UPDATE tasks SET team_id = owner_id WHERE team_id IS NULL")
    op.execute("UPDATE tasks SET priority = 'medium' WHERE priority IS NULL")

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.alter_column("assignee_id", existing_type=sa.String(length=36), nullable=False)
        batch_op.alter_column("team_id", existing_type=sa.String(length=36), nullable=False)
        batch_op.alter_column("priority", existing_type=sa.String(length=16), nullable=False)
        batch_op.create_index(batch_op.f("ix_tasks_assignee_id"), ["assignee_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_tasks_team_id"), ["team_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_tasks_priority"), ["priority"], unique=False)
        batch_op.create_index(batch_op.f("ix_tasks_deadline"), ["deadline"], unique=False)

    op.create_table(
        "task_status_history",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("changed_by", sa.String(length=36), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_task_status_history_task_id"), "task_status_history", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_task_status_history_task_id"), table_name="task_status_history")
    op.drop_table("task_status_history")

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_index(batch_op.f("ix_tasks_deadline"))
        batch_op.drop_index(batch_op.f("ix_tasks_priority"))
        batch_op.drop_index(batch_op.f("ix_tasks_team_id"))
        batch_op.drop_index(batch_op.f("ix_tasks_assignee_id"))
        batch_op.drop_column("deadline")
        batch_op.drop_column("priority")
        batch_op.drop_column("team_id")
        batch_op.drop_column("assignee_id")