"""Add OIDC identities, courses and scoped memberships.

Revision ID: 20260903_0002
Revises: 20260903_0001
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0002"
down_revision: str | None = "20260903_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("oidc_issuer", sa.String(length=2048), nullable=False),
        sa.Column("oidc_subject", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_users_oidc_identity"),
    )
    op.create_table(
        "courses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_courses_title_not_blank"),
        sa.PrimaryKeyConstraint("id", name="pk_courses"),
    )
    op.create_table(
        "course_memberships",
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('teacher', 'student')", name="ck_memberships_role"),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_memberships_course",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_memberships_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("course_id", "user_id", name="pk_course_memberships"),
    )
    op.create_index(
        "ix_course_memberships_user_id",
        "course_memberships",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_course_memberships_user_id", table_name="course_memberships")
    op.drop_table("course_memberships")
    op.drop_table("courses")
    op.drop_table("users")
