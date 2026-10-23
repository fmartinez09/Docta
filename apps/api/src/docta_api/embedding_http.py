"""OpenAI-compatible `/v1/embeddings` adapter. Never exposes payloads, texts or vectors."""

import math
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from docta_api.config import Settings
from docta_api.embedding import (
    TEXT_PLACEHOLDER,
    Embedder,
    EmbeddingInvalid,
    EmbeddingUnavailable,
    derive_embedding_version,
)

# NVIDIA asymmetric models distinguish the question from the stored passage.
_NVIDIA_INPUT_TYPES = {"query": "query", "document": "passage"}
_TRANSIENT_STATUSES = {408, 425, 429}


class HttpEmbedder:
    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str,
        dimensions: int,
        timeout_seconds: float,
        batch_size: int,
        query_template: str = TEXT_PLACEHOLDER,
        document_template: str = TEXT_PLACEHOLDER,
        input_type: str = "off",
        provider_routing: Mapping[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._model = model
        self._api_key = api_key
        self._dimensions = dimensions
        self._timeout_seconds = timeout_seconds
        self._batch_size = batch_size
        self._query_template = query_template
        self._document_template = document_template
        self._input_type = input_type
        self._provider_routing = dict(provider_routing) if provider_routing else None
        self._transport = transport
        self.version = derive_embedding_version(
            model=model,
            dimensions=dimensions,
            query_template=query_template,
            document_template=document_template,
            input_type=input_type,
            provider_routing=self._provider_routing,
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        inputs = [self._document_template.replace(TEXT_PLACEHOLDER, text) for text in texts]
        vectors: list[list[float]] = []
        for start in range(0, len(inputs), self._batch_size):
            vectors.extend(self._request(inputs[start : start + self._batch_size], "document"))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._request([self._query_template.replace(TEXT_PLACEHOLDER, text)], "query")[0]

    def _request(self, inputs: list[str], kind: str) -> list[list[float]]:
        body: dict[str, Any] = {
            "model": self._model,
            "input": inputs,
            "encoding_format": "float",
        }
        if self._input_type == "nvidia":
            body["input_type"] = _NVIDIA_INPUT_TYPES[kind]
        if self._provider_routing:
            body["provider"] = self._provider_routing
        try:
            with httpx.Client(timeout=self._timeout_seconds, transport=self._transport) as client:
                response = client.post(
                    self._endpoint,
                    json=body,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.HTTPError:
            raise EmbeddingUnavailable("Embedding provider is unreachable") from None
        status = response.status_code
        if status >= 500 or status in _TRANSIENT_STATUSES:
            raise EmbeddingUnavailable(f"Embedding provider returned HTTP {status}")
        if status != 200:
            raise EmbeddingInvalid(f"Embedding provider rejected the request with HTTP {status}")
        return self._parse(response, expected=len(inputs))

    def _parse(self, response: httpx.Response, *, expected: int) -> list[list[float]]:
        try:
            data = response.json()["data"]
            if not isinstance(data, list):
                raise TypeError
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            if [item.get("index", position) for position, item in enumerate(ordered)] != list(
                range(len(ordered))
            ):
                raise ValueError
            vectors = [item["embedding"] for item in ordered]
        except (ValueError, TypeError, KeyError, AttributeError):
            raise EmbeddingInvalid("Embedding response has an unexpected shape") from None
        if len(vectors) != expected:
            raise EmbeddingInvalid("Embedding response has the wrong number of vectors")
        for vector in vectors:
            if not isinstance(vector, list) or len(vector) != self._dimensions:
                raise EmbeddingInvalid("Embedding response has the wrong dimensions")
            if not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                for value in vector
            ):
                raise EmbeddingInvalid("Embedding response contains non-finite values")
        return [[float(value) for value in vector] for vector in vectors]


def build_embedder(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> Embedder | None:
    """Return the configured embedder, or None when no embedder is configured."""
    if not settings.embedder_configured:
        return None
    assert settings.embed_endpoint_url and settings.embed_model and settings.embed_api_key
    assert settings.embed_dimensions
    return HttpEmbedder(
        endpoint=str(settings.embed_endpoint_url),
        model=settings.embed_model,
        api_key=settings.embed_api_key.get_secret_value(),
        dimensions=settings.embed_dimensions,
        timeout_seconds=settings.embed_timeout_seconds,
        batch_size=settings.embed_batch_size,
        query_template=settings.embed_query_template,
        document_template=settings.embed_document_template,
        input_type=settings.embed_input_type,
        provider_routing=settings.embed_provider_routing,
        transport=transport,
    )
