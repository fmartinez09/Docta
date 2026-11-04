"""Authorization shared by every retriever (D-07-022): server-captured scope, fail closed."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from docta_api.conversation_models import Conversation, Message, MessageState
from docta_api.models import CorpusVersion, CourseMembership
from docta_api.rag import RAGFailure, RetrievalScope


def validate_scope(scope: RetrievalScope) -> None:
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


def authorize_retrieval(session: Session, scope: RetrievalScope, query: str) -> None:
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
            Message.question == query,
            Message.deadline_at > func.now(),
            Conversation.owner_user_id == scope.principal_user_id,
            CorpusVersion.state == "READY",
        )
    )
    if authorized is None:
        raise RAGFailure("RETRIEVAL_SCOPE_INVALID")
