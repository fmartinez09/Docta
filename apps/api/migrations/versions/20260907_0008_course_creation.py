"""Idempotent course creation for retryable browser submissions."""

import sqlalchemy as sa
from alembic import op

revision = "20260907_0008"
down_revision = "20260907_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_creations",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("idempotency_key", sa.String(255), primary_key=True),
        sa.Column(
            "course_id", sa.Uuid(), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("course_creations")
