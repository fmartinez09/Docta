import json
from dataclasses import replace

import httpx
import pytest
from tests.unit.test_rag import draft_for, make_tutor_request

from docta_api.rag import EvidenceResponseValidator, HistoryTurn, RAGFailure
from docta_api.tutor_http import ChatCompletionsTutorModel


@pytest.fixture
def tutor_request():
    return make_tutor_request()


def adapter(handler, *, endpoint="https://model.test/v1/chat/completions"):
    return ChatCompletionsTutorModel(
        endpoint=endpoint,
        model="configured-test-model",
        api_key="test-secret",
        timeout_seconds=1,
        max_output_tokens=512,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("profile", ["standard", "llama_cpp"])
@pytest.mark.parametrize("version", ["v2", "tutor-intervention-v2"])
async def test_provider_schema_requires_fields_per_block_and_rejects_partial_output(
    tutor_request, profile, version
):
    evidence = tutor_request.evidence[0]
    data = {
        "version": version,
        "situation": "entry",
        "blocks": [
            {"type": "explanation"},
            {"type": "explanation", "text": "Una explicación."},
            {"type": "sources"},
            {"type": "activity", "question": "¿Por qué?"},
        ],
        "citations": [{"chunk_id": str(evidence.chunk_id), "quote": evidence.content}],
        "grounded": True,
    }

    def handler(request):
        payload = json.loads(request.content)
        schema = payload["response_format"]["json_schema"]["schema"]
        refs = schema["properties"]["blocks"]["items"]["anyOf"]
        assert {ref["$ref"] for ref in refs} == {
            f"#/$defs/{name}Block"
            for name in ("Question", "Hint", "Explanation", "Diagram", "Activity", "Sources")
        }
        assert set(schema["$defs"]["ExplanationBlock"]["required"]) == {"type", "title", "text"}
        assert set(schema["$defs"]["SourcesBlock"]["required"]) == {"type", "citation_indexes"}
        assert set(schema["$defs"]["ActivityBlock"]["required"]) == {
            "type", "question", "options", "correct_option_id", "hint", "explanation"
        }
        assert schema["properties"]["version"]["const"] == "tutor-intervention-v2"
        assert '"tutor-intervention-v2"' in payload["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": json.dumps(data)}
        }]})

    model = ChatCompletionsTutorModel(
        endpoint="https://model.test/v1/chat/completions", model="test", api_key="test",
        timeout_seconds=1, max_output_tokens=512, schema_profile=profile,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RAGFailure, match="MODEL_OUTPUT_INVALID"):
        await model.generate(tutor_request)


@pytest.mark.anyio
async def test_unsloth_profile_limits_generation_and_keeps_validation(tutor_request):
    data = draft_for(tutor_request).model_dump(mode="json")

    def handler(request):
        payload = json.loads(request.content)
        assert payload["max_tokens"] == payload["max_completion_tokens"] == 512
        assert payload["enable_thinking"] is False
        assert payload["enable_tools"] is False
        assert payload["mcp_enabled"] is False
        assert payload["stream"] is False
        assert payload["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]},
        )

    model = ChatCompletionsTutorModel(
        endpoint="https://model.test/v1/chat/completions",
        model="test-model",
        api_key="test-secret",
        timeout_seconds=1,
        max_output_tokens=512,
        provider="unsloth",
        schema_profile="llama_cpp",
        transport=httpx.MockTransport(handler),
    )
    assert (await model.generate(tutor_request)).mode == "hint"
    data["answer"] = "x" * 6001
    with pytest.raises(RAGFailure, match=r"^MODEL_OUTPUT_INVALID$"):
        await model.generate(tutor_request)


