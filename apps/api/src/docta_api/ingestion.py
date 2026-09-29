import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from random import uniform
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError

from docta_api.database import Database
from docta_api.document_parser import (
    DocumentParser,
    DocumentProcessingFailure,
    build_chunks,
)
from docta_api.jobs import JobEnvelope, envelope_for, record_dead_letter, schedule_retry
from docta_api.models import (
    Chunk,
    CorpusVersion,
    CorpusVersionDocument,
    CorpusVersionState,
    DocumentVersion,
    DocumentVersionState,
    IngestionJob,
    IngestionJobState,
    OutboxEvent,
)
from docta_api.object_storage import (
    ObjectStorage,
    ObjectStorageUnavailable,
    StoredObjectNotFound,
    StoredObjectTooLarge,
)

logger = logging.getLogger("docta.ingestion")


class InvalidJobEnvelope(Exception):
    """Transport identifiers do not match the authoritative database scope."""


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
        lease_seconds: int = 300,
        max_attempts: int = 3,
        retry_base_seconds: float = 2,
    ) -> None:
        self._database = database
        self._object_storage = object_storage
        self._parser = parser
        self._max_document_bytes = max_document_bytes
        self._chunk_size_characters = chunk_size_characters
        self._chunk_overlap_characters = chunk_overlap_characters
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds

    def process(self, envelope: JobEnvelope) -> bool:
        job_id, correlation_id = envelope.job_id, envelope.correlation_id
        started_at = perf_counter()
        claimed = self._claim(envelope)
        if claimed is None:
            self._log(job_id, correlation_id, "ignored", started_at)
            return self._acknowledgeable(job_id)

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
            return self._acknowledgeable(job_id)
        except StoredObjectTooLarge:
            code = "DOCUMENT_TOO_LARGE"
            self._persist_failure(claimed, code, DocumentVersionState.REJECTED)
            self._log(job_id, correlation_id, "rejected", started_at, code)
            return self._acknowledgeable(job_id)
        except StoredObjectNotFound:
            code = "UPLOAD_NOT_FOUND"
            self._persist_failure(claimed, code, DocumentVersionState.FAILED)
            self._log(job_id, correlation_id, "failed", started_at, code)
            return self._acknowledgeable(job_id)
        except ObjectStorageUnavailable:
            code = "OBJECT_STORAGE_UNAVAILABLE"
            self._persist_failure(claimed, code, DocumentVersionState.FAILED, transient=True)
            self._log(job_id, correlation_id, "storage_unavailable", started_at, code)
            return self._acknowledgeable(job_id)
        except SQLAlchemyError:
            # A database outage cannot be acknowledged. Lease recovery resumes the job.
            raise
        except Exception:
            code = "DOCUMENT_PROCESSING_FAILED"
            self._persist_failure(claimed, code, DocumentVersionState.FAILED)
            self._log(job_id, correlation_id, "failed", started_at, code)
            return self._acknowledgeable(job_id)

        self._log(job_id, correlation_id, "completed", started_at)
        return self._acknowledgeable(job_id)

    def _acknowledgeable(self, job_id: UUID) -> bool:
        with self._database.session() as session:
            job = session.get(IngestionJob, job_id)
            return job is not None and job.state in {"SUCCEEDED", "FAILED", "PENDING"}

    def _claim(self, envelope: JobEnvelope) -> ClaimedJob | None:
        with self._database.session() as session:
            job = session.scalar(
                select(IngestionJob)
                .where(
                    IngestionJob.id == envelope.job_id,
                )
                .with_for_update()
            )
            event = session.get(OutboxEvent, envelope.event_id)
            if (
                job is None
                or event is None
                or event.kind != "ingestion"
                or event.job_id != job.id
                or envelope_for(event, job) != envelope
            ):
                raise InvalidJobEnvelope
            if job.state in {"SUCCEEDED", "FAILED"}:
                return None
            now = session.scalar(select(func.clock_timestamp()))
            if (job.state == "PENDING" and job.next_attempt_at > now) or (
                job.state == "RUNNING"
                and job.lease_expires_at is not None
                and job.lease_expires_at > now
            ):
                return None
            if job.attempt >= self._max_attempts:
                job.state = "FAILED"
                job.failure_code = "INGESTION_ATTEMPTS_EXHAUSTED"
                job.completed_at = job.updated_at = now
                job.lease_expires_at = None
                job.fencing_token += 1
                session.execute(
                    update(DocumentVersion)
                    .where(
                        DocumentVersion.id == job.document_version_id,
                        DocumentVersion.course_id == job.course_id,
                    )
                    .values(state="FAILED", failure_code=job.failure_code, updated_at=now)
                )
                record_dead_letter(session, job)
                return None
            job.state = "RUNNING"
            job.attempt += 1
            job.fencing_token += 1
            job.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            job.updated_at = now
            version_update = session.execute(
                update(DocumentVersion)
                .where(
                    DocumentVersion.id == job.document_version_id,
                    DocumentVersion.course_id == job.course_id,
                    DocumentVersion.state.in_(["QUEUED", "PROCESSING"]),
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
                ).where(DocumentVersion.id == job.document_version_id)
            ).one()
            if version.storage_version_id is None:
                raise RuntimeError("document version has no immutable storage version")
            return ClaimedJob(
                id=job.id,
                course_id=job.course_id,
                document_version_id=job.document_version_id,
                storage_key=version.storage_key,
                storage_version_id=version.storage_version_id,
                expected_size_bytes=version.expected_size_bytes,
                expected_sha256=version.expected_sha256,
                pipeline_version=job.pipeline_version,
                fencing_token=job.fencing_token,
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
                    IngestionJob.lease_expires_at > func.clock_timestamp(),
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
                    DocumentVersion.course_id == job.course_id,
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
            active_job.lease_expires_at = None

    def _persist_failure(
        self,
        job: ClaimedJob,
        code: str,
        version_state: DocumentVersionState,
        transient: bool = False,
    ) -> None:
        with self._database.session() as session:
            active_job = session.scalar(
                select(IngestionJob)
                .where(
                    IngestionJob.id == job.id,
                    IngestionJob.state == IngestionJobState.RUNNING.value,
                    IngestionJob.fencing_token == job.fencing_token,
                    IngestionJob.lease_expires_at > func.clock_timestamp(),
                )
                .with_for_update()
            )
            if active_job is None:
                return
            now = session.scalar(select(func.clock_timestamp()))
            retry = transient and active_job.attempt < self._max_attempts
            active_job.state = "PENDING" if retry else "FAILED"
            active_job.failure_code = code
            active_job.updated_at = now
            active_job.lease_expires_at = None
            if retry:
                delay = min(60, self._retry_base_seconds * 2 ** (active_job.attempt - 1))
                active_job.next_attempt_at = now + timedelta(seconds=uniform(delay / 2, delay))
                schedule_retry(session, active_job, active_job.next_attempt_at)
            else:
                active_job.completed_at = now
                record_dead_letter(session, active_job)
            session.execute(
                update(DocumentVersion)
                .where(
                    DocumentVersion.id == job.document_version_id,
                    DocumentVersion.course_id == job.course_id,
                    DocumentVersion.state == DocumentVersionState.PROCESSING.value,
                )
                .values(
                    state="QUEUED" if retry else version_state.value,
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
        with self._database.session() as session:
            job = session.get(IngestionJob, job_id)
            scope = (
                {}
                if job is None
                else {
                    "course_id": str(job.course_id),
                    "document_version_id": str(job.document_version_id),
                }
            )
        logger.info(
            json.dumps(
                {
                    "correlation_id": correlation_id,
                    "job_id": str(job_id),
                    "operation": "document.ingest",
                    "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                    "outcome": outcome,
                    "error_code": error_code,
                    **scope,
                },
                separators=(",", ":"),
            )
        )
