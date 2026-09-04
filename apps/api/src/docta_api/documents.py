import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter
from typing import Annotated, Literal, NoReturn
from uuid import UUID, uuid4

from anyio import to_thread
from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError

from docta_api.api_errors import APIError
from docta_api.courses import RequiredIdentity
from docta_api.database import Database
from docta_api.identity import AuthenticatedIdentity
from docta_api.ingestion import JobDispatcher
from docta_api.models import (
    CorpusVersion,
    CorpusVersionDocument,
    CorpusVersionState,
    Course,
    CourseMembership,
    Document,
    DocumentVersion,
    DocumentVersionState,
    IngestionJob,
    IngestionJobState,
    MembershipRole,
    User,
)
from docta_api.object_storage import (
    ObjectStorage,
    ObjectStorageUnavailable,
    StoredObjectMetadata,
    StoredObjectNotFound,
    UploadAuthorization,
)

logger = logging.getLogger("docta.document")
router = APIRouter(prefix="/api/v1/courses", tags=["documents"])
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


class UploadCreateRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(min_length=64, max_length=64)
    content_type: Literal["application/pdf"] = "application/pdf"

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.lower()
        if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
            raise ValueError("sha256 must be 64 hexadecimal characters")
        return normalized


class UploadResponse(BaseModel):
    document_id: UUID
    version_id: UUID
    state: DocumentVersionState
    upload_url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]
    expires_at: datetime


class DocumentVersionResponse(BaseModel):
    document_id: UUID
    version_id: UUID
    state: DocumentVersionState
    failure_code: str | None
    page_count: int | None
    chunk_count: int | None
    corpus_version_id: UUID | None
    job_id: UUID | None
    job_state: IngestionJobState | None


class CorpusActivateRequest(BaseModel):
    corpus_version_id: UUID
    expected_course_version: int = Field(ge=0)


class CorpusActivationResponse(BaseModel):
    course_id: UUID
    active_corpus_version_id: UUID
    course_version: int


@dataclass(frozen=True)
class UploadRecord:
    document_id: UUID
    version_id: UUID
    storage_key: str
    state: DocumentVersionState


@dataclass(frozen=True)
class UploadResult:
    record: UploadRecord
    authorization: UploadAuthorization


@dataclass(frozen=True)
class VersionView:
    document_id: UUID
    version_id: UUID
    state: DocumentVersionState
    failure_code: str | None
    page_count: int | None
    chunk_count: int | None
    corpus_version_id: UUID | None
    job_id: UUID | None
    job_state: IngestionJobState | None


@dataclass(frozen=True)
class ActivationView:
    course_id: UUID
    active_corpus_version_id: UUID
    course_version: int


class DocumentResourceNotFound(Exception):
    pass


class IdempotencyKeyMissing(Exception):
    pass


class IdempotencyKeyReused(Exception):
    pass


class UploadNotReady(Exception):
    pass


class UploadValidationFailure(Exception):
    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class CorpusNotReady(Exception):
    pass


class PublicationConflict(Exception):
    pass