@pytest.mark.anyio
async def test_llama_schema_profile_preserves_local_output_limits(tutor_request):
    draft = draft_for(tutor_request).model_dump(mode="json")

    def handler(request):
        payload = json.loads(request.content)
        schema = payload["response_format"]["json_schema"]["schema"]
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert "maxLength" not in json.dumps(schema)
        assert schema["properties"]["blocks"]["minItems"] == 1
        assert schema["properties"]["citations"]["maxItems"] == 5
        assert schema["additionalProperties"] is False
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(draft)}}]
            },
        )

    model = ChatCompletionsTutorModel(
        endpoint="https://model.test/v1/chat/completions",
        model="test-model",
        api_key="test-secret",
        timeout_seconds=1,
        max_output_tokens=512,
        schema_profile="llama_cpp",
        transport=httpx.MockTransport(handler),
    )
    assert (await model.generate(tutor_request)).answer == draft["answer"]
    draft["answer"] = "x" * 6001
    with pytest.raises(RAGFailure, match=r"^MODEL_OUTPUT_INVALID$"):
        await model.generate(tutor_request)


@pytest.mark.anyio
async def test_adapter_sends_bounded_nonstreaming_structured_contract(tutor_request):
    draft = draft_for(tutor_request)

    def handler(request):
        payload = json.loads(request.content)
        assert payload["stream"] is False and payload["store"] is False
        assert payload["max_completion_tokens"] == 512
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert "tools" not in payload
        assert "enable_thinking" not in payload and "max_tokens" not in payload
        assert request.headers["x-request-id"] == "rag-unit"
        context = json.loads(payload["messages"][1]["content"])
        assert context["sources"][0]["text"] == tutor_request.evidence[0].content
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": draft.model_dump_json(),
                        },
                    }
                ]
            },
        )

    assert await adapter(handler).generate(tutor_request) == draft


@pytest.mark.anyio
async def test_concept_question_requests_explanation_and_preserves_evidence(tutor_request):
    """Exercise the actual provider request and validated structured response, without an LLM."""
    intervention = {
        "version": "tutor-intervention-v2",
        "situation": "entry",
        "blocks": [
            {
                "type": "explanation",
                "title": "Velocidad",
                "text": "La velocidad relaciona desplazamiento y tiempo.",
            },
            {"type": "question", "text": "¿Qué magnitudes relaciona?"},
            {"type": "sources", "citation_indexes": [0]},
        ],
        "citations": [
            {
                "chunk_id": str(tutor_request.evidence[0].chunk_id),
                "quote": tutor_request.evidence[0].content,
            }
        ],
        "grounded": True,
    }

    def handler(request):
        payload = json.loads(request.content)
        policy = payload["messages"][0]["content"]
        assert "pregunta conceptual directa" in policy
        assert "responde primero con una explicación" in policy
        assert "No agregues una actividad por rutina" in policy
        assert "Una pregunta de información no necesita un cuestionario" in policy
        assert "si usas activity, no añadas un bloque" in policy
        assert "No reveles la alternativa" in policy
        assert "las fuentes no sustenten" in policy
        context = json.loads(payload["messages"][1]["content"])
        assert context["question"] == tutor_request.question
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(intervention)}}
                ]
            },
        )

    draft = await adapter(handler).generate(tutor_request)
    validated = EvidenceResponseValidator().validate(draft, tutor_request)
    assert validated.intervention is not None
    assert validated.intervention.blocks[0].type == "explanation"
    assert validated.citations[0].quote == tutor_request.evidence[0].content


