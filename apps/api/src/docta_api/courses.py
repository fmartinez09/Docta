import json
import logging
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Annotated
from uuid import UUID, uuid4

from anyio import to_thread
from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from docta_api.api_errors import APIError
from docta_api.database import Database
from docta_api.identity import (
    AuthenticatedIdentity,
    AuthenticationError,
    IdentityProvider,
    IdentityProviderUnavailable,
)
from docta_api.models import Course, CourseCreation, CourseMembership, MembershipRole, User

logger = logging.getLogger("docta.course")
router = APIRouter(prefix="/api/v1/courses", tags=["courses"])


class CourseCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, title: str) -> str:
        normalized = title.strip()
        if not normalized:
            raise ValueError("title must not be blank")
        return normalized


class CourseResponse(BaseModel):
    id: UUID
    title: str
    role: MembershipRole
    version: int
    active_corpus_version_id: UUID | None
    created_at: datetime


@dataclass(frozen=True)
class CourseView:
    id: UUID
    title: str
    role: MembershipRole
    version: int
    active_corpus_version_id: UUID | None
    created_at: datetime


class CourseNotFound(Exception):
    """No course visible to the authenticated identity exists for this identifier."""


class CourseService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create_course(
        self, identity: AuthenticatedIdentity, title: str, idempotency_key: str | None = None
    ) -> CourseView:
        if idempotency_key is not None and not (0 < len(idempotency_key.strip()) <= 255):
            raise APIError(400, "idempotency_key_required", "A valid Idempotency-Key is required.")
        with self._database.session() as session:
            proposed_user_id = uuid4()
            inserted_user_id = session.scalar(
                insert(User)
                .values(
                    id=proposed_user_id,
                    oidc_issuer=identity.issuer,
                    oidc_subject=identity.subject,
                )
                .on_conflict_do_nothing(constraint="uq_users_oidc_identity")
                .returning(User.id)
            )
            user_id = inserted_user_id or session.scalar(
                select(User.id).where(
                    User.oidc_issuer == identity.issuer,
                    User.oidc_subject == identity.subject,
                )
            )
            if user_id is None:
                raise RuntimeError("identity persistence invariant failed")

            if idempotency_key is not None:
                idempotency_key = idempotency_key.strip()
                session.scalar(select(User.id).where(User.id == user_id).with_for_update())
                previous = session.scalar(
                    select(Course)
                    .join(
                        CourseCreation,
                        CourseCreation.course_id == Course.id,
                    )
                    .where(
                        CourseCreation.user_id == user_id,
                        CourseCreation.idempotency_key == idempotency_key,
                    )
                )
                if previous is not None:
                    role = session.scalar(
                        select(CourseMembership.role).where(
                            CourseMembership.course_id == previous.id,
                            CourseMembership.user_id == user_id,
                        )
                    )
                    if role != MembershipRole.TEACHER:
                        raise APIError(404, "course_not_found", "The course was not found.")
                    if previous.title != title:
                        raise APIError(
                            409, "idempotency_key_reused", "The key belongs to another request."
                        )
                    return CourseView(
                        previous.id,
                        previous.title,
                        MembershipRole.TEACHER,
                        previous.version,
                        previous.active_corpus_version_id,
                        previous.created_at,
                    )

            course = Course(id=uuid4(), title=title)
            session.add(course)
            session.add(
                CourseMembership(
                    course_id=course.id,
                    user_id=user_id,
                    role=MembershipRole.TEACHER.value,
                )
            )
            session.flush()
            if idempotency_key is not None:
                session.add(
                    CourseCreation(
                        user_id=user_id, idempotency_key=idempotency_key, course_id=course.id
                    )
                )
            return CourseView(
                id=course.id,
                title=course.title,
                role=MembershipRole.TEACHER,
                version=course.version,
                active_corpus_version_id=course.active_corpus_version_id,
                created_at=course.created_at,
            )

    def get_course(self, identity: AuthenticatedIdentity, course_id: UUID) -> CourseView:
        with self._database.session() as session:
            result = session.execute(
                select(Course, CourseMembership.role)
                .join(CourseMembership, CourseMembership.course_id == Course.id)
                .join(User, User.id == CourseMembership.user_id)
                .where(
                    Course.id == course_id,
                    User.oidc_issuer == identity.issuer,
                    User.oidc_subject == identity.subject,
                )
            ).one_or_none()
            if result is None:
                raise CourseNotFound
            course, role = result
            return CourseView(
                id=course.id,
                title=course.title,
                role=MembershipRole(role),
                version=course.version,
                active_corpus_version_id=course.active_corpus_version_id,
                created_at=course.created_at,
            )


async def require_identity(request: Request) -> AuthenticatedIdentity:
    provider: IdentityProvider = request.app.state.identity_provider
    try:
        return await provider.authenticate(request.headers.get("authorization"))
    except AuthenticationError as error:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="authentication_required",
            message="A valid bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    except IdentityProviderUnavailable as error:
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="identity_provider_unavailable",
            message="Authentication is temporarily unavailable.",
        ) from error


RequiredIdentity = Annotated[AuthenticatedIdentity, Depends(require_identity)]


@router.post("", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
async def create_course(
    payload: CourseCreateRequest,
    request: Request,
    identity: RequiredIdentity,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CourseResponse:
    started_at = perf_counter()
    service: CourseService = request.app.state.course_service
    try:
        course = await to_thread.run_sync(
            service.create_course, identity, payload.title, idempotency_key
        )
    except SQLAlchemyError as error:
        _log(request, "course.create", "failed", started_at)
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="persistence_unavailable",
            message="The course could not be created at this time.",
        ) from error
    _log(request, "course.create", "completed", started_at, course.id)
    return CourseResponse.model_validate(course, from_attributes=True)


@router.get("/{course_id}", response_model=CourseResponse)
async def get_course(
    course_id: UUID,
    request: Request,
    identity: RequiredIdentity,
) -> CourseResponse:
    started_at = perf_counter()
    service: CourseService = request.app.state.course_service
    try:
        course = await to_thread.run_sync(service.get_course, identity, course_id)
    except CourseNotFound as error:
        _log(request, "course.read", "denied", started_at, course_id)
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="course_not_found",
            message="The course was not found.",
        ) from error
    except SQLAlchemyError as error:
        _log(request, "course.read", "failed", started_at, course_id)
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="persistence_unavailable",
            message="The course could not be read at this time.",
        ) from error
    _log(request, "course.read", "completed", started_at, course.id)
    return CourseResponse.model_validate(course, from_attributes=True)


def _log(
    request: Request,
    operation: str,
    outcome: str,
    started_at: float,
    course_id: UUID | None = None,
) -> None:
    event = {
        "correlation_id": request.state.request_id,
        "course_id": str(course_id) if course_id is not None else None,
        "operation": operation,
        "duration_ms": round((perf_counter() - started_at) * 1000, 2),
        "outcome": outcome,
    }
    logger.info(json.dumps(event, separators=(",", ":")))
