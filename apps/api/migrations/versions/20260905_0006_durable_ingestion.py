"""Transactional outbox and recoverable ingestion leases.

Revision ID: 20260905_0006
Revises: 20260903_0005
"""

import sqlalchemy as sa
from alembic import op

revision = "20260905_0006"
down_revision = "20260903_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_ingestion_jobs_course_id_id", "ingestion_jobs", ["course_id", "id"]
    )
    op.add_column("ingestion_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("publish_token", sa.Uuid()),
        sa.Column("publish_lease_until", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["course_id", "job_id"],
            ["ingestion_jobs.course_id", "ingestion_jobs.id"],
            name="fk_outbox_course_job",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("job_id", "kind", name="uq_outbox_job_kind"),
        sa.CheckConstraint("kind IN ('ingestion', 'dead')", name="ck_outbox_kind"),
    )
    op.create_index("ix_outbox_dispatch", "outbox_events", ["published_at", "available_at"])
    # Stop the old inline API/worker before applying this migration. Preserve old attempts;
    # invalidate their fencing tokens and recover queued/interrupted jobs via the outbox.
    op.execute("""
        UPDATE ingestion_jobs SET state = 'PENDING', fencing_token = fencing_token + 1
        WHERE state = 'RUNNING'
    """)
    op.execute("""
        UPDATE document_versions SET state = 'QUEUED'
        WHERE id IN (SELECT document_version_id FROM ingestion_jobs WHERE state = 'PENDING')
    """)
    op.execute("""
        INSERT INTO outbox_events (id, course_id, job_id, kind)
        SELECT gen_random_uuid(), course_id, id,
               CASE WHEN state = 'FAILED' THEN 'dead' ELSE 'ingestion' END
        FROM ingestion_jobs WHERE state != 'SUCCEEDED'
    """)


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_column("ingestion_jobs", "next_attempt_at")
    op.drop_column("ingestion_jobs", "lease_expires_at")
    op.drop_constraint("uq_ingestion_jobs_course_id_id", "ingestion_jobs", type_="unique")
