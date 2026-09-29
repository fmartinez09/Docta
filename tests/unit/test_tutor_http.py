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


def adapter(handler):
    return ChatCompletionsTutorModel(
        endpoint="https://model.test/v1/chat/completions",
        model="configured-test-model",
        api_key="test-secret",
        timeout_seconds=1,
        max_output_tokens=512,
        transport=httpx.MockTransport(handler),
    )


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
        assert schema["properties"]["answer"]["minLength"] == 1
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
