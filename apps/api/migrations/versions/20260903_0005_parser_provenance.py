"""Record the effective parser version for document provenance.

Revision ID: 20260903_0005
Revises: 20260903_0004
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0005"
down_revision: str | None = "20260903_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("parser_version", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "parser_version")
