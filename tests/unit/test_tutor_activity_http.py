import json

import httpx
import pytest
from tests.unit.test_rag import make_tutor_request

from docta_api.rag import EvidenceResponseValidator, RAGFailure
from docta_api.tutor_http import ChatCompletionsTutorModel


@pytest.mark.anyio
async def test_activity_survives_provider_adapter_and_evidence_validation():
    request = make_tutor_request()
    source = request.evidence[0]
    response = {
        "version": "tutor-intervention-v2",
        "situation": "entry",
        "blocks": [{
            "type": "activity",
            "question": "¿Qué factor importa?",
            "options": [{"id": "a", "text": "Primero"}, {"id": "b", "text": "Segundo"}],
            "correct_option_id": "a",
            "hint": "Lee la fuente.",
            "explanation": "La fuente explica el primero.",
        }],
        "citations": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
        "grounded": True,
    }

    def handler(http_request):
        payload = json.loads(http_request.content)
        schema = payload["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["version"]["const"] == "tutor-intervention-v2"
        assert {"type", "question", "options", "correct_option_id", "hint", "explanation"} == set(
            schema["$defs"]["ActivityBlock"]["required"]
        )
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": json.dumps(response)}
        }]})

    model = ChatCompletionsTutorModel(
        endpoint="https://model.test/v1/chat/completions",
        model="test-model",
        api_key="test-key",
        timeout_seconds=1,
        max_output_tokens=512,
        transport=httpx.MockTransport(handler),
    )
    draft = await model.generate(request)
    validated = EvidenceResponseValidator().validate(draft, request)
    assert validated.intervention.blocks[0].type == "activity"
    assert "Lee la fuente." not in validated.answer

    response["citations"][0]["quote"] = "Un texto que no está en la fuente"
    draft = await model.generate(request)
    with pytest.raises(RAGFailure, match="MODEL_OUTPUT_INVALID"):
        EvidenceResponseValidator().validate(draft, request)

    response["blocks"][0]["correct_option_id"] = "unknown"
    with pytest.raises(RAGFailure, match="MODEL_OUTPUT_INVALID"):
        await model.generate(request)
