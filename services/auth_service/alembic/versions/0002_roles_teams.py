"""Add roles and teams to auth-service."""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "auth_0002_roles_teams"
down_revision = "auth_0001_initial_mvp"
branch_labels = None
depends_on = None


ROLE_TABLE = sa.table(
    "roles",
    sa.column("id", sa.String(length=36)),
    sa.column("code", sa.String(length=32)),
    sa.column("name", sa.String(length=100)),
    sa.column("created_at", sa.DateTime(timezone=True)),
)

ROLE_CREATED_AT = datetime(2026, 4, 14, tzinfo=timezone.utc)


def upgrade() -> None:
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE users SET updated_at = created_at WHERE updated_at IS NULL")

    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)

    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_roles_code"), "roles", ["code"], unique=True)

    op.bulk_insert(
        ROLE_TABLE,
        [
            {
                "id": "3f0ab5fe-645f-4b70-9f9c-1d66b4ec3bd1",
                "code": "user",
                "name": "User",
                "created_at": ROLE_CREATED_AT,
            },
            {
                "id": "5fdca8c6-cbb7-47fd-b857-968a7de4c703",
                "code": "teamlead",
                "name": "Team Lead",
                "created_at": ROLE_CREATED_AT,
            },
            {
                "id": "7467bc67-cc74-4fa8-bc2c-ff59009ffd66",
                "code": "admin",
                "name": "Admin",
                "created_at": ROLE_CREATED_AT,
            },
        ],
    )

    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "user_roles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role_id", sa.String(length=36), nullable=False),
        sa.Column("assigned_by", sa.String(length=36), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
    )
    op.create_index(op.f("ix_user_roles_role_id"), "user_roles", ["role_id"], unique=False)
    op.create_index(op.f("ix_user_roles_user_id"), "user_roles", ["user_id"], unique=False)

    op.create_table(
        "team_memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "team_id", name="uq_team_memberships_user_team"),
    )
    op.create_index(op.f("ix_team_memberships_team_id"), "team_memberships", ["team_id"], unique=False)
    op.create_index(op.f("ix_team_memberships_user_id"), "team_memberships", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_team_memberships_user_id"), table_name="team_memberships")
    op.drop_index(op.f("ix_team_memberships_team_id"), table_name="team_memberships")
    op.drop_table("team_memberships")

    op.drop_index(op.f("ix_user_roles_user_id"), table_name="user_roles")
    op.drop_index(op.f("ix_user_roles_role_id"), table_name="user_roles")
    op.drop_table("user_roles")

    op.drop_table("teams")

    op.drop_index(op.f("ix_roles_code"), table_name="roles")
    op.drop_table("roles")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.drop_column("is_active")