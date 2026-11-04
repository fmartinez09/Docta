import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from docta_api.tutor_contract import (
    TutorIntervention,
    allowed_actions,
    equivalent_text,
)

CITE = {"chunk_id": str(uuid4()), "quote": "una cita textual suficiente"}


def build(**overrides):
    data = {
        "version": "tutor-intervention-v1",
        "situation": "blocked",
        "blocks": [{"type": "hint", "level": 1, "text": "Revisa el segundo párrafo."}],
        "citations": [CITE],
        "grounded": True,
    }
    data.update(overrides)
    return TutorIntervention.model_validate(data)


def test_diagram_has_equivalent_text_and_actions():
    value = build(
        blocks=[
            {
                "type": "diagram",
                "kind": "sequence",
                "title": "Causas",
                "nodes": [{"id": "a", "label": "Crisis"}, {"id": "b", "label": "Revuelta"}],
                "edges": [{"source": "a", "target": "b", "label": "provoca"}],
            },
            {"type": "sources", "citation_indexes": [0]},
        ]
    )
    text = equivalent_text(value)
    assert "Crisis → Revuelta (provoca)" in text and "[1]" in text
    assert allowed_actions(value) == ["another_hint", "explain", "view_source"]


ACTIVITY = {
    "type": "activity",
    "question": "¿Qué factor influyó?",
    "options": [{"id": "a", "text": "La crisis"}, {"id": "b", "text": "El clima"}],
    "correct_option_id": "a",
    "hint": "Revisa el texto.",
    "explanation": "El material menciona la crisis.",
}


def test_activity_preserves_v1_and_does_not_leak_solution_in_text():
    value = build(version="tutor-intervention-v2", blocks=[ACTIVITY])
    assert equivalent_text(value) == (
        "Ahora te toca razonar: ¿Qué factor influyó?\nA. La crisis\nB. El clima"
    )
    assert ACTIVITY["hint"] not in equivalent_text(value)
    assert ACTIVITY["explanation"] not in equivalent_text(value)
    with pytest.raises(ValidationError):
        build(version="tutor-intervention-v1", blocks=[ACTIVITY])


@pytest.mark.parametrize(
    "change",
    [
        {"correct_option_id": "z"},
        {"options": [{"id": "a", "text": "Uno"}]},
        {"options": [{"id": "a", "text": "Uno"}, {"id": "a", "text": "Dos"}]},
        {"options": [{"id": "a", "text": "Uno"}, {"id": "b", "text": "Uno"}]},
        {"hint": ""},
        {"explanation": ""},
    ],
)
def test_invalid_activity_is_rejected(change):
    with pytest.raises(ValidationError):
        build(version="tutor-intervention-v2", blocks=[ACTIVITY | change])


def test_activity_cannot_bypass_one_question_or_evidence_rules():
    with pytest.raises(ValidationError):
        build(
            version="tutor-intervention-v2",
            blocks=[ACTIVITY, {"type": "question", "text": "¿Otra pregunta?"}],
        )
    with pytest.raises(ValidationError):
        build(version="tutor-intervention-v2", blocks=[ACTIVITY], citations=[], grounded=False)


def test_clarification_needs_no_citations_but_nothing_else():
    ok = build(
        situation="ambiguous",
        blocks=[{"type": "question", "text": "¿Qué consigna trabajas?"}],
        citations=[],
        grounded=False,
    )
    assert allowed_actions(ok) == []
    with pytest.raises(ValidationError):
        build(
            situation="ambiguous",
            blocks=[{"type": "explanation", "title": "x", "text": "hecho"}],
            citations=[],
            grounded=False,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"citations": []},
        {"blocks": [{"type": "question", "text": "a"}, {"type": "question", "text": "b"}]},
        {"blocks": [{"type": "sources", "citation_indexes": [3]}]},
        {"blocks": [{"type": "html", "text": "<b>"}]},
        {
            "blocks": [
                {
                    "type": "diagram",
                    "kind": "relations",
                    "title": "t",
                    "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                    "edges": [{"source": "a", "target": "z"}],
                }
            ]
        },
    ],
)
def test_invalid_interventions_are_rejected(overrides):
    with pytest.raises(ValidationError):
        build(**overrides)


FIXTURES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "tutor_contract.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", FIXTURES["cases"], ids=lambda c: c["name"])
def test_shared_fixture_parity(case):
    if not case["valid"]:
        with pytest.raises(ValidationError):
            TutorIntervention.model_validate(case["value"])
        return
    value = TutorIntervention.model_validate(case["value"])
    assert equivalent_text(value) == case["text"]
    assert allowed_actions(value) == case["actions"]
