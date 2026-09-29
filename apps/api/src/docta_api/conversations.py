import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from time import perf_counter
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from docta_api.api_errors import APIError
from docta_api.conversation_models import (
    Citation,
    Conversation,
    Message,
    MessageState,
    RetrievedEvidence,
)
from docta_api.database import Database
from docta_api.identity import AuthenticatedIdentity
from docta_api.models import (
    Chunk,
    CorpusVersion,
    Course,
    CourseMembership,
    Document,
    DocumentVersion,
    User,
)
from docta_api.rag import (
    PROMPT_VERSION,
    RETRIEVAL_VERSION,
    Evidence,
    HistoryTurn,
    RAGFailure,
    RetrievalScope,
    TutorDraft,
    TutorRequest,
)

logger = logging.getLogger("docta.conversation")


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


class CitationView(BaseModel):
    chunk_id: UUID
    document_version_id: UUID
    document_title: str
    document_sha256: str
    page: int
    page_end: int
    fragment: str
    content_hash: str
    quote: str


class AnswerView(BaseModel):
    mode: str
    answer: str
    grounded: bool
    citations: list[CitationView]


class MessageView(BaseModel):
    id: UUID
    course_id: UUID
    conversation_id: UUID
    corpus_version_id: UUID | None
    sequence: int
    question: str
    state: MessageState
    response: AnswerView | None
    failure_code: str | None
    failure_message: str | None
    created_at: datetime
    completed_at: datetime | None


class ConversationView(BaseModel):
    id: UUID
    course_id: UUID
    created_at: datetime


class HistoryView(ConversationView):
    messages: list[MessageView]
    next_after_sequence: int | None


@dataclass(frozen=True)
class AcceptedMessage:
    message: MessageView
    created: bool
    owner_user_id: UUID
    correlation_id: str


def idempotency_key(value: str | None) -> str:
    if value is None or not value.strip() or len(value.strip()) > 255:
        raise APIError(400, "idempotency_key_required", "An Idempotency-Key is required.")
    return value.strip()


def _not_found() -> APIError:
    return APIError(404, "conversation_not_found", "The conversation was not found.")


def _member(session: Session, identity: AuthenticatedIdentity, course_id: UUID) -> UUID:
    user_id = session.scalar(
        select(User.id)
        .join(CourseMembership, CourseMembership.user_id == User.id)
        .where(
            CourseMembership.course_id == course_id,
            User.oidc_issuer == identity.issuer,
            User.oidc_subject == identity.subject,
        )
    )
    if user_id is None:
        raise _not_found()
    return user_id


def _owned(
    session: Session, identity: AuthenticatedIdentity, conversation_id: UUID, *, lock: bool = False
) -> Conversation:
    query = (
        select(Conversation)
        .join(User, User.id == Conversation.owner_user_id)
        .join(
            CourseMembership,
            (CourseMembership.user_id == User.id)
            & (CourseMembership.course_id == Conversation.course_id),
        )
        .where(
            Conversation.id == conversation_id,
            User.oidc_issuer == identity.issuer,
            User.oidc_subject == identity.subject,
        )
    )
    if lock:
        query = query.with_for_update(of=Conversation)
    conversation = session.scalar(query)
    if conversation is None:
        raise _not_found()
    return conversation


