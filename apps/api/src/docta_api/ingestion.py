import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, select, update

from docta_api.database import Database
from docta_api.document_parser import (
    DocumentParser,
    DocumentProcessingFailure,
    build_chunks,
)
from docta_api.models import (
    Chunk,
    CorpusVersion,
    CorpusVersionDocument,
    CorpusVersionState,
    DocumentVersion,
    DocumentVersionState,
    IngestionJob,
    IngestionJobState,
)
from docta_api.object_storage import (
    ObjectStorage,
    ObjectStorageUnavailable,
    StoredObjectNotFound,
    StoredObjectTooLarge,
)

logger = logging.getLogger("docta.ingestion")


class JobDispatcher(Protocol):
    def dispatch(self, job_id: UUID, correlation_id: str) -> None: ...


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    course_id: UUID
    document_version_id: UUID
    storage_key: str
    storage_version_id: str
    expected_size_bytes: int
    expected_sha256: str
    pipeline_version: str
    fencing_token: int


class IngestionWorker:
    def __init__(
        self,
        *,
        database: Database,
        object_storage: ObjectStorage,
        parser: DocumentParser,
        max_document_bytes: int,
        chunk_size_characters: int,
        chunk_overlap_characters: int,
    ) -> None:
        self._database = database
        self._object_storage = object_storage
        self._parser = parser
        self._max_document_bytes = max_document_bytes
        self._chunk_size_characters = chunk_size_characters
        self._chunk_overlap_characters = chunk_overlap_characters

    def process(self, job_id: UUID, correlation_id: str) -> None:
        started_at = perf_counter()
        claimed = self._claim(job_id)
        if claimed is None:
            self._log(job_id, correlation_id, "ignored", started_at)
            return

        try:
            content = self._object_storage.read(
                claimed.storage_key,
                version_id=claimed.storage_version_id,
                max_bytes=self._max_document_bytes,
            )
            if len(content) != claimed.expected_size_bytes:
                raise DocumentProcessingFailure(
                    "DOCUMENT_SIZE_MISMATCH",
                    DocumentVersionState.REJECTED,
                )
            actual_sha256 = sha256(content).hexdigest()
            if actual_sha256 != claimed.expected_sha256:
                raise DocumentProcessingFailure(
                    "DOCUMENT_CHECKSUM_MISMATCH",
                    DocumentVersionState.REJECTED,
                )
            parsed = self._parser.parse(content)
            chunks = build_chunks(
                parsed,
                size_characters=self._chunk_size_characters,
                overlap_characters=self._chunk_overlap_characters,
            )
            self._persist_success(
                claimed,
                actual_sha256,
                len(content),
                parsed.pages,
                chunks,
                parsed.parser_version,
            )
        except DocumentProcessingFailure as error:
            self._persist_failure(claimed, error.code, error.state)
            self._log(job_id, correlation_id, "rejected", started_at, error.code)
            return
        except StoredObjectTooLarge:
            code = "DOCUMENT_TOO_LARGE"
            self._persist_failure(claimed, code, DocumentVersionState.REJECTED)
            self._log(job_id, correlation_id, "rejected", started_at, code)
            return
        except StoredObjectNotFound:
            code = "UPLOAD_NOT_FOUND"
            self._persist_failure(claimed, code, DocumentVersionState.FAILED)
            self._log(job_id, correlation_id, "failed", started_at, code)
            return
        except ObjectStorageUnavailable:
            code = "OBJECT_STORAGE_UNAVAILABLE"
            self._persist_failure(claimed, code, DocumentVersionState.FAILED)
            self._log(job_id, correlation_id, "failed", started_at, code)
            return
        except Exception:
            code = "DOCUMENT_PROCESSING_FAILED"
            self._persist_failure(claimed, code, DocumentVersionState.FAILED)
            self._log(job_id, correlation_id, "failed", started_at, code)
            return

        self._log(job_id, correlation_id, "completed", started_at)

    def _claim(self, job_id: UUID) -> ClaimedJob | None:
        with self._database.session() as session:
            claimed = session.execute(
                update(IngestionJob)
                .where(
                    IngestionJob.id == job_id,
                    IngestionJob.state == IngestionJobState.PENDING.value,
                )
                .values(
                    state=IngestionJobState.RUNNING.value,
                    attempt=IngestionJob.attempt + 1,
                    fencing_token=IngestionJob.fencing_token + 1,
                    updated_at=func.now(),
                )
                .returning(
                    IngestionJob.id,
                    IngestionJob.course_id,
                    IngestionJob.document_version_id,
                    IngestionJob.pipeline_version,
                    IngestionJob.fencing_token,
                )
            ).one_or_none()
            if claimed is None:
                return None
            version_update = session.execute(
                update(DocumentVersion)
                .where(
                    DocumentVersion.id == claimed.document_version_id,
                    DocumentVersion.course_id == claimed.course_id,
                    DocumentVersion.state == DocumentVersionState.QUEUED.value,
                )
                .values(state=DocumentVersionState.PROCESSING.value, updated_at=func.now())
            )
            if version_update.rowcount != 1:
                raise RuntimeError("document version could not enter PROCESSING")
            version = session.execute(
                select(
                    DocumentVersion.storage_key,
                    DocumentVersion.storage_version_id,
                    DocumentVersion.expected_size_bytes,
                    DocumentVersion.expected_sha256,
                ).where(DocumentVersion.id == claimed.document_version_id)
            ).one()
            if version.storage_version_id is None:
                raise RuntimeError("document version has no immutable storage version")
            return ClaimedJob(
                id=claimed.id,
                course_id=claimed.course_id,
                document_version_id=claimed.document_version_id,
                storage_key=version.storage_key,
                storage_version_id=version.storage_version_id,
                expected_size_bytes=version.expected_size_bytes,
                expected_sha256=version.expected_sha256,
                pipeline_version=claimed.pipeline_version,
                fencing_token=claimed.fencing_token,
            )

    def _persist_success(
        self,
        job: ClaimedJob,
        actual_sha256: str,
        size_bytes: int,
        pages: tuple,
        parsed_chunks: tuple,
        parser_version: str,
    ) -> None:
        with self._database.session() as session:
            active_job = session.scalar(
                select(IngestionJob)
                .where(
                    IngestionJob.id == job.id,
                    IngestionJob.state == IngestionJobState.RUNNING.value,
                    IngestionJob.fencing_token == job.fencing_token,
                )
                .with_for_update()
            )
            if active_job is None:
                raise RuntimeError("stale ingestion fencing token")

            corpus = CorpusVersion(
                id=uuid4(),
                course_id=job.course_id,
                state=CorpusVersionState.BUILDING.value,
                pipeline_version=job.pipeline_version,
                page_count=len(pages),
                chunk_count=len(parsed_chunks),
            )
            session.add(corpus)
            session.flush()
            session.add(
                CorpusVersionDocument(
                    corpus_version_id=corpus.id,
                    document_version_id=job.document_version_id,
                    course_id=job.course_id,
                )
            )
            session.flush()
            for parsed_chunk in parsed_chunks:
                session.add(
                    Chunk(
                        id=uuid4(),
                        course_id=job.course_id,
                        corpus_version_id=corpus.id,
                        document_version_id=job.document_version_id,
                        page_start=parsed_chunk.page,
                        page_end=parsed_chunk.page,
                        ordinal=parsed_chunk.ordinal,
                        content=parsed_chunk.content,
                        content_hash=parsed_chunk.content_hash,
                    )
                )
            session.flush()
            indexed_count = session.scalar(
                select(func.count(Chunk.id)).where(
                    Chunk.corpus_version_id == corpus.id,
                    Chunk.search_vector.is_not(None),
                )
            )
            if indexed_count != len(parsed_chunks):
                raise RuntimeError("corpus build verification failed")

            corpus.state = CorpusVersionState.READY.value
            corpus.ready_at = datetime.now(UTC)
            version_update = session.execute(
                update(DocumentVersion)
                .where(
                    DocumentVersion.id == job.document_version_id,
                    DocumentVersion.state == DocumentVersionState.PROCESSING.value,
                )
                .values(
                    state=DocumentVersionState.INDEXED.value,
                    object_size_bytes=size_bytes,
                    object_sha256=actual_sha256,
                    page_count=len(pages),
                    chunk_count=len(parsed_chunks),
                    parser_version=parser_version,
                    failure_code=None,
                    updated_at=func.now(),
                )
            )
            if version_update.rowcount != 1:
                raise RuntimeError("document version could not enter INDEXED")
            active_job.state = IngestionJobState.SUCCEEDED.value
            active_job.failure_code = None
            active_job.updated_at = datetime.now(UTC)
            active_job.completed_at = datetime.now(UTC)

    def _persist_failure(
        self,
        job: ClaimedJob,
        code: str,
        version_state: DocumentVersionState,
    ) -> None:
        with self._database.session() as session:
            job_update = session.execute(
                update(IngestionJob)
                .where(
                    IngestionJob.id == job.id,
                    IngestionJob.state == IngestionJobState.RUNNING.value,
                    IngestionJob.fencing_token == job.fencing_token,
                )
                .values(
                    state=IngestionJobState.FAILED.value,
                    failure_code=code,
                    updated_at=func.now(),
                    completed_at=func.now(),
                )
            )
            if job_update.rowcount != 1:
                return
            session.execute(
                update(DocumentVersion)
                .where(
                    DocumentVersion.id == job.document_version_id,
                    DocumentVersion.state == DocumentVersionState.PROCESSING.value,
                )
                .values(
                    state=version_state.value,
                    failure_code=code,
                    updated_at=func.now(),
                )
            )

    def _log(
        self,
        job_id: UUID,
        correlation_id: str,
        outcome: str,
        started_at: float,
        error_code: str | None = None,
    ) -> None:
        logger.info(
            json.dumps(
                {
                    "correlation_id": correlation_id,
                    "job_id": str(job_id),
                    "operation": "document.ingest",
                    "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                    "outcome": outcome,
                    "error_code": error_code,
                },
                separators=(",", ":"),
            )
        )


class InlineJobDispatcher:
    def __init__(self, worker: IngestionWorker) -> None:
        self._worker = worker

    def dispatch(self, job_id: UUID, correlation_id: str) -> None:
        self._worker.process(job_id, correlation_id)
