"""Vendor-independent evidence, tutor and validation contracts for the phase-0 path."""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

PROMPT_VERSION = "phase0-guidance-v2"
RETRIEVAL_VERSION = "spanish-fts-and-top5-v1"
ABSTENTION = (
    "No encuentro evidencia suficiente en el material activo del curso para responder. "
    "¿Puedes precisar el concepto o indicar la sección del PDF que estás estudiando?"
)


class RAGFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RetrievalScope:
    course_id: UUID
    corpus_version_id: UUID
    principal_user_id: UUID
    conversation_id: UUID
    message_id: UUID
    correlation_id: str


@dataclass(frozen=True)
class Evidence:
    chunk_id: UUID
    course_id: UUID
    corpus_version_id: UUID
    document_version_id: UUID
    document_title: str
    document_sha256: str
    page_start: int
    page_end: int
    fragment: str
    content: str
    content_hash: str


@dataclass(frozen=True)
class HistoryTurn:
    question: str
    answer: str


@dataclass(frozen=True)
class TutorRequest:
    scope: RetrievalScope
    question: str
    evidence: tuple[Evidence, ...]
    history: tuple[HistoryTurn, ...]


class DraftCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    chunk_id: UUID
    quote: str = Field(min_length=10, max_length=1600)


class TutorDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["hint", "guided_question", "explanation", "abstain"]
    answer: str = Field(min_length=1, max_length=6000)
    citations: list[DraftCitation] = Field(max_length=5)
    grounded: bool = Field(strict=True)


class Retriever(Protocol):
    def retrieve(self, scope: RetrievalScope, question: str) -> tuple[Evidence, ...]: ...


class TutorModel(Protocol):
    @property
    def version(self) -> str: ...

    async def generate(self, request: TutorRequest) -> TutorDraft: ...


class ResponseValidator(Protocol):
    def validate(self, draft: TutorDraft, request: TutorRequest) -> TutorDraft: ...


def abstention() -> TutorDraft:
    return TutorDraft(mode="abstain", answer=ABSTENTION, citations=[], grounded=False)


class EvidenceResponseValidator:
    def validate(self, draft: TutorDraft, request: TutorRequest) -> TutorDraft:
        # Revalidate even typed adapters: model_construct or a mutated list must not bypass checks.
        try:
            draft = TutorDraft.model_validate_json(draft.model_dump_json())
        except (ValueError, AttributeError, TypeError) as error:
            raise RAGFailure("MODEL_OUTPUT_INVALID") from error
        sources = {item.chunk_id: item for item in request.evidence}
        if len(sources) != len(request.evidence) or any(
            item.course_id != request.scope.course_id
            or item.corpus_version_id != request.scope.corpus_version_id
            for item in request.evidence
        ):
            raise RAGFailure("RETRIEVAL_SCOPE_INVALID")
        if draft.mode == "abstain":
            if draft.grounded or draft.citations:
                raise RAGFailure("MODEL_OUTPUT_INVALID")
            # A provider cannot smuggle unsupported factual text inside an abstention.
            return abstention()
        if not request.evidence or not draft.grounded or not draft.citations:
            raise RAGFailure("MODEL_OUTPUT_INVALID")
        if not draft.answer.strip():
            raise RAGFailure("MODEL_OUTPUT_INVALID")
        seen: set[UUID] = set()
        for citation in draft.citations:
            evidence = sources.get(citation.chunk_id)
            if (
                evidence is None
                or citation.chunk_id in seen
                or not citation.quote.strip()
                or citation.quote not in evidence.content
            ):
                raise RAGFailure("MODEL_OUTPUT_INVALID")
            seen.add(citation.chunk_id)
        return draft
