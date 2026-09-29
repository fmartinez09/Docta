"""Pin document versions to immutable object-storage versions.

Revision ID: 20260903_0004
Revises: 20260903_0003
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0004"
down_revision: str | None = "20260903_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("storage_version_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "storage_version_id")
