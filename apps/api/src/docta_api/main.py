from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from docta_api.api_errors import APIError
from docta_api.config import Settings, get_settings
from docta_api.conversation_routes import router as conversations_router
from docta_api.conversation_runtime import ConversationRuntime
from docta_api.conversations import ConversationService
from docta_api.courses import CourseService
from docta_api.courses import router as courses_router
from docta_api.database import Database
from docta_api.documents import DocumentService
from docta_api.documents import router as documents_router
from docta_api.health import MinioProbe, PostgresProbe, ReadinessProbes
from docta_api.identity import (
    IdentityProvider,
    OIDCJWTIdentityProvider,
    UnconfiguredIdentityProvider,
)
from docta_api.jobs import JobDispatcher, OutboxJobDispatcher
from docta_api.object_storage import ObjectStorage, UnconfiguredObjectStorage
from docta_api.postgres_retriever import PostgresRetriever
from docta_api.rag import EvidenceResponseValidator, Retriever, TutorModel
from docta_api.s3_object_storage import S3ObjectStorage
from docta_api.tutor_http import ChatCompletionsTutorModel, UnconfiguredTutorModel
from docta_api.workspace import WorkspaceService
from docta_api.workspace import router as workspace_router


class LiveResponse(BaseModel):
    status: str
    service: str


class ReadyResponse(BaseModel):
    status: str
    checks: dict[str, str]


class NotReadyResponse(BaseModel):
    code: str
    message: str
    checks: dict[str, str]


def _default_probes(settings: Settings) -> ReadinessProbes:
    return ReadinessProbes(
        postgres=PostgresProbe(
            database_url=str(settings.database_url),
            timeout_seconds=settings.dependency_timeout_seconds,
        ),
        object_storage=MinioProbe(
            health_url=str(settings.minio_health_url),
            timeout_seconds=settings.dependency_timeout_seconds,
        ),
    )


def _default_identity_provider(settings: Settings) -> IdentityProvider:
    if not settings.oidc_configured:
        return UnconfiguredIdentityProvider()
    assert settings.oidc_issuer is not None
    assert settings.oidc_audience is not None
    assert settings.oidc_jwks_url is not None
    return OIDCJWTIdentityProvider(
        issuer=str(settings.oidc_issuer),
        audience=settings.oidc_audience,
        jwks_url=str(settings.oidc_jwks_url),
        timeout_seconds=settings.dependency_timeout_seconds,
    )


def _default_object_storage(settings: Settings) -> ObjectStorage:
    if not settings.object_storage_configured:
        return UnconfiguredObjectStorage()
    assert settings.s3_endpoint_url is not None
    assert settings.s3_access_key is not None
    assert settings.s3_secret_key is not None
    return S3ObjectStorage(
        endpoint_url=str(settings.s3_endpoint_url),
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key.get_secret_value(),
        bucket=settings.s3_bucket,
        region=settings.s3_region,
        upload_ttl_seconds=settings.upload_ttl_seconds,
        timeout_seconds=settings.dependency_timeout_seconds,
    )


def _default_tutor_model(settings: Settings) -> TutorModel:
    if settings.tutor_endpoint_url is None:
        return UnconfiguredTutorModel()
    assert settings.tutor_model is not None and settings.tutor_api_key is not None
    return ChatCompletionsTutorModel(
        endpoint=str(settings.tutor_endpoint_url), model=settings.tutor_model,
        api_key=settings.tutor_api_key.get_secret_value(),
        timeout_seconds=settings.tutor_timeout_seconds,
        max_output_tokens=settings.tutor_max_output_tokens,
        schema_profile=settings.tutor_schema_profile,
        provider=settings.tutor_provider,
    )


def create_app(
    settings: Settings | None = None,
    probes: ReadinessProbes | None = None,
    identity_provider: IdentityProvider | None = None,
    course_service: CourseService | None = None,
    document_service: DocumentService | None = None,
    object_storage: ObjectStorage | None = None,
    job_dispatcher: JobDispatcher | None = None,
    conversation_service: ConversationService | None = None,
    tutor_model: TutorModel | None = None,
    retriever: Retriever | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    database: Database | None = None
    if course_service is None or document_service is None or conversation_service is None:
        database = Database(str(resolved_settings.database_url))
    if course_service is None:
        assert database is not None
        resolved_course_service = CourseService(database)
    else:
        resolved_course_service = course_service
    if document_service is None:
        assert database is not None
        resolved_object_storage = object_storage or _default_object_storage(resolved_settings)
        dispatcher = job_dispatcher or OutboxJobDispatcher()
        resolved_document_service = DocumentService(
            database=database,
            object_storage=resolved_object_storage,
            job_dispatcher=dispatcher,
            environment=resolved_settings.environment,
            max_document_bytes=resolved_settings.max_document_bytes,
            pipeline_version=resolved_settings.pipeline_version,
        )
    else:
        resolved_document_service = document_service

    resolved_model = tutor_model or _default_tutor_model(resolved_settings)
    if conversation_service is None:
        assert database is not None
        conversation_service = ConversationService(
            database, model_version=resolved_model.version,
            message_timeout_seconds=resolved_settings.message_timeout_seconds,
        )
    conversation_runtime = ConversationRuntime(
        conversation_service, retriever or PostgresRetriever(conversation_service.database),
        resolved_model, EvidenceResponseValidator(),
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.settings = resolved_settings
        application.state.readiness_probes = probes or _default_probes(resolved_settings)
        application.state.identity_provider = identity_provider or _default_identity_provider(
            resolved_settings
        )
        application.state.course_service = resolved_course_service
        application.state.document_service = resolved_document_service
        application.state.conversation_runtime = conversation_runtime
        application.state.workspace_service = WorkspaceService(conversation_service.database)
        conversation_runtime.start_recovery()
        try:
            yield
        finally:
            await conversation_runtime.close()
            if database is not None:
                database.close()

    application = FastAPI(title="Docta API", version="0.1.0", lifespan=lifespan)
    application.include_router(courses_router)
    application.include_router(documents_router)
    application.include_router(conversations_router)
    application.include_router(workspace_router)

    @application.exception_handler(APIError)
    async def api_error_handler(request: Request, error: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "code": error.code,
                "message": error.message,
                "request_id": request.state.request_id,
            },
            headers=error.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_request",
                "message": "The request did not match the expected contract.",
                "request_id": request.state.request_id,
            },
        )

    @application.middleware("http")
    async def correlation_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Any]],
    ):
        supplied_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_id
            if (
                0 < len(supplied_id) <= 100
                and all(
                    character.isascii() and (character.isalnum() or character in "-_.")
                    for character in supplied_id
                )
            )
            else str(uuid4())
        )
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    @application.get("/api/v1/health/live", response_model=LiveResponse)
    async def live() -> LiveResponse:
        return LiveResponse(status="ok", service="docta-api")

    @application.get(
        "/api/v1/health/ready",
        response_model=ReadyResponse,
        responses={503: {"model": NotReadyResponse}},
    )
    async def ready(request: Request) -> ReadyResponse | JSONResponse:
        active_probes: ReadinessProbes = request.app.state.readiness_probes
        checks = await active_probes.check_all()
        if any(status != "ok" for status in checks.values()):
            payload = NotReadyResponse(
                code="service_not_ready",
                message="One or more required dependencies are unavailable.",
                checks=checks,
            )
            return JSONResponse(status_code=503, content=payload.model_dump())
        return ReadyResponse(status="ready", checks=checks)

    return application


app = create_app()
