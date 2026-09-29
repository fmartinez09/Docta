"""Establish the executable migration baseline.

Revision ID: 20260903_0001
Revises: None
Create Date: 2026-09-03
"""

from collections.abc import Sequence

revision: str = "20260903_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Alembic records this revision; domain tables begin in Increment 1."""


def downgrade() -> None:
    """No domain schema exists in the baseline revision."""

