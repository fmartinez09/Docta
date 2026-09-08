"""Authorized discovery queries used by the minimal teacher/student workspace."""

from datetime import datetime
from uuid import UUID

from anyio import to_thread
from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from docta_api.api_errors import APIError
from docta_api.conversation_models import Conversation
from docta_api.conversations import ConversationView, _member
from docta_api.courses import CourseResponse, RequiredIdentity
from docta_api.database import Database
from docta_api.identity import AuthenticatedIdentity
from docta_api.models import (
    CorpusVersionDocument,
    Course,
    CourseMembership,
    Document,
    DocumentVersion,
    User,
)

router = APIRouter(prefix="/api/v1", tags=["workspace"])


class DocumentSummary(BaseModel):
    document_id: UUID
    version_id: UUID
    title: str
    state: str
    failure_code: str | None
    corpus_version_id: UUID | None
    created_at: datetime


class WorkspaceService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def courses(self, identity: AuthenticatedIdentity) -> list[CourseResponse]:
        with self.database.session() as session:
            rows = session.execute(
                select(Course, CourseMembership.role)
                .join(CourseMembership, CourseMembership.course_id == Course.id)
                .join(User, User.id == CourseMembership.user_id)
                .where(User.oidc_issuer == identity.issuer, User.oidc_subject == identity.subject)
                .order_by(Course.created_at.desc(), Course.id)
                .limit(100)
            ).all()
            return [
                CourseResponse(
                    id=c.id,
                    title=c.title,
                    role=role,
                    version=c.version,
                    active_corpus_version_id=c.active_corpus_version_id,
                    created_at=c.created_at,
                )
                for c, role in rows
            ]

    def conversations(
        self, identity: AuthenticatedIdentity, course_id: UUID
    ) -> list[ConversationView]:
        with self.database.session() as session:
            user_id = _member(session, identity, course_id)
            rows = session.scalars(
                select(Conversation)
                .where(Conversation.course_id == course_id, Conversation.owner_user_id == user_id)
                .order_by(Conversation.created_at.desc(), Conversation.id)
                .limit(100)
            ).all()
            return [ConversationView.model_validate(row, from_attributes=True) for row in rows]

    def documents(self, identity: AuthenticatedIdentity, course_id: UUID) -> list[DocumentSummary]:
        with self.database.session() as session:
            user_id = _member(session, identity, course_id)
            role = session.scalar(
                select(CourseMembership.role).where(
                    CourseMembership.course_id == course_id, CourseMembership.user_id == user_id
                )
            )
            if role != "teacher":
                raise APIError(404, "course_not_found", "The course was not found.")
            rows = session.execute(
                select(DocumentVersion, Document.title, CorpusVersionDocument.corpus_version_id)
                .join(
                    Document,
                    (Document.id == DocumentVersion.document_id)
                    & (Document.course_id == DocumentVersion.course_id),
                )
                .outerjoin(
                    CorpusVersionDocument,
                    CorpusVersionDocument.document_version_id == DocumentVersion.id,
                )
                .where(DocumentVersion.course_id == course_id)
                .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id)
                .limit(100)
            ).all()
            return [
                DocumentSummary(
                    document_id=v.document_id,
                    version_id=v.id,
                    title=title,
                    state=v.state,
                    failure_code=v.failure_code,
                    corpus_version_id=corpus,
                    created_at=v.created_at,
                )
                for v, title, corpus in rows
            ]


async def _query(request: Request, name: str, *args):
    service: WorkspaceService = request.app.state.workspace_service
    try:
        return await to_thread.run_sync(getattr(service, name), *args)
    except SQLAlchemyError as error:
        raise APIError(
            503, "persistence_unavailable", "The workspace is temporarily unavailable."
        ) from error


@router.get("/courses", response_model=list[CourseResponse])
async def list_courses(request: Request, identity: RequiredIdentity):
    return await _query(request, "courses", identity)


@router.get("/courses/{course_id}/conversations", response_model=list[ConversationView])
async def list_conversations(course_id: UUID, request: Request, identity: RequiredIdentity):
    return await _query(request, "conversations", identity, course_id)


@router.get("/courses/{course_id}/documents", response_model=list[DocumentSummary])
async def list_documents(course_id: UUID, request: Request, identity: RequiredIdentity):
    return await _query(request, "documents", identity, course_id)
