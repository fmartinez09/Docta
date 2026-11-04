"""Vendor-independent evidence, tutor and validation contracts for the phase-0 path."""

import re
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from docta_api.tutor_contract import (
    IntentCitation,
    TutorIntervention,
    equivalent_text,
    mode_for,
)

PROMPT_VERSION = "socratic-tutor-v4"
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
    situation: str | None = None
    help_level: int = 0


@dataclass(frozen=True)
class TutorRequest:
    scope: RetrievalScope
    question: str
    evidence: tuple[Evidence, ...]
    history: tuple[HistoryTurn, ...]
    # Continuity (D-socratic-v1): derived query bound to the stored message, never client input.
    turn_kind: str = "question"
    turn_intent: str | None = None
    retrieval_query: str | None = None
    needs_antecedent: bool = False


class DraftCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    chunk_id: UUID
    quote: str = Field(min_length=10, max_length=1600)


class TutorDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["hint", "guided_question", "explanation", "abstain", "clarify"]
    answer: str = Field(min_length=1, max_length=6000)
    citations: list[DraftCitation] = Field(max_length=5)
    grounded: bool = Field(strict=True)
    intervention: TutorIntervention | None = None


class Retriever(Protocol):
    @property
    def version(self) -> str: ...

    def retrieve(self, scope: RetrievalScope, question: str) -> tuple[Evidence, ...]: ...


class TutorModel(Protocol):
    @property
    def version(self) -> str: ...

    async def generate(self, request: TutorRequest) -> TutorDraft: ...


class ResponseValidator(Protocol):
    def validate(self, draft: TutorDraft, request: TutorRequest) -> TutorDraft: ...


def abstention() -> TutorDraft:
    return TutorDraft(mode="abstain", answer=ABSTENTION, citations=[], grounded=False)


CLARIFICATION = (
    "¿A qué actividad o pregunta te refieres? Escríbela o indica el fragmento "
    "del material que estás estudiando."
)


def clarification() -> TutorDraft:
    """Canonical, uncited coordination request used when no authorized antecedent exists."""
    return draft_from_intervention(
        TutorIntervention.model_validate(
            {
                "version": "tutor-intervention-v1",
                "situation": "ambiguous",
                "blocks": [{"type": "question", "text": CLARIFICATION}],
                "citations": [],
                "grounded": False,
            }
        )
    )


def draft_from_intervention(intervention: TutorIntervention) -> TutorDraft:
    """The stored fields are derived from the one structured model output."""
    return TutorDraft(
        mode=mode_for(intervention),
        answer=equivalent_text(intervention),
        citations=[
            DraftCitation(chunk_id=c.chunk_id, quote=c.quote) for c in intervention.citations
        ],
        grounded=intervention.grounded,
        intervention=intervention,
    )


class EvidenceResponseValidator:
    def validate(self, draft: TutorDraft, request: TutorRequest) -> TutorDraft:
        # Revalidate even typed adapters: model_construct or a mutated list must not bypass checks.
        try:
            draft = TutorDraft.model_validate_json(draft.model_dump_json())
        except (ValueError, AttributeError, TypeError) as error:
            raise RAGFailure("MODEL_OUTPUT_INVALID") from error
        intervention = draft.intervention
        if intervention is not None:
            if (
                draft.answer != equivalent_text(intervention)
                or draft.mode != mode_for(intervention)
                or draft.grounded != intervention.grounded
                or [(c.chunk_id, c.quote) for c in draft.citations]
                != [(c.chunk_id, c.quote) for c in intervention.citations]
                or len({c.chunk_id for c in intervention.citations}) != len(intervention.citations)
            ):
                raise RAGFailure("MODEL_OUTPUT_INVALID")
            if intervention.situation == "ambiguous":
                # Bounded coordination: one uncited question and no factual content.
                return draft
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
        citations: list[DraftCitation] = []
        for citation in draft.citations:
            evidence = sources.get(citation.chunk_id)
            if evidence is None or not citation.quote.strip():
                raise RAGFailure("MODEL_OUTPUT_INVALID")
            quote = citation.quote
            if quote not in evidence.content:
                # A case-only mismatch can be replaced by the unique verbatim source span.
                # All other changes, including whitespace and punctuation, still fail closed.
                matches = list(re.finditer(re.escape(quote), evidence.content, re.IGNORECASE))
                if len(matches) != 1:
                    raise RAGFailure("MODEL_OUTPUT_INVALID")
                match = matches[0]
                quote = evidence.content[match.start() : match.end()]
            if citation.chunk_id not in seen:
                citations.append(DraftCitation(chunk_id=citation.chunk_id, quote=quote))
                seen.add(citation.chunk_id)
        if intervention is not None:
            repaired = intervention.model_copy(
                update={
                    "citations": [
                        IntentCitation(chunk_id=c.chunk_id, quote=c.quote) for c in citations
                    ]
                }
            )
            return draft_from_intervention(repaired)
        return draft.model_copy(update={"citations": citations})
