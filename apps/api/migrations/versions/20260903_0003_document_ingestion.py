"""Add document upload, ingestion, FTS corpus and publication state.

Revision ID: 20260903_0003
Revises: 20260903_0002
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0003"
down_revision: str | None = "20260903_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "courses",
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "courses",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_documents_course",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_documents_created_by_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("course_id", "id", name="uq_documents_course_id_id"),
    )
    op.create_index("ix_documents_course_id", "documents", ["course_id"])

    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("expected_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expected_sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("pipeline_version", sa.String(length=100), nullable=False),
        sa.Column("upload_idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("upload_request_hash", sa.String(length=64), nullable=False),
        sa.Column("confirmation_idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("confirmation_request_hash", sa.String(length=64), nullable=True),
        sa.Column("object_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("object_sha256", sa.String(length=64), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("expected_size_bytes > 0", name="ck_document_versions_expected_size"),
        sa.CheckConstraint(
            "expected_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_document_versions_expected_sha256",
        ),
        sa.CheckConstraint(
            "state IN ('AWAITING_UPLOAD', 'QUEUED', 'PROCESSING', 'INDEXED', "
            "'FAILED', 'REJECTED', 'OCR_REQUIRED')",
            name="ck_document_versions_state",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "document_id"],
            ["documents.course_id", "documents.id"],
            name="fk_document_versions_course_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.UniqueConstraint("course_id", "id", name="uq_document_versions_course_id_id"),
        sa.UniqueConstraint("storage_key", name="uq_document_versions_storage_key"),
        sa.UniqueConstraint(
            "course_id",
            "upload_idempotency_key",
            name="uq_document_versions_upload_idempotency",
        ),
    )
    op.create_index("ix_document_versions_course_id", "document_versions", ["course_id"])

    op.create_table(
        "corpus_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("pipeline_version", sa.String(length=100), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('BUILDING', 'READY', 'FAILED', 'RETIRED')",
            name="ck_corpus_versions_state",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_corpus_versions_course",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_corpus_versions"),
        sa.UniqueConstraint("course_id", "id", name="uq_corpus_versions_course_id_id"),
    )
    op.create_index("ix_corpus_versions_course_id", "corpus_versions", ["course_id"])
    op.add_column(
        "courses",
        sa.Column("active_corpus_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_courses_active_corpus_version",
        "courses",
        "corpus_versions",
        ["active_corpus_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "corpus_version_documents",
        sa.Column("corpus_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["course_id", "corpus_version_id"],
            ["corpus_versions.course_id", "corpus_versions.id"],
            name="fk_corpus_documents_course_corpus",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "document_version_id"],
            ["document_versions.course_id", "document_versions.id"],
            name="fk_corpus_documents_course_document_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "corpus_version_id",
            "document_version_id",
            name="pk_corpus_version_documents",
        ),
    )

    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("corpus_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('spanish'::regconfig, coalesce(content, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.CheckConstraint("page_start > 0", name="ck_chunks_page_start"),
        sa.CheckConstraint("page_end >= page_start", name="ck_chunks_page_range"),
        sa.ForeignKeyConstraint(
            ["course_id", "corpus_version_id"],
            ["corpus_versions.course_id", "corpus_versions.id"],
            name="fk_chunks_course_corpus",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "document_version_id"],
            ["document_versions.course_id", "document_versions.id"],
            name="fk_chunks_course_document_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_chunk_id"],
            ["chunks.id"],
            name="fk_chunks_parent_chunk",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.UniqueConstraint("corpus_version_id", "ordinal", name="uq_chunks_corpus_ordinal"),
    )
    op.create_index(
        "ix_chunks_course_corpus",
        "chunks",
        ["course_id", "corpus_version_id"],
    )
    op.create_index(
        "ix_chunks_search_vector",
        "chunks",
        ["search_vector"],
        postgresql_using="gin",
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=32), server_default="ingestion", nullable=False),
        sa.Column("pipeline_version", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("fencing_token", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("correlation_id", sa.String(length=100), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_ingestion_jobs_state",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "document_version_id"],
            ["document_versions.course_id", "document_versions.id"],
            name="fk_ingestion_jobs_course_document_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_jobs"),
        sa.UniqueConstraint(
            "job_type",
            "document_version_id",
            "pipeline_version",
            name="uq_ingestion_jobs_logical_job",
        ),
    )
    op.create_index("ix_ingestion_jobs_course_id", "ingestion_jobs", ["course_id"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_course_id", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_index("ix_chunks_search_vector", table_name="chunks")
    op.drop_index("ix_chunks_course_corpus", table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("corpus_version_documents")
    op.drop_constraint("fk_courses_active_corpus_version", "courses", type_="foreignkey")
    op.drop_column("courses", "active_corpus_version_id")
    op.drop_index("ix_corpus_versions_course_id", table_name="corpus_versions")
    op.drop_table("corpus_versions")
    op.drop_index("ix_document_versions_course_id", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index("ix_documents_course_id", table_name="documents")
    op.drop_table("documents")
    op.drop_column("courses", "updated_at")
    op.drop_column("courses", "version")