class DocumentService:
    def __init__(
        self,
        *,
        database: Database,
        object_storage: ObjectStorage,
        job_dispatcher: JobDispatcher,
        environment: str,
        max_document_bytes: int,
        pipeline_version: str,
    ) -> None:
        self._database = database
        self._object_storage = object_storage
        self._job_dispatcher = job_dispatcher
        self._environment = environment
        self._max_document_bytes = max_document_bytes
        self._pipeline_version = pipeline_version

    def create_upload(
        self,
        *,
        identity: AuthenticatedIdentity,
        course_id: UUID,
        filename: str,
        size_bytes: int,
        expected_sha256: str,
        media_type: str,
        idempotency_key: str | None,
    ) -> UploadResult:
        key = _validated_idempotency_key(idempotency_key)
        if size_bytes > self._max_document_bytes:
            raise UploadValidationFailure("DOCUMENT_TOO_LARGE", status.HTTP_413_CONTENT_TOO_LARGE)
        safe_filename = _sanitize_filename(filename)
        request_hash = _request_hash(
            {
                "filename": safe_filename,
                "size_bytes": size_bytes,
                "sha256": expected_sha256,
                "content_type": media_type,
            }
        )

        with self._database.session() as session:
            user_id = _authorized_teacher_user_id(session, identity, course_id)
            existing = session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.course_id == course_id,
                    DocumentVersion.upload_idempotency_key == key,
                )
            )
            if existing is not None:
                if existing.upload_request_hash != request_hash:
                    raise IdempotencyKeyReused
                if existing.state != DocumentVersionState.AWAITING_UPLOAD.value:
                    raise UploadNotReady
                record = UploadRecord(
                    document_id=existing.document_id,
                    version_id=existing.id,
                    storage_key=existing.storage_key,
                    state=DocumentVersionState(existing.state),
                )
            else:
                document_id = uuid4()
                version_id = uuid4()
                storage_key = (
                    f"{self._environment}/courses/{course_id}/documents/{document_id}/"
                    f"versions/{version_id}/source.pdf"
                )
                document = Document(
                    id=document_id,
                    course_id=course_id,
                    title=_document_title(safe_filename),
                    created_by_user_id=user_id,
                )
                version = DocumentVersion(
                    id=version_id,
                    course_id=course_id,
                    document_id=document_id,
                    storage_key=storage_key,
                    original_filename=safe_filename,
                    expected_size_bytes=size_bytes,
                    expected_sha256=expected_sha256,
                    media_type=media_type,
                    state=DocumentVersionState.AWAITING_UPLOAD.value,
                    pipeline_version=self._pipeline_version,
                    upload_idempotency_key=key,
                    upload_request_hash=request_hash,
                )
                session.add(document)
                session.add(version)
                record = UploadRecord(
                    document_id=document_id,
                    version_id=version_id,
                    storage_key=storage_key,
                    state=DocumentVersionState.AWAITING_UPLOAD,
                )

        authorization = self._object_storage.create_upload_authorization(
            storage_key=record.storage_key,
            media_type=media_type,
            sha256=expected_sha256,
        )
        return UploadResult(record=record, authorization=authorization)

    def confirm_upload(
        self,
        *,
        identity: AuthenticatedIdentity,
        course_id: UUID,
        document_id: UUID,
        version_id: UUID,
        idempotency_key: str | None,
        correlation_id: str,
    ) -> VersionView:
        key = _validated_idempotency_key(idempotency_key)
        request_hash = _request_hash(
            {
                "course_id": str(course_id),
                "document_id": str(document_id),
                "version_id": str(version_id),
            }
        )
        existing_job = self._existing_confirmation(
            identity=identity,
            course_id=course_id,
            document_id=document_id,
            version_id=version_id,
            idempotency_key=key,
            request_hash=request_hash,
        )
        if existing_job is not None:
            if existing_job[1] == IngestionJobState.PENDING:
                self._job_dispatcher.dispatch(existing_job[0], correlation_id)
            return self.get_version(identity, course_id, document_id, version_id)

        record = self._load_upload(identity, course_id, document_id, version_id)
        metadata = self._object_storage.inspect(record.storage_key)
        self._validate_stored_object(metadata, record)

        with self._database.session() as session:
            _authorized_teacher_user_id(session, identity, course_id)
            version = session.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.course_id == course_id,
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.id == version_id,
                )
                .with_for_update()
            )
            if version is None:
                raise DocumentResourceNotFound
            if version.confirmation_idempotency_key is not None:
                if (
                    version.confirmation_idempotency_key != key
                    or version.confirmation_request_hash != request_hash
                ):
                    raise IdempotencyKeyReused
                job = session.scalar(
                    select(IngestionJob).where(IngestionJob.document_version_id == version.id)
                )
                if job is None:
                    raise RuntimeError("confirmed document has no ingestion job")
                job_id = job.id
            else:
                if version.state != DocumentVersionState.AWAITING_UPLOAD.value:
                    raise UploadNotReady
                job_id = uuid4()
                version.confirmation_idempotency_key = key
                version.confirmation_request_hash = request_hash
                version.object_size_bytes = metadata.size_bytes
                version.storage_version_id = metadata.version_id
                version.state = DocumentVersionState.QUEUED.value
                version.updated_at = datetime.now(UTC)
                session.add(
                    IngestionJob(
                        id=job_id,
                        course_id=course_id,
                        document_version_id=version.id,
                        job_type="ingestion",
                        pipeline_version=version.pipeline_version,
                        state=IngestionJobState.PENDING.value,
                        correlation_id=correlation_id,
                    )
                )

        self._job_dispatcher.dispatch(job_id, correlation_id)
        return self.get_version(identity, course_id, document_id, version_id)

    def get_version(
        self,
        identity: AuthenticatedIdentity,
        course_id: UUID,
        document_id: UUID,
        version_id: UUID,
    ) -> VersionView:
        with self._database.session() as session:
            _authorized_teacher_user_id(session, identity, course_id)
            result = session.execute(
                select(
                    DocumentVersion,
                    CorpusVersionDocument.corpus_version_id,
                    IngestionJob.id,
                    IngestionJob.state,
                )
                .outerjoin(
                    CorpusVersionDocument,
                    CorpusVersionDocument.document_version_id == DocumentVersion.id,
                )
                .outerjoin(
                    IngestionJob,
                    IngestionJob.document_version_id == DocumentVersion.id,
                )
                .where(
                    DocumentVersion.course_id == course_id,
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.id == version_id,
                )
            ).one_or_none()
            if result is None:
                raise DocumentResourceNotFound
            version, corpus_version_id, job_id, job_state = result
            return VersionView(
                document_id=version.document_id,
                version_id=version.id,
                state=DocumentVersionState(version.state),
                failure_code=version.failure_code,
                page_count=version.page_count,
                chunk_count=version.chunk_count,
                corpus_version_id=corpus_version_id,
                job_id=job_id,
                job_state=IngestionJobState(job_state) if job_state is not None else None,
            )

    def activate_corpus(
        self,
        *,
        identity: AuthenticatedIdentity,
        course_id: UUID,
        corpus_version_id: UUID,
        expected_course_version: int,
    ) -> ActivationView:
        with self._database.session() as session:
            _authorized_teacher_user_id(session, identity, course_id)
            corpus = session.scalar(
                select(CorpusVersion).where(
                    CorpusVersion.id == corpus_version_id,
                    CorpusVersion.course_id == course_id,
                )
            )
            if corpus is None:
                raise DocumentResourceNotFound
            if corpus.state != CorpusVersionState.READY.value:
                raise CorpusNotReady
            updated_version = session.scalar(
                update(Course)
                .where(Course.id == course_id, Course.version == expected_course_version)
                .values(
                    active_corpus_version_id=corpus_version_id,
                    version=Course.version + 1,
                    updated_at=func.now(),
                )
                .returning(Course.version)
            )
            if updated_version is None:
                raise PublicationConflict
            return ActivationView(
                course_id=course_id,
                active_corpus_version_id=corpus_version_id,
                course_version=updated_version,
            )

    def _existing_confirmation(
        self,
        *,
        identity: AuthenticatedIdentity,
        course_id: UUID,
        document_id: UUID,
        version_id: UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[UUID, IngestionJobState] | None:
        with self._database.session() as session:
            _authorized_teacher_user_id(session, identity, course_id)
            result = session.execute(
                select(
                    DocumentVersion.confirmation_idempotency_key,
                    DocumentVersion.confirmation_request_hash,
                    IngestionJob.id,
                    IngestionJob.state,
                )
                .outerjoin(
                    IngestionJob,
                    IngestionJob.document_version_id == DocumentVersion.id,
                )
                .where(
                    DocumentVersion.course_id == course_id,
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.id == version_id,
                )
            ).one_or_none()
            if result is None:
                raise DocumentResourceNotFound
            if result.confirmation_idempotency_key is None:
                return None
            if (
                result.confirmation_idempotency_key != idempotency_key
                or result.confirmation_request_hash != request_hash
            ):
                raise IdempotencyKeyReused
            if result.id is None or result.state is None:
                raise RuntimeError("confirmed document has no ingestion job")
            return result.id, IngestionJobState(result.state)

    def _load_upload(
        self,
        identity: AuthenticatedIdentity,
        course_id: UUID,
        document_id: UUID,
        version_id: UUID,
    ) -> DocumentVersion:
        with self._database.session() as session:
            _authorized_teacher_user_id(session, identity, course_id)
            version = session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.course_id == course_id,
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.id == version_id,
                )
            )
            if version is None:
                raise DocumentResourceNotFound
            session.expunge(version)
            return version

    def _validate_stored_object(
        self,
        metadata: StoredObjectMetadata,
        record: DocumentVersion,
    ) -> None:
        if metadata.size_bytes > self._max_document_bytes:
            raise UploadValidationFailure("DOCUMENT_TOO_LARGE", status.HTTP_413_CONTENT_TOO_LARGE)
        if metadata.size_bytes != record.expected_size_bytes:
            raise UploadValidationFailure("DOCUMENT_SIZE_MISMATCH")
        if (metadata.media_type or "").split(";", 1)[0].strip().lower() != "application/pdf":
            raise UploadValidationFailure("DOCUMENT_INVALID_CONTENT_TYPE")
        if metadata.metadata.get("sha256", "").lower() != record.expected_sha256:
            raise UploadValidationFailure("DOCUMENT_CHECKSUM_METADATA_MISMATCH")
        if metadata.version_id is None:
            raise UploadValidationFailure("OBJECT_STORAGE_VERSIONING_REQUIRED", 503)


