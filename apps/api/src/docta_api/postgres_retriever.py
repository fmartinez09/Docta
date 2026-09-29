from sqlalchemy import func, select

from docta_api.conversation_models import Conversation, Message, MessageState
from docta_api.database import Database
from docta_api.models import (
    Chunk,
    CorpusVersion,
    CourseMembership,
    Document,
    DocumentVersion,
)
from docta_api.rag import Evidence, RAGFailure, RetrievalScope


class PostgresRetriever:
    def __init__(self, database: Database) -> None:
        self._database = database

    def retrieve(self, scope: RetrievalScope, question: str) -> tuple[Evidence, ...]:
        if not isinstance(scope, RetrievalScope) or not all(
            (
                scope.course_id,
                scope.corpus_version_id,
                scope.principal_user_id,
                scope.conversation_id,
                scope.message_id,
            )
        ):
            raise RAGFailure("RETRIEVAL_SCOPE_INVALID")
        with self._database.session() as session:
            # Recheck the durable, server-captured scope and current membership at the port.
            authorized = session.scalar(
                select(Message.id)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .join(
                    CourseMembership,
                    (CourseMembership.course_id == Conversation.course_id)
                    & (CourseMembership.user_id == Conversation.owner_user_id),
                )
                .join(
                    CorpusVersion,
                    (CorpusVersion.id == Message.corpus_version_id)
                    & (CorpusVersion.course_id == Message.course_id),
                )
                .where(
                    Message.id == scope.message_id,
                    Message.course_id == scope.course_id,
                    Message.corpus_version_id == scope.corpus_version_id,
                    Message.conversation_id == scope.conversation_id,
                    Message.state == MessageState.PENDING,
                    Message.question == question,
                    Message.deadline_at > func.now(),
                    Conversation.owner_user_id == scope.principal_user_id,
                    CorpusVersion.state == "READY",
                )
            )
            if authorized is None:
                raise RAGFailure("RETRIEVAL_SCOPE_INVALID")
            query = func.plainto_tsquery("spanish", question)
            rows = session.execute(
                select(Chunk, Document.title, DocumentVersion.object_sha256)
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
                    Chunk.course_id == scope.course_id,
                    Chunk.corpus_version_id == scope.corpus_version_id,
                    DocumentVersion.state == "INDEXED",
                    Chunk.search_vector.op("@@")(query),
                )
                .order_by(func.ts_rank_cd(Chunk.search_vector, query).desc(), Chunk.ordinal)
                .limit(5)
            ).all()
            return tuple(
                Evidence(
                    chunk_id=chunk.id,
                    course_id=chunk.course_id,
                    corpus_version_id=chunk.corpus_version_id,
                    document_version_id=chunk.document_version_id,
                    document_title=title,
                    document_sha256=checksum,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    fragment=f"chunk-{chunk.ordinal}",
                    content=chunk.content,
                    content_hash=chunk.content_hash,
                )
                for chunk, title, checksum in rows
            )
