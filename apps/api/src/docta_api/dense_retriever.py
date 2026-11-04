"""Dense retriever: exact cosine search over stored chunk embeddings; fails closed."""

import math

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, exists, literal, select

from docta_api.database import Database
from docta_api.embedding import Embedder
from docta_api.models import Chunk, ChunkEmbedding, Document, DocumentVersion
from docta_api.rag import Evidence, RAGFailure, RetrievalScope
from docta_api.retrieval_auth import authorize_retrieval, validate_scope

MAX_VERSION_LENGTH = 100  # messages.retrieval_config_version is String(100)
TOP_K = 5


class DenseRetriever:
    def __init__(self, database: Database, embedder: Embedder, max_distance: float) -> None:
        self._database = database
        self._embedder = embedder
        self._max_distance = max_distance
        self.version = f"dense-cosine-top5-d{max_distance}-{embedder.version}"
        if len(self.version) > MAX_VERSION_LENGTH:
            raise ValueError("Retrieval version exceeds the stored length")
        try:
            self._dimensions = int(embedder.version.split("|")[-2])
        except (IndexError, ValueError) as error:
            raise ValueError("Embedding version does not declare its dimensions") from error

    def retrieve(self, scope: RetrievalScope, question: str) -> tuple[Evidence, ...]:
        validate_scope(scope)
        with self._database.session() as session:
            authorize_retrieval(session, scope, question)
        # The provider call happens outside any database session and is never retried.
        try:
            vector = [float(value) for value in self._embedder.embed_query(question)]
        except Exception as error:
            raise RAGFailure("RETRIEVAL_UNAVAILABLE") from error
        if len(vector) != self._dimensions or not all(math.isfinite(v) for v in vector):
            raise RAGFailure("RETRIEVAL_UNAVAILABLE")
        embedding_version = self._embedder.version
        with self._database.session() as session:
            authorize_retrieval(session, scope, question)
            indexed = (
                (Chunk.course_id == scope.course_id)
                & (Chunk.corpus_version_id == scope.corpus_version_id)
                & (DocumentVersion.state == "INDEXED")
            )
            has_embedding = exists().where(
                ChunkEmbedding.chunk_id == Chunk.id,
                ChunkEmbedding.course_id == Chunk.course_id,
                ChunkEmbedding.corpus_version_id == Chunk.corpus_version_id,
                ChunkEmbedding.embedding_version == embedding_version,
                ChunkEmbedding.dims == self._dimensions,
            )
            missing = session.scalar(
                select(Chunk.id)
                .join(
                    DocumentVersion,
                    (DocumentVersion.id == Chunk.document_version_id)
                    & (DocumentVersion.course_id == Chunk.course_id),
                )
                .where(indexed, ~has_embedding)
                .limit(1)
            )
            if missing is not None:
                raise RAGFailure("RETRIEVAL_INDEX_MISSING")
            query_vector = literal(vector, Vector())
            distance = ChunkEmbedding.embedding.op("<=>", return_type=Float)(query_vector)
            # Pre-filter first, so rows of other dimensions never reach the distance operator.
            candidates = (
                select(
                    Chunk.id.label("chunk_id"),
                    Chunk.ordinal.label("ordinal"),
                    distance.label("distance"),
                )
                .join(
                    ChunkEmbedding,
                    (ChunkEmbedding.chunk_id == Chunk.id)
                    & (ChunkEmbedding.course_id == Chunk.course_id)
                    & (ChunkEmbedding.corpus_version_id == Chunk.corpus_version_id),
                )
                .join(
                    DocumentVersion,
                    (DocumentVersion.id == Chunk.document_version_id)
                    & (DocumentVersion.course_id == Chunk.course_id),
                )
                .where(
                    indexed,
                    ChunkEmbedding.embedding_version == embedding_version,
                    ChunkEmbedding.dims == self._dimensions,
                )
                .cte("candidates")
                .prefix_with("MATERIALIZED")
            )
            rows = session.execute(
                select(Chunk, Document.title, DocumentVersion.object_sha256)
                .join(candidates, candidates.c.chunk_id == Chunk.id)
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
                .where(candidates.c.distance <= self._max_distance)
                .order_by(candidates.c.distance, candidates.c.ordinal)
                .limit(TOP_K)
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