@pytest.mark.anyio
async def test_gemini_compatibility_omits_unsupported_store(tutor_request):
    def handler(request):
        payload = json.loads(request.content)
        assert "store" not in payload
        assert payload["stream"] is False
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["max_completion_tokens"] == 512
        schema = payload["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["activities"]["items"] == {"$ref": "#/$defs/ActivityBlock"}
        assert "activities" in schema["required"]
        assert "anyOf" not in json.dumps(schema)
        assert "minItems" not in json.dumps(schema)
        return httpx.Response(503, text="private provider payload")

    with pytest.raises(RAGFailure, match=r"^MODEL_UNAVAILABLE$"):
        await adapter(
            handler,
            endpoint="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        ).generate(tutor_request)


@pytest.mark.anyio
async def test_gemini_typed_lists_are_validated_and_rendered_in_order(tutor_request):
    source = tutor_request.evidence[0]
    data = {
        "situation": "entry", "grounded": True,
        "explanations": [{"title": "Concepto", "text": "Explicación."}],
        "hints": [], "diagrams": [], "questions": [],
        "activities": [{
            "question": "¿Cuál?",
            "options": [{"id": "a", "text": "Uno"}, {"id": "b", "text": "Dos"}],
            "correct_option_id": "a", "hint": "Mira la fuente.", "explanation": "Es uno.",
        }],
        "source_blocks": [{"citation_indexes": [0]}],
        "citations": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
    }

    def handler(request):
        payload = json.loads(request.content)
        assert "no envíes blocks" in payload["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": json.dumps(data)}
        }]})

    model = adapter(handler, endpoint="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
    draft = await model.generate(tutor_request)
    validated = EvidenceResponseValidator().validate(draft, tutor_request)
    assert [b.type for b in validated.intervention.blocks] == ["explanation", "activity", "sources"]
    del data["activities"][0]["options"]
    with pytest.raises(RAGFailure, match="MODEL_OUTPUT_INVALID"):
        await model.generate(tutor_request)


@pytest.mark.anyio
@pytest.mark.parametrize("quote_kind", ["original", "translated", "reformatted"])
async def test_spanish_explanation_keeps_original_english_pdf_quote(tutor_request, quote_kind):
    source = "Speed is displacement divided by time.\nMeasurements must use consistent units."
    request = replace(
        tutor_request,
        evidence=(replace(tutor_request.evidence[0], content=source),),
        history=(HistoryTurn("Hola", "No encuentro evidencia suficiente."),),
    )
    quotes = {
        "original": source,
        "translated": "La velocidad es desplazamiento dividido por tiempo.",
        "reformatted": source.replace("\n", " "),
    }
    data = draft_for(request).model_dump(mode="json")
    data.update(mode="explanation", answer="La velocidad relaciona desplazamiento y tiempo.")
    data["citations"][0]["quote"] = quotes[quote_kind]

    def handler(http_request):
        payload = json.loads(http_request.content)
        context = json.loads(payload["messages"][1]["content"])
        assert context["question"] == request.question
        assert context["sources"] == [
            {"chunk_id": str(request.evidence[0].chunk_id), "text": source}
        ]
        assert context["history"] == [
            {"question": request.history[0].question, "answer": request.history[0].answer}
        ]
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]},
        )

    draft = await adapter(handler).generate(request)
    if quote_kind == "original":
        validated = EvidenceResponseValidator().validate(draft, request)
        assert validated.mode == "explanation"
        assert validated.citations[0].quote == source
    else:
        with pytest.raises(RAGFailure, match=r"^MODEL_OUTPUT_INVALID$"):
            EvidenceResponseValidator().validate(draft, request)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        {"choices": []},
        {},
        {"choices": [{"finish_reason": "length"}]},
        {"choices": [{"finish_reason": "stop", "message": {"refusal": "secret-refusal"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "not JSON secret"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "null"}}]},
    ],
)
async def test_malformed_truncated_or_refused_output_is_safe(tutor_request, response):
    with pytest.raises(RAGFailure, match=r"^MODEL_OUTPUT_INVALID$"):
        await adapter(lambda _: httpx.Response(200, json=response)).generate(tutor_request)


@pytest.mark.anyio
async def test_provider_outage_and_oversized_output_are_safe(tutor_request):
    with pytest.raises(RAGFailure, match=r"^MODEL_UNAVAILABLE$"):
        await adapter(lambda _: httpx.Response(503, text="provider-secret")).generate(tutor_request)
    with pytest.raises(RAGFailure, match=r"^MODEL_OUTPUT_INVALID$"):
        await adapter(lambda _: httpx.Response(200, content=b"x" * 128001)).generate(tutor_request)
