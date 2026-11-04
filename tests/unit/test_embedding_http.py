import json
import math

import httpx
import pytest
from tests.unit.test_config import embed_settings

from docta_api.embedding import EmbeddingInvalid, EmbeddingUnavailable
from docta_api.embedding_http import HttpEmbedder, build_embedder

SECRET = "embed-secret"
TEXT = "texto confidencial del curso"


def vectors(count: int, dims: int = 4) -> dict:
    return {
        "data": [
            {"index": index, "embedding": [float(index + 1)] * dims} for index in range(count)
        ]
    }


def make(handler, **overrides) -> HttpEmbedder:
    options = {
        "endpoint": "https://embed.test/v1/embeddings",
        "model": "vendor/model",
        "api_key": SECRET,
        "dimensions": 4,
        "timeout_seconds": 1,
        "batch_size": 2,
        "transport": httpx.MockTransport(handler),
    }
    return HttpEmbedder(**(options | overrides))


def echo(requests: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=vectors(len(json.loads(request.content)["input"])))

    return handler


def test_documents_use_template_batching_and_auth() -> None:
    requests: list[httpx.Request] = []
    embedder = make(echo(requests), document_template="passage: {text}")

    result = embedder.embed_documents(["a", "b", "c"])

    assert len(result) == 3
    bodies = [json.loads(request.content) for request in requests]
    assert [body["input"] for body in bodies] == [["passage: a", "passage: b"], ["passage: c"]]
    assert bodies[0]["model"] == "vendor/model"
    assert "input_type" not in bodies[0] and "provider" not in bodies[0]
    assert "dimensions" not in bodies[0]
    assert requests[0].headers["authorization"] == f"Bearer {SECRET}"
    assert str(requests[0].url) == "https://embed.test/v1/embeddings"


def test_query_uses_query_template() -> None:
    requests: list[httpx.Request] = []
    embedder = make(echo(requests), query_template="query: {text}")

    assert embedder.embed_query("hola") == [1.0] * 4
    assert json.loads(requests[0].content)["input"] == ["query: hola"]


def test_empty_documents_make_no_request() -> None:
    requests: list[httpx.Request] = []

    assert make(echo(requests)).embed_documents([]) == []
    assert requests == []


def test_nvidia_input_type_and_routing_are_sent() -> None:
    requests: list[httpx.Request] = []
    routing = {"order": ["nvidia"], "allow_fallbacks": False}
    embedder = make(echo(requests), input_type="nvidia", provider_routing=routing)

    embedder.embed_documents(["a"])
    embedder.embed_query("q")

    document_body, query_body = (json.loads(request.content) for request in requests)
    assert document_body["input_type"] == "passage"
    assert query_body["input_type"] == "query"
    assert document_body["provider"] == routing


def test_version_changes_with_extras() -> None:
    plain = make(echo([]))
    assert plain.version.startswith("vendor/model|4|")
    assert make(echo([]), input_type="nvidia").version != plain.version


def test_response_items_are_reordered_by_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [2.0] * 4},
                    {"index": 0, "embedding": [1.0] * 4},
                ]
            },
        )

    assert make(handler).embed_documents(["a", "b"]) == [[1.0] * 4, [2.0] * 4]


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_transient_statuses_are_unavailable(status: int) -> None:
    with pytest.raises(EmbeddingUnavailable):
        make(lambda request: httpx.Response(status, text=TEXT)).embed_query(TEXT)


@pytest.mark.parametrize("status", [400, 401, 404, 422])
def test_permanent_statuses_are_invalid(status: int) -> None:
    with pytest.raises(EmbeddingInvalid):
        make(lambda request: httpx.Response(status, text=TEXT)).embed_query(TEXT)


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
def test_transport_failures_are_unavailable(error) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error("boom", request=request)

    with pytest.raises(EmbeddingUnavailable):
        make(handler).embed_query(TEXT)


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        vectors(2),
        vectors(1, dims=3),
        vectors(1, dims=5),
        {"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0, "x"]}]},
        {"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0, True]}]},
        {"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0, None]}]},
        {"data": [{"index": 3, "embedding": [1.0] * 4}]},
        {"data": "nope"},
        {"data": [{"index": 0}]},
        {"other": []},
        [],
    ],
)
def test_invalid_responses_are_rejected(payload) -> None:
    with pytest.raises(EmbeddingInvalid):
        make(lambda request: httpx.Response(200, json=payload)).embed_query("q")


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_values_are_rejected(token: str) -> None:
    body = f'{{"data":[{{"index":0,"embedding":[1.0,{token},1.0,1.0]}}]}}'

    with pytest.raises(EmbeddingInvalid):
        make(lambda request: httpx.Response(200, content=body)).embed_query("q")


def test_non_json_body_is_invalid() -> None:
    with pytest.raises(EmbeddingInvalid):
        make(lambda request: httpx.Response(200, text="<html>")).embed_query("q")


def test_batch_failure_discards_partial_results() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json=vectors(2))

    with pytest.raises(EmbeddingUnavailable):
        make(handler).embed_documents(["a", "b", "c"])


def test_failures_never_expose_payload_text_or_secret() -> None:
    def leaking(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"{TEXT} {SECRET} [0.1, 0.2]")

    def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.123] * 3}]})

    for handler in (leaking, malformed):
        with pytest.raises((EmbeddingUnavailable, EmbeddingInvalid)) as caught:
            make(handler).embed_query(TEXT)
        rendered = f"{caught.value!r} {caught.value} {caught.value.__cause__!r}"
        assert TEXT not in rendered and SECRET not in rendered and "0.123" not in rendered
        assert caught.value.__context__ is None or caught.value.__suppress_context__


def test_returned_vectors_are_finite_floats() -> None:
    result = make(echo([])).embed_query("q")

    assert all(isinstance(value, float) and math.isfinite(value) for value in result)


def test_build_embedder_uses_settings() -> None:
    requests: list[httpx.Request] = []
    settings = embed_settings(
        embed_dimensions=4,
        embed_batch_size=1,
        embed_query_template="q: {text}",
        embed_input_type="nvidia",
    )

    embedder = build_embedder(settings, transport=httpx.MockTransport(echo(requests)))

    assert embedder is not None
    embedder.embed_documents(["a", "b"])
    assert len(requests) == 2
    assert json.loads(requests[0].content)["input_type"] == "passage"
    assert SECRET not in embedder.version


def test_build_embedder_returns_none_without_configuration() -> None:
    settings = embed_settings(
        embed_endpoint_url=None, embed_model=None, embed_api_key=None, embed_dimensions=None
    )

    assert build_embedder(settings) is None