class ConversationService:
    def __init__(
        self, database: Database, *, model_version: str, message_timeout_seconds: int = 90
    ) -> None:
        self.database = database
        self.model_version = model_version
        self.message_timeout_seconds = message_timeout_seconds

    def create(
        self, identity: AuthenticatedIdentity, course_id: UUID, key: str | None
    ) -> ConversationView:
        key = idempotency_key(key)
        with self.database.session() as session:
            user_id = _member(session, identity, course_id)
            session.execute(
                insert(Conversation)
                .values(
                    id=uuid4(),
                    course_id=course_id,
                    owner_user_id=user_id,
                    idempotency_key=key,
                )
                .on_conflict_do_nothing(constraint="uq_conversations_creation")
            )
            conversation = session.scalars(
                select(Conversation).where(
                    Conversation.course_id == course_id,
                    Conversation.owner_user_id == user_id,
                    Conversation.idempotency_key == key,
                )
            ).one()
            return ConversationView.model_validate(conversation, from_attributes=True)

    def accept(
        self,
        identity: AuthenticatedIdentity,
        conversation_id: UUID,
        question: str,
        key: str | None,
        correlation_id: str,
    ) -> AcceptedMessage:
        key = idempotency_key(key)
        with self.database.session() as session:
            conversation = _owned(session, identity, conversation_id, lock=True)
            self._expire(session, conversation_id)
            existing = session.scalar(
                select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.idempotency_key == key,
                )
            )
            if existing is not None:
                if existing.question != question:
                    raise APIError(
                        409,
                        "idempotency_key_reused",
                        "The key has already been used for a different question.",
                    )
                return AcceptedMessage(
                    self._view(session, existing),
                    False,
                    conversation.owner_user_id,
                    existing.correlation_id,
                )
            if (
                session.scalar(
                    select(Message.id).where(
                        Message.conversation_id == conversation_id,
                        Message.state == MessageState.PENDING,
                    )
                )
                is not None
            ):
                raise APIError(409, "conversation_busy", "A question is already pending.")
            # Capture only a READY active corpus from this authorized course, never from a client.
            corpus_id = session.scalar(
                select(CorpusVersion.id)
                .join(
                    Course,
                    (Course.active_corpus_version_id == CorpusVersion.id)
                    & (Course.id == CorpusVersion.course_id),
                )
                .where(Course.id == conversation.course_id, CorpusVersion.state == "READY")
            )
            now = session.scalar(select(func.clock_timestamp()))
            message = Message(
                id=uuid4(),
                course_id=conversation.course_id,
                conversation_id=conversation.id,
                corpus_version_id=corpus_id,
                sequence=conversation.next_sequence,
                question=question,
                idempotency_key=key,
                state=MessageState.PENDING,
                correlation_id=correlation_id,
                prompt_version=PROMPT_VERSION,
                retrieval_config_version=RETRIEVAL_VERSION,
                model_version=self.model_version,
                deadline_at=now + timedelta(seconds=self.message_timeout_seconds),
            )
            conversation.next_sequence += 1
            session.add(message)
            session.flush()
            result = AcceptedMessage(
                self._view(session, message), True, conversation.owner_user_id, correlation_id
            )
        # The context manager has committed before any retrieval or model can be scheduled.
        self.log(result, "persist", "accepted", perf_counter())
        return result

    def history(
        self,
        identity: AuthenticatedIdentity,
        conversation_id: UUID,
        after_sequence: int = 0,
        limit: int = 50,
    ) -> HistoryView:
        with self.database.session() as session:
            conversation = _owned(session, identity, conversation_id)
            self._expire(session, conversation_id)
            messages = session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.sequence > after_sequence,
                )
                .order_by(Message.sequence)
                .limit(limit + 1)
            ).all()
            return HistoryView(
                id=conversation.id,
                course_id=conversation.course_id,
                created_at=conversation.created_at,
                messages=[self._view(session, message) for message in messages[:limit]],
                next_after_sequence=messages[limit - 1].sequence if len(messages) > limit else None,
            )

    def read(
        self, identity: AuthenticatedIdentity, conversation_id: UUID, message_id: UUID
    ) -> MessageView:
        with self.database.session() as session:
            _owned(session, identity, conversation_id)
            self._expire(session, conversation_id)
            message = session.scalar(
                select(Message).where(
                    Message.id == message_id,
                    Message.conversation_id == conversation_id,
                )
            )
            if message is None:
                raise _not_found()
            return self._view(session, message)

    def request(self, accepted: AcceptedMessage, evidence: tuple[Evidence, ...]) -> TutorRequest:
        message = accepted.message
        if message.corpus_version_id is None:
            raise RAGFailure("COURSE_CORPUS_NOT_READY")
        scope = RetrievalScope(
            message.course_id,
            message.corpus_version_id,
            accepted.owner_user_id,
            message.conversation_id,
            message.id,
            accepted.correlation_id,
        )
        with self.database.session() as session:
            rows = session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == message.conversation_id,
                    Message.sequence < message.sequence,
                    Message.state == MessageState.COMPLETED,
                )
                .order_by(Message.sequence.desc())
                .limit(6)
            ).all()
            history: list[HistoryTurn] = []
            remaining = 12000
            for previous in rows:
                answer = previous.answer or ""
                size = len(previous.question) + len(answer)
                if size > remaining:
                    break
                history.append(HistoryTurn(previous.question, answer))
                remaining -= size
            return TutorRequest(scope, message.question, evidence, tuple(reversed(history)))

    def save_evidence(self, accepted: AcceptedMessage, evidence: tuple[Evidence, ...]) -> None:
        with self.database.session() as session:
            message = self._pending(session, accepted.message.id)
            if len(evidence) > 5 or len({item.chunk_id for item in evidence}) != len(evidence):
                raise RAGFailure("RETRIEVAL_SCOPE_INVALID")
            for item in evidence:
                if (
                    item.course_id != message.course_id
                    or item.corpus_version_id != message.corpus_version_id
                ):
                    raise RAGFailure("RETRIEVAL_SCOPE_INVALID")
                stored = session.execute(
                    select(Chunk, DocumentVersion, Document)
                    .join(
                        DocumentVersion,
                        (DocumentVersion.id == Chunk.document_version_id)
                        & (DocumentVersion.course_id == Chunk.course_id),
                    )
                    .join(
                        Document,
                        (Document.id == DocumentVersion.document_id)
                        & (Document.course_id == Chunk.course_id),
                    )
                    .where(
                        Chunk.id == item.chunk_id,
                        Chunk.course_id == message.course_id,
                        Chunk.corpus_version_id == message.corpus_version_id,
                    )
                ).one_or_none()
                if stored is None:
                    raise RAGFailure("RETRIEVAL_SCOPE_INVALID")
                chunk, version, document = stored
                if (
                    item.document_version_id != version.id
                    or item.document_title != document.title
                    or item.document_sha256 != version.object_sha256
                    or item.content != chunk.content
                    or item.content_hash != chunk.content_hash
                    or item.page_start != chunk.page_start
                    or item.page_end != chunk.page_end
                    or item.fragment != f"chunk-{chunk.ordinal}"
                ):
                    raise RAGFailure("RETRIEVAL_SCOPE_INVALID")
                session.add(RetrievedEvidence(message_id=message.id, **asdict(item)))

    def complete(self, message_id: UUID, draft: TutorDraft) -> None:
        with self.database.session() as session:
            message = self._pending(session, message_id)
            for index, citation in enumerate(draft.citations):
                # Composite FK proves each citation was retrieved for this question/course.
                session.add(
                    Citation(
                        message_id=message.id,
                        course_id=message.course_id,
                        chunk_id=citation.chunk_id,
                        ordinal=index,
                        quote=citation.quote,
                    )
                )
            message.answer = draft.answer
            message.mode = draft.mode
            message.grounded = draft.grounded
            message.state = MessageState.COMPLETED
            message.completed_at = func.clock_timestamp()

    def fail(self, message_id: UUID, code: str) -> None:
        with self.database.session() as session:
            session.execute(
                update(Message)
                .where(
                    Message.id == message_id,
                    Message.state == MessageState.PENDING,
                )
                .values(
                    state=MessageState.FAILED,
                    failure_code=code,
                    completed_at=func.clock_timestamp(),
                )
            )

    def expire(self) -> None:
        with self.database.session() as session:
            self._expire(session)

    @staticmethod
    def _expire(session: Session, conversation_id: UUID | None = None) -> None:
        statement = update(Message).where(
            Message.state == MessageState.PENDING,
            Message.deadline_at <= func.clock_timestamp(),
        )
        if conversation_id is not None:
            statement = statement.where(Message.conversation_id == conversation_id)
        session.execute(
            statement.values(
                state=MessageState.FAILED,
                failure_code="PROCESSING_INTERRUPTED",
                completed_at=func.clock_timestamp(),
            )
        )

    @staticmethod
    def _pending(session: Session, message_id: UUID) -> Message:
        message = session.scalar(
            select(Message)
            .where(
                Message.id == message_id,
                Message.state == MessageState.PENDING,
                Message.deadline_at > func.clock_timestamp(),
            )
            .with_for_update()
        )
        if message is None:
            raise RAGFailure("PROCESSING_INTERRUPTED")
        return message

    @staticmethod
    def _view(session: Session, message: Message) -> MessageView:
        response = None
        if message.state == MessageState.COMPLETED:
            rows = session.execute(
                select(Citation, RetrievedEvidence)
                .join(
                    RetrievedEvidence,
                    (RetrievedEvidence.message_id == Citation.message_id)
                    & (RetrievedEvidence.chunk_id == Citation.chunk_id)
                    & (RetrievedEvidence.course_id == Citation.course_id),
                )
                .where(Citation.message_id == message.id)
                .order_by(Citation.ordinal)
            ).all()
            response = AnswerView(
                mode=message.mode,
                answer=message.answer,
                grounded=message.grounded,
                citations=[
                    CitationView(
                        chunk_id=e.chunk_id,
                        document_version_id=e.document_version_id,
                        document_title=e.document_title,
                        document_sha256=e.document_sha256,
                        page=e.page_start,
                        page_end=e.page_end,
                        fragment=e.fragment,
                        content_hash=e.content_hash,
                        quote=c.quote,
                    )
                    for c, e in rows
                ],
            )
        return MessageView(
            id=message.id,
            course_id=message.course_id,
            conversation_id=message.conversation_id,
            corpus_version_id=message.corpus_version_id,
            sequence=message.sequence,
            question=message.question,
            state=message.state,
            response=response,
            failure_code=message.failure_code,
            failure_message=(
                "No se pudo completar la respuesta. La pregunta está guardada."
                if message.failure_code
                else None
            ),
            created_at=message.created_at,
            completed_at=message.completed_at,
        )

    @staticmethod
    def log(
        accepted: AcceptedMessage,
        operation: str,
        outcome: str,
        started: float,
        document_version_id: UUID | None = None,
    ) -> None:
        message = accepted.message
        logger.info(
            json.dumps(
                {
                    "correlation_id": accepted.correlation_id,
                    "course_id": str(message.course_id),
                    "conversation_id": str(message.conversation_id),
                    "message_id": str(message.id),
                    "document_version_id": str(document_version_id)
                    if document_version_id
                    else None,
                    "corpus_version_id": str(message.corpus_version_id)
                    if message.corpus_version_id
                    else None,
                    "operation": operation,
                    "outcome": outcome,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                },
                separators=(",", ":"),
            )
        )
