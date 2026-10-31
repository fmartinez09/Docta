from dataclasses import fields, replace
from uuid import uuid4

import pytest

from docta_api.rag import RAGFailure, RetrievalScope
from docta_api.retrieval_auth import validate_scope


def _scope() -> RetrievalScope:
    return RetrievalScope(
        course_id=uuid4(),
        corpus_version_id=uuid4(),
        principal_user_id=uuid4(),
        conversation_id=uuid4(),
        message_id=uuid4(),
        correlation_id="unit-test",
    )


def test_validate_scope_accepts_a_complete_scope() -> None:
    validate_scope(_scope())


@pytest.mark.parametrize(
    "field",
    [
        "course_id",
        "corpus_version_id",
        "principal_user_id",
        "conversation_id",
        "message_id",
    ],
)
def test_validate_scope_rejects_a_missing_identifier(field: str) -> None:
    assert field in {item.name for item in fields(RetrievalScope)}
    scope = replace(_scope(), **{field: None})

    with pytest.raises(RAGFailure) as error:
        validate_scope(scope)

    assert error.value.code == "RETRIEVAL_SCOPE_INVALID"


@pytest.mark.parametrize("value", [None, object(), {"course_id": uuid4()}])
def test_validate_scope_rejects_a_non_scope_value(value: object) -> None:
    with pytest.raises(RAGFailure) as error:
        validate_scope(value)  # type: ignore[arg-type]

    assert error.value.code == "RETRIEVAL_SCOPE_INVALID"
