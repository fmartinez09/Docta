"""Durable logical messages: a question and its optional validated tutor response."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from docta_api.models import Base


class MessageState(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("course_id", "id", name="uq_conversations_course_id"),
        UniqueConstraint(
            "course_id", "owner_user_id", "idempotency_key", name="uq_conversations_creation"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    course_id: Mapped[UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    next_sequence: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "conversation_id"],
            ["conversations.course_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_messages_conversation",
        ),
        ForeignKeyConstraint(
            ["course_id", "corpus_version_id"],
            ["corpus_versions.course_id", "corpus_versions.id"],
            ondelete="RESTRICT",
            name="fk_messages_corpus",
        ),
        UniqueConstraint("conversation_id", "idempotency_key", name="uq_messages_idempotency"),
        UniqueConstraint("conversation_id", "sequence", name="uq_messages_sequence"),
        UniqueConstraint("course_id", "corpus_version_id", "id", name="uq_messages_scope"),
        CheckConstraint("state IN ('pending', 'completed', 'failed')", name="ck_messages_state"),
        CheckConstraint("length(btrim(question)) BETWEEN 1 AND 4000", name="ck_messages_question"),
        CheckConstraint("sequence > 0", name="ck_messages_sequence"),
        CheckConstraint(
            "(state = 'pending' AND answer IS NULL AND mode IS NULL AND grounded IS NULL "
            "AND failure_code IS NULL AND completed_at IS NULL) OR "
            "(state = 'failed' AND answer IS NULL AND mode IS NULL AND grounded IS NULL "
            "AND failure_code IS NOT NULL AND completed_at IS NOT NULL) OR "
            "(state = 'completed' AND answer IS NOT NULL AND mode IS NOT NULL "
            "AND grounded IS NOT NULL AND failure_code IS NULL AND completed_at IS NOT NULL)",
            name="ck_messages_terminal_payload",
        ),
        CheckConstraint(
            "mode IS NULL OR mode IN ('hint','guided_question','explanation','abstain')",
            name="ck_messages_mode",
        ),
        Index(
            "uq_messages_one_pending",
            "conversation_id",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
        Index("ix_messages_deadline", "deadline_at", postgresql_where=text("state = 'pending'")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    course_id: Mapped[UUID] = mapped_column()
    conversation_id: Mapped[UUID] = mapped_column()
    corpus_version_id: Mapped[UUID | None] = mapped_column()
    sequence: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    question: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default=MessageState.PENDING)
    answer: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str | None] = mapped_column(String(32))
    grounded: Mapped[bool | None] = mapped_column(Boolean)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(100))
    retrieval_config_version: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetrievedEvidence(Base):
    __tablename__ = "retrieved_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "corpus_version_id", "message_id"],
            ["messages.course_id", "messages.corpus_version_id", "messages.id"],
            name="fk_evidence_message",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["course_id", "corpus_version_id", "chunk_id"],
            ["chunks.course_id", "chunks.corpus_version_id", "chunks.id"],
            name="fk_evidence_chunk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("course_id", "message_id", "chunk_id", name="uq_evidence_scope"),
    )

    message_id: Mapped[UUID] = mapped_column(primary_key=True)
    chunk_id: Mapped[UUID] = mapped_column(primary_key=True)
    course_id: Mapped[UUID] = mapped_column()
    corpus_version_id: Mapped[UUID] = mapped_column()
    document_version_id: Mapped[UUID] = mapped_column()
    document_title: Mapped[str] = mapped_column(String(255))
    document_sha256: Mapped[str] = mapped_column(String(64))
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    fragment: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "message_id", "chunk_id"],
            [
                "retrieved_evidence.course_id",
                "retrieved_evidence.message_id",
                "retrieved_evidence.chunk_id",
            ],
            name="fk_citations_evidence",
            ondelete="CASCADE",
        ),
        UniqueConstraint("message_id", "ordinal", name="uq_citations_ordinal"),
    )

    message_id: Mapped[UUID] = mapped_column(primary_key=True)
    chunk_id: Mapped[UUID] = mapped_column(primary_key=True)
    course_id: Mapped[UUID] = mapped_column()
    ordinal: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)
