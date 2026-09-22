from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MembershipRole(StrEnum):
    TEACHER = "teacher"
    STUDENT = "student"


class DocumentVersionState(StrEnum):
    AWAITING_UPLOAD = "AWAITING_UPLOAD"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    INDEXED = "INDEXED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    OCR_REQUIRED = "OCR_REQUIRED"


class CorpusVersionState(StrEnum):
    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"
    RETIRED = "RETIRED"


class IngestionJobState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_users_oidc_identity"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    oidc_issuer: Mapped[str] = mapped_column(String(2048), nullable=False)
    oidc_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        CheckConstraint("length(btrim(title)) > 0", name="ck_courses_title_not_blank"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active_corpus_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "corpus_versions.id",
            name="fk_courses_active_corpus_version",
            use_alter=True,
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CourseMembership(Base):
    __tablename__ = "course_memberships"
    __table_args__ = (
        CheckConstraint("role IN ('teacher', 'student')", name="ck_memberships_role"),
    )

    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CourseCreation(Base):
    __tablename__ = "course_creations"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    course_id: Mapped[UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("course_id", "id", name="uq_documents_course_id_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "document_id"],
            ["documents.course_id", "documents.id"],
            name="fk_document_versions_course_document",
            ondelete="CASCADE",
        ),
        UniqueConstraint("course_id", "id", name="uq_document_versions_course_id_id"),
        UniqueConstraint("storage_key", name="uq_document_versions_storage_key"),
        UniqueConstraint(
            "course_id",
            "upload_idempotency_key",
            name="uq_document_versions_upload_idempotency",
        ),
        CheckConstraint(
            "state IN ('AWAITING_UPLOAD', 'QUEUED', 'PROCESSING', 'INDEXED', "
            "'FAILED', 'REJECTED', 'OCR_REQUIRED')",
            name="ck_document_versions_state",
        ),
        CheckConstraint("expected_size_bytes > 0", name="ck_document_versions_expected_size"),
        CheckConstraint(
            "expected_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_document_versions_expected_sha256",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    course_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    storage_version_id: Mapped[str | None] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expected_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(100), nullable=False)
    parser_version: Mapped[str | None] = mapped_column(String(100))
    upload_idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    upload_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmation_idempotency_key: Mapped[str | None] = mapped_column(String(255))
    confirmation_request_hash: Mapped[str | None] = mapped_column(String(64))
    object_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    object_sha256: Mapped[str | None] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CorpusVersion(Base):
    __tablename__ = "corpus_versions"
    __table_args__ = (
        UniqueConstraint("course_id", "id", name="uq_corpus_versions_course_id_id"),
        CheckConstraint(
            "state IN ('BUILDING', 'READY', 'FAILED', 'RETIRED')",
            name="ck_corpus_versions_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(100), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CorpusVersionDocument(Base):
    __tablename__ = "corpus_version_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "corpus_version_id"],
            ["corpus_versions.course_id", "corpus_versions.id"],
            name="fk_corpus_documents_course_corpus",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["course_id", "document_version_id"],
            ["document_versions.course_id", "document_versions.id"],
            name="fk_corpus_documents_course_document_version",
            ondelete="RESTRICT",
        ),
    )

    corpus_version_id: Mapped[UUID] = mapped_column(primary_key=True)
    document_version_id: Mapped[UUID] = mapped_column(primary_key=True)
    course_id: Mapped[UUID] = mapped_column(nullable=False)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "corpus_version_id"],
            ["corpus_versions.course_id", "corpus_versions.id"],
            name="fk_chunks_course_corpus",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["course_id", "document_version_id"],
            ["document_versions.course_id", "document_versions.id"],
            name="fk_chunks_course_document_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("corpus_version_id", "ordinal", name="uq_chunks_corpus_ordinal"),
        UniqueConstraint("course_id", "corpus_version_id", "id", name="uq_chunks_scope"),
        CheckConstraint("page_start > 0", name="ck_chunks_page_start"),
        CheckConstraint("page_end >= page_start", name="ck_chunks_page_range"),
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_chunks_course_corpus", "course_id", "corpus_version_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    course_id: Mapped[UUID] = mapped_column(nullable=False)
    corpus_version_id: Mapped[UUID] = mapped_column(nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(nullable=False)
    parent_chunk_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="RESTRICT"),
    )
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    search_vector: Mapped[object] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('spanish'::regconfig, coalesce(content, ''))", persisted=True),
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("course_id", "id", name="uq_ingestion_jobs_course_id_id"),
        ForeignKeyConstraint(
            ["course_id", "document_version_id"],
            ["document_versions.course_id", "document_versions.id"],
            name="fk_ingestion_jobs_course_document_version",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "job_type",
            "document_version_id",
            "pipeline_version",
            name="uq_ingestion_jobs_logical_job",
        ),
        CheckConstraint(
            "state IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_ingestion_jobs_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    course_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    document_version_id: Mapped[UUID] = mapped_column(nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="ingestion")
    pipeline_version: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "job_id"],
            ["ingestion_jobs.course_id", "ingestion_jobs.id"],
            name="fk_outbox_course_job",
            ondelete="CASCADE",
        ),
        UniqueConstraint("job_id", "kind", name="uq_outbox_job_kind"),
        CheckConstraint("kind IN ('ingestion', 'dead')", name="ck_outbox_kind"),
        Index("ix_outbox_dispatch", "published_at", "available_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    course_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_token: Mapped[UUID | None] = mapped_column()
    publish_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
