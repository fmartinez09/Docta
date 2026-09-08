from dataclasses import replace
from uuid import uuid4

import pytest

from docta_api.rag import (
    ABSTENTION,
    DraftCitation,
    Evidence,
    EvidenceResponseValidator,
    RAGFailure,
    RetrievalScope,
    TutorDraft,
    TutorRequest,
)


@pytest.fixture
def tutor_request():
    return make_tutor_request()


def make_tutor_request():
    course, corpus, chunk = uuid4(), uuid4(), uuid4()
    scope = RetrievalScope(course, corpus, uuid4(), uuid4(), uuid4(), "rag-unit")
    evidence = Evidence(
        chunk,
        course,
        corpus,
        uuid4(),
        "Física",
        "a" * 64,
        1,
        1,
        "chunk-0",
        "La velocidad es desplazamiento dividido por tiempo.",
        "b" * 64,
    )
    return TutorRequest(scope, "¿Qué es velocidad?", (evidence,), ())


def draft_for(request):
    return TutorDraft(
        mode="hint",
        answer="Identifica el desplazamiento y el tiempo transcurrido.",
        citations=[
            DraftCitation(chunk_id=request.evidence[0].chunk_id, quote=request.evidence[0].content)
        ],
        grounded=True,
    )


def test_valid_citation_and_exact_quote(tutor_request):
    draft = draft_for(tutor_request)
    assert EvidenceResponseValidator().validate(draft, tutor_request) == draft


@pytest.mark.parametrize(
    "change",
    [
        "foreign",
        "quote",
        "duplicate",
        "empty",
        "ungrounded",
        "blank",
        "abstain_citations",
        "abstain_grounded",
    ],
)
def test_invalid_provider_output_is_rejected(tutor_request, change):
    draft = draft_for(tutor_request)
    data = draft.model_dump(mode="json")
    if change == "foreign":
        data["citations"][0]["chunk_id"] = str(uuid4())
    elif change == "quote":
        data["citations"][0]["quote"] = "This quotation is fabricated."
    elif change == "duplicate":
        data["citations"] *= 2
    elif change == "empty":
        data["citations"] = []
    elif change == "ungrounded":
        data["grounded"] = False
    elif change == "blank":
        data["answer"] = "   "
    else:
        data["mode"] = "abstain"
        if change == "abstain_grounded":
            data["citations"] = []
        else:
            data["grounded"] = False
    with pytest.raises(RAGFailure, match="MODEL_OUTPUT_INVALID"):
        EvidenceResponseValidator().validate(TutorDraft.model_validate(data), tutor_request)


def test_cross_course_evidence_and_missing_evidence_fail_closed(tutor_request):
    invalid = replace(
        tutor_request, evidence=(replace(tutor_request.evidence[0], course_id=uuid4()),)
    )
    with pytest.raises(RAGFailure, match="RETRIEVAL_SCOPE_INVALID"):
        EvidenceResponseValidator().validate(draft_for(tutor_request), invalid)
    with pytest.raises(RAGFailure, match="MODEL_OUTPUT_INVALID"):
        EvidenceResponseValidator().validate(
            draft_for(tutor_request), replace(tutor_request, evidence=())
        )


def test_abstention_never_exposes_arbitrary_provider_text(tutor_request):
    draft = TutorDraft(mode="abstain", answer="A fabricated fact", citations=[], grounded=False)
    validated = EvidenceResponseValidator().validate(draft, tutor_request)
    assert validated.answer == ABSTENTION
    assert validated.citations == []
