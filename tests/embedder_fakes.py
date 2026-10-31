"""Deterministic in-memory Embedder with injectable failures, for tests only."""

import hashlib
import math
from collections.abc import Sequence

from docta_api.embedding import derive_embedding_version


class FakeEmbedder:
    def __init__(
        self,
        *,
        dimensions: int = 8,
        model: str = "fake-embedder",
        fail_with: Exception | None = None,
        fail_calls: int | None = None,
        returned_dimensions: int | None = None,
        returned_count_delta: int = 0,
    ) -> None:
        self.dimensions = dimensions
        self.version = derive_embedding_version(model=model, dimensions=dimensions)
        self.fail_with = fail_with
        self.fail_calls = fail_calls  # None: fail on every call; N: fail the first N calls
        self.returned_dimensions = returned_dimensions
        self.returned_count_delta = returned_count_delta
        self.calls = 0
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    def vector_for(self, text: str, *, dimensions: int | None = None) -> list[float]:
        size = dimensions or self.dimensions
        raw: list[float] = []
        counter = 0
        while len(raw) < size:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            raw.extend(byte / 255 - 0.5 for byte in digest)
            counter += 1
        values = raw[:size]
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]

    def _maybe_fail(self) -> None:
        self.calls += 1
        if self.fail_with is None:
            return
        if self.fail_calls is None or self.calls <= self.fail_calls:
            raise self.fail_with

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self._maybe_fail()
        self.document_calls.append(list(texts))
        vectors = [self.vector_for(text, dimensions=self.returned_dimensions) for text in texts]
        if self.returned_count_delta > 0:
            vectors.extend(vectors[:1] * self.returned_count_delta)
        elif self.returned_count_delta < 0:
            vectors = vectors[: self.returned_count_delta]
        return vectors

    def embed_query(self, text: str) -> list[float]:
        self._maybe_fail()
        self.query_calls.append(text)
        return self.vector_for(text, dimensions=self.returned_dimensions)
