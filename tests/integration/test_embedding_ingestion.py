import logging

import pytest
from sqlalchemy import func, select

from docta_api import ingestion
from docta_api.embedding import EmbeddingInvalid, EmbeddingUnavailable
from docta_api.models import Chunk, ChunkEmbedding, CorpusVersionDocument
from tests.embedder_fakes import FakeEmbedder
from tests.integration.test_jobs import _artifact_count, _expire, _job, queued_pdf  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

API_KEY = "sk-test-secret-key-0123456789"


def _install(resource, embedder):
    resource["runtime"].worker._embedder = embedder
    return embedder


def _counts(resource):
    envelope = resource["envelope"]
    with resource["runtime"].database.session() as session:
        corpora = session.scalar(
            select(func.count(CorpusVersionDocument.corpus_version_id)).where(
                CorpusVersionDocument.document_version_id == envelope.document_version_id
            )
        )
        embeddings = session.scalar(
            select(func.count(ChunkEmbedding.chunk_id)).join(
                Chunk,
                (Chunk.id == ChunkEmbedding.chunk_id)
                & (Chunk.document_version_id == envelope.document_version_id),
            )
        )
    return corpora, embeddings


async def test_ready_corpus_has_one_embedding_per_chunk(queued_pdf):  # noqa: F811
    r = queued_pdf
    embedder = _install(r, FakeEmbedder(dimensions=8))
    assert r["runtime"].worker.process(r["envelope"])
    job = _job(r)
    assert job.state == "SUCCEEDED"
    assert job.failure_code is None
    chunk_count = _artifact_count(r)
    assert chunk_count >= 1
    assert _counts(r) == (1, chunk_count)
    assert sum(len(batch) for batch in embedder.document_calls) == chunk_count
    envelope = r["envelope"]
    with r["runtime"].database.session() as session:
        job_corpora = select(CorpusVersionDocument.corpus_version_id).where(
            CorpusVersionDocument.document_version_id == envelope.document_version_id
        )
        rows = session.execute(
            select(ChunkEmbedding.embedding_version, ChunkEmbedding.dims).where(
                ChunkEmbedding.corpus_version_id.in_(job_corpora)
            )
        ).all()
    mine = [row for row in rows if row.embedding_version == embedder.version]
    assert len(mine) == chunk_count
    assert {row.dims for row in mine} == {8}


async def test_transient_embedder_failure_retries_then_succeeds(queued_pdf):  # noqa: F811
    r = queued_pdf
    embedder = _install(r, FakeEmbedder(fail_with=EmbeddingUnavailable(), fail_calls=1))
    assert r["runtime"].worker.process(r["envelope"])
    job = _job(r)
    assert job.state == "PENDING"
    assert job.failure_code == "EMBEDDING_UNAVAILABLE"
    assert _counts(r) == (0, 0)
    _expire(r)  # the retry jitter puts next_attempt_at in the future
    assert r["runtime"].worker.process(r["envelope"])
    job = _job(r)
    assert job.state == "SUCCEEDED"
    assert job.failure_code is None
    assert job.attempt == 2
    assert embedder.calls == 2
    assert _counts(r) == (1, _artifact_count(r))


async def test_persistent_embedder_outage_ends_failed_without_rows(queued_pdf):  # noqa: F811
    r = queued_pdf
    _install(r, FakeEmbedder(fail_with=EmbeddingUnavailable()))
    for attempt in range(1, 4):
        assert r["runtime"].worker.process(r["envelope"])
        job = _job(r)
        assert job.attempt == attempt
        assert job.state == ("PENDING" if attempt < 3 else "FAILED")
        _expire(r)
    assert job.failure_code == "EMBEDDING_UNAVAILABLE"
    assert _counts(r) == (0, 0)
    assert _artifact_count(r) == 0


async def test_exhausted_attempts_never_call_the_embedder(queued_pdf):  # noqa: F811
    r = queued_pdf
    worker = r["runtime"].worker
    embedder = _install(r, FakeEmbedder())
    for _ in range(3):
        assert worker._claim(r["envelope"]) is not None
        _expire(r)
    assert worker.process(r["envelope"])
    assert _job(r).state == "FAILED"
    assert _job(r).failure_code == "INGESTION_ATTEMPTS_EXHAUSTED"
    assert embedder.calls == 0
    assert _counts(r) == (0, 0)


@pytest.mark.parametrize(
    "embedder",
    [
        FakeEmbedder(dimensions=8, returned_dimensions=4),
        FakeEmbedder(returned_count_delta=1),
        FakeEmbedder(fail_with=EmbeddingInvalid()),
    ],
    ids=["wrong-dims", "wrong-count", "provider-invalid"],
)
async def test_invalid_embeddings_fail_permanently(queued_pdf, embedder):  # noqa: F811
    r = queued_pdf
    _install(r, embedder)
    assert r["runtime"].worker.process(r["envelope"])
    job = _job(r)
    assert job.state == "FAILED"
    assert job.failure_code == "EMBEDDING_INVALID"
    assert job.attempt == 1
    assert _counts(r) == (0, 0)
    assert _artifact_count(r) == 0


async def test_non_finite_vector_is_invalid(queued_pdf):  # noqa: F811
    r = queued_pdf

    class NonFinite(FakeEmbedder):
        def embed_documents(self, texts):
            return [[float("nan")] * self.dimensions for _ in texts]

    _install(r, NonFinite())
    assert r["runtime"].worker.process(r["envelope"])
    assert _job(r).state == "FAILED"
    assert _job(r).failure_code == "EMBEDDING_INVALID"
    assert _counts(r) == (0, 0)


async def test_without_embedder_corpus_is_ready_with_no_embeddings(queued_pdf):  # noqa: F811
    r = queued_pdf
    _install(r, None)
    assert r["runtime"].worker.process(r["envelope"])
    assert _job(r).state == "SUCCEEDED"
    assert _counts(r) == (1, 0)
    assert _artifact_count(r) >= 1


async def test_failure_while_inserting_embeddings_rolls_back_everything(
    queued_pdf,  # noqa: F811
    monkeypatch,
):
    r = queued_pdf
    _install(r, FakeEmbedder())

    def broken(**kwargs):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(ingestion, "ChunkEmbedding", broken)
    assert r["runtime"].worker.process(r["envelope"])
    job = _job(r)
    assert job.state == "FAILED"
    assert job.failure_code == "DOCUMENT_PROCESSING_FAILED"
    assert _counts(r) == (0, 0)
    assert _artifact_count(r) == 0


async def test_logs_never_contain_text_vectors_or_credentials(queued_pdf, caplog):  # noqa: F811
    r = queued_pdf
    embedder = _install(
        r, FakeEmbedder(fail_with=EmbeddingUnavailable(API_KEY), fail_calls=1)
    )
    caplog.set_level(logging.DEBUG)
    assert r["runtime"].worker.process(r["envelope"])
    _expire(r)
    assert r["runtime"].worker.process(r["envelope"])
    assert _job(r).state == "SUCCEEDED"
    with r["runtime"].database.session() as session:
        stored = session.scalars(
            select(Chunk.content).where(
                Chunk.document_version_id == r["envelope"].document_version_id
            )
        ).all()
    assert stored
    assert caplog.records
    assert API_KEY not in caplog.text
    assert all(text not in caplog.text for text in stored)
    assert all(str(embedder.vector_for(text)[0]) not in caplog.text for text in stored)
