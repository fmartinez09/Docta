"""Synchronous embedding port and the derivation of the stored embedding version."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

TEXT_PLACEHOLDER = "{text}"
MAX_EMBEDDING_VERSION_LENGTH = 200  # chunk_embeddings.embedding_version is varchar(200)
MAX_EMBEDDING_DIMENSIONS = 16000  # pgvector `vector` limit
_HASH_LENGTH = 8
# "<model>|<dims>|<hash>": two separators and the widest dimension count.
MAX_EMBED_MODEL_LENGTH = (
    MAX_EMBEDDING_VERSION_LENGTH - 2 - len(str(MAX_EMBEDDING_DIMENSIONS)) - _HASH_LENGTH
)


class EmbeddingUnavailable(Exception):
    """Transient failure; the caller may retry. Never carries payloads or vectors."""


class EmbeddingInvalid(Exception):
    """Permanent failure or unusable response. Never carries payloads or vectors."""


class Embedder(Protocol):
    version: str

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def derive_embedding_version(
    *,
    model: str,
    dimensions: int,
    query_template: str = TEXT_PLACEHOLDER,
    document_template: str = TEXT_PLACEHOLDER,
    input_type: str = "off",
    provider_routing: Mapping[str, Any] | None = None,
) -> str:
    """Return `model|dims|sha8(templates+extras)`; any input change yields a new version."""
    canonical = json.dumps(
        {
            "query_template": query_template,
            "document_template": document_template,
            "input_type": input_type,
            "provider_routing": provider_routing,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_HASH_LENGTH]
    version = f"{model}|{dimensions}|{digest}"
    if len(version) > MAX_EMBEDDING_VERSION_LENGTH:
        raise ValueError("embedding version exceeds the stored length")
    return version
