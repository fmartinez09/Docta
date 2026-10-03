import re

from sqlalchemy import Text, any_, cast, func, select
from sqlalchemy.dialects.postgresql import ARRAY, TSQUERY

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

_TSQUERY_LEXEME = re.compile(r"'((?:[^'\\]|''|\\.)*)'")
_TSQUERY_UNESCAPE = re.compile(r"''|\\(.)")


def _query_lexemes(plain_query_text: str) -> tuple[str, ...]:
    """Distinct stemmed lexemes of a `plainto_tsquery` rendered as text."""
    lexemes = (
        _TSQUERY_UNESCAPE.sub(lambda m: m.group(1) or "'", raw)
        for raw in _TSQUERY_LEXEME.findall(plain_query_text)
    )
    return tuple(dict.fromkeys(lexemes))


def _or_tsquery_text(lexemes: tuple[str, ...]) -> str:
    # Each lexeme is quoted and escaped, so it can neither break nor extend the tsquery.
    return " | ".join(
        "'" + lexeme.replace("\\", "\\\\").replace("'", "''") + "'" for lexeme in lexemes
    )


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
            # Recall: plainto_tsquery ANDs every lexeme, so one term absent from the target chunk
            # yielded no rows. Instead, take the lexemes the question stems to (taken from the
            # tsquery itself so they match the stored tsvector exactly, no re-stemming), match any
            # of them via an OR tsquery cast from the quoted/escaped lexemes (a cast does not
            # re-stem), and require coverage: matched * 3 >= n * 2, i.e. >= ceil(2n/3) lexemes.
            # The floor keeps off-topic or one-common-word chunks out. Integer math only.
            plain_text = session.scalar(
                select(cast(func.plainto_tsquery("spanish", question), Text))
            )
            lexemes = _query_lexemes(plain_text or "")
            if not lexemes:
                return ()
            lexeme_param = cast(list(lexemes), ARRAY(Text))
            query = cast(_or_tsquery_text(lexemes), TSQUERY)
            lexeme_column = func.unnest(lexeme_param).column_valued("lexeme")
            matched = (
                select(func.count())
                .where(lexeme_column == any_(func.tsvector_to_array(Chunk.search_vector)))
                .correlate(Chunk)
                .scalar_subquery()
            )
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
                    matched * 3 >= len(lexemes) * 2,
                )
                .order_by(
                    matched.desc(),
                    func.ts_rank_cd(Chunk.search_vector, query).desc(),
                    Chunk.ordinal,
                )
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
