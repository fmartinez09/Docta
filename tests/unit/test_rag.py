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


def test_unique_case_only_quote_is_restored_to_exact_source_text(tutor_request):
    source = "El triángulo de Pascal es una configuración numérica."
    request = replace(
        tutor_request, evidence=(replace(tutor_request.evidence[0], content=source),)
    )
    draft = TutorDraft(
        mode="explanation",
        answer="Cada fila comienza y termina en uno.",
        citations=[
            DraftCitation(
                chunk_id=request.evidence[0].chunk_id,
                quote="el triángulo de Pascal es",
            )
        ],
        grounded=True,
    )

    validated = EvidenceResponseValidator().validate(draft, request)

    assert validated.citations[0].quote == "El triángulo de Pascal es"


def test_ambiguous_case_only_quote_is_rejected(tutor_request):
    source = "El triángulo de Pascal es uno. EL TRIÁNGULO DE PASCAL ES otro."
    request = replace(
        tutor_request, evidence=(replace(tutor_request.evidence[0], content=source),)
    )
    draft = TutorDraft(
        mode="explanation",
        answer="Una explicación.",
        citations=[
            DraftCitation(
                chunk_id=request.evidence[0].chunk_id,
                quote="el triángulo de Pascal es",
            )
        ],
        grounded=True,
    )

    with pytest.raises(RAGFailure, match="MODEL_OUTPUT_INVALID"):
        EvidenceResponseValidator().validate(draft, request)


def test_repeated_citations_to_one_chunk_are_checked_then_collapsed(tutor_request):
    source = (
        "El triángulo de Pascal es una configuración numérica, "
        "donde cada fila empieza con uno."
    )
    request = replace(
        tutor_request, evidence=(replace(tutor_request.evidence[0], content=source),)
    )
    draft = TutorDraft(
        mode="explanation",
        answer="Cada fila comienza con uno.",
        citations=[
            DraftCitation(
                chunk_id=request.evidence[0].chunk_id,
                quote="el triángulo de Pascal es una configuración numérica",
            ),
            DraftCitation(
                chunk_id=request.evidence[0].chunk_id,
                quote="donde cada fila empieza con uno",
            ),
        ],
        grounded=True,
    )

    validated = EvidenceResponseValidator().validate(draft, request)

    assert [citation.quote for citation in validated.citations] == [
        "El triángulo de Pascal es una configuración numérica"
    ]


def test_repeated_citation_with_fabricated_quote_is_rejected(tutor_request):
    draft = draft_for(tutor_request)
    draft.citations.append(
        DraftCitation(chunk_id=tutor_request.evidence[0].chunk_id, quote="fabricated quote")
    )

    with pytest.raises(RAGFailure, match="MODEL_OUTPUT_INVALID"):
        EvidenceResponseValidator().validate(draft, tutor_request)


@pytest.mark.parametrize(
    "change",
    [
        "foreign",
        "quote",
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