@router.post(
    "/{course_id}/documents/uploads",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_upload(
    course_id: UUID,
    payload: UploadCreateRequest,
    request: Request,
    identity: RequiredIdentity,
    idempotency_key: IdempotencyKey,
) -> UploadResponse:
    started_at = perf_counter()
    service: DocumentService = request.app.state.document_service
    try:
        result = await to_thread.run_sync(
            lambda: service.create_upload(
                identity=identity,
                course_id=course_id,
                filename=payload.filename,
                size_bytes=payload.size_bytes,
                expected_sha256=payload.sha256,
                media_type=payload.content_type,
                idempotency_key=idempotency_key,
            )
        )
    except Exception as error:
        _raise_document_api_error(error)
    _log(request, "document.upload.create", "completed", started_at, course_id)
    return UploadResponse(
        document_id=result.record.document_id,
        version_id=result.record.version_id,
        state=result.record.state,
        upload_url=result.authorization.url,
        headers=result.authorization.headers,
        expires_at=result.authorization.expires_at,
    )


@router.post(
    "/{course_id}/documents/{document_id}/versions/{version_id}/complete",
    response_model=DocumentVersionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def complete_document_upload(
    course_id: UUID,
    document_id: UUID,
    version_id: UUID,
    request: Request,
    identity: RequiredIdentity,
    idempotency_key: IdempotencyKey,
) -> DocumentVersionResponse:
    started_at = perf_counter()
    service: DocumentService = request.app.state.document_service
    try:
        result = await to_thread.run_sync(
            lambda: service.confirm_upload(
                identity=identity,
                course_id=course_id,
                document_id=document_id,
                version_id=version_id,
                idempotency_key=idempotency_key,
                correlation_id=request.state.request_id,
            )
        )
    except Exception as error:
        _raise_document_api_error(error)
    _log(request, "document.upload.complete", "accepted", started_at, course_id, version_id)
    return DocumentVersionResponse.model_validate(result, from_attributes=True)


@router.get(
    "/{course_id}/documents/{document_id}/versions/{version_id}",
    response_model=DocumentVersionResponse,
)
async def get_document_version(
    course_id: UUID,
    document_id: UUID,
    version_id: UUID,
    request: Request,
    identity: RequiredIdentity,
) -> DocumentVersionResponse:
    started_at = perf_counter()
    service: DocumentService = request.app.state.document_service
    try:
        result = await to_thread.run_sync(
            service.get_version,
            identity,
            course_id,
            document_id,
            version_id,
        )
    except Exception as error:
        _raise_document_api_error(error)
    _log(request, "document.version.read", "completed", started_at, course_id, version_id)
    return DocumentVersionResponse.model_validate(result, from_attributes=True)


@router.post("/{course_id}/corpus/activate", response_model=CorpusActivationResponse)
async def activate_corpus(
    course_id: UUID,
    payload: CorpusActivateRequest,
    request: Request,
    identity: RequiredIdentity,
) -> CorpusActivationResponse:
    started_at = perf_counter()
    service: DocumentService = request.app.state.document_service
    try:
        result = await to_thread.run_sync(
            lambda: service.activate_corpus(
                identity=identity,
                course_id=course_id,
                corpus_version_id=payload.corpus_version_id,
                expected_course_version=payload.expected_course_version,
            )
        )
    except Exception as error:
        _raise_document_api_error(error)
    _log(request, "corpus.activate", "completed", started_at, course_id)
    return CorpusActivationResponse.model_validate(result, from_attributes=True)


def _authorized_teacher_user_id(session, identity: AuthenticatedIdentity, course_id: UUID) -> UUID:
    user_id = session.scalar(
        select(User.id)
        .join(CourseMembership, CourseMembership.user_id == User.id)
        .where(
            CourseMembership.course_id == course_id,
            CourseMembership.role == MembershipRole.TEACHER.value,
            User.oidc_issuer == identity.issuer,
            User.oidc_subject == identity.subject,
        )
    )
    if user_id is None:
        raise DocumentResourceNotFound
    return user_id


def _validated_idempotency_key(value: str | None) -> str:
    if value is None or not value.strip():
        raise IdempotencyKeyMissing
    normalized = value.strip()
    if len(normalized) > 255:
        raise IdempotencyKeyMissing
    return normalized


def _request_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _sanitize_filename(filename: str) -> str:
    safe = filename.replace("\\", "/").rsplit("/", 1)[-1]
    safe = "".join(character for character in safe if character.isprintable()).strip()
    if not safe or len(safe) > 255 or not safe.lower().endswith(".pdf"):
        raise UploadValidationFailure("DOCUMENT_INVALID_FILENAME")
    return safe


def _document_title(filename: str) -> str:
    title = filename[:-4].strip()
    return title or "Untitled document"


def _raise_document_api_error(error: Exception) -> NoReturn:
    if isinstance(error, APIError):
        raise error
    if isinstance(error, IdempotencyKeyMissing):
        raise APIError(400, "idempotency_key_required", "An Idempotency-Key is required.")
    if isinstance(error, IdempotencyKeyReused):
        raise APIError(
            409,
            "idempotency_key_reused",
            "The Idempotency-Key was already used for a different request.",
        )
    if isinstance(error, (DocumentResourceNotFound, StoredObjectNotFound)):
        raise APIError(404, "resource_not_found", "The requested resource was not found.")
    if isinstance(error, UploadNotReady):
        raise APIError(
            409,
            "upload_not_ready",
            "The upload cannot be changed in its current state.",
        )
    if isinstance(error, UploadValidationFailure):
        raise APIError(error.status_code, error.code.lower(), "The uploaded document is invalid.")
    if isinstance(error, CorpusNotReady):
        raise APIError(409, "corpus_not_ready", "Only a ready corpus can be activated.")
    if isinstance(error, PublicationConflict):
        raise APIError(409, "publication_conflict", "The course changed before publication.")
    if isinstance(error, ObjectStorageUnavailable):
        raise APIError(503, "object_storage_unavailable", "Object storage is unavailable.")
    if isinstance(error, SQLAlchemyError):
        raise APIError(503, "persistence_unavailable", "Persistence is unavailable.")
    raise error


def _log(
    request: Request,
    operation: str,
    outcome: str,
    started_at: float,
    course_id: UUID,
    document_version_id: UUID | None = None,
) -> None:
    logger.info(
        json.dumps(
            {
                "correlation_id": request.state.request_id,
                "course_id": str(course_id),
                "document_version_id": (
                    str(document_version_id) if document_version_id is not None else None
                ),
                "operation": operation,
                "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                "outcome": outcome,
            },
            separators=(",", ":"),
        )
    )
