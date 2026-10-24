"""Conversation runtime with the dense retriever: safe failures, abstention, version, logs.

The real application is wired with a DenseRetriever over the fake embedder and the shared
RecordingTutor; embeddings of the indexed course are seeded directly with that fake.
"""

import logging
from contextlib import asynccontextmanager

import psycopg
import pytest

from docta_api.database import Database
from docta_api.dense_retriever import DenseRetriever
from docta_api.embedding import EmbeddingUnavailable
from docta_api.main import create_app
from tests.embedder_fakes import FakeEmbedder
from tests.integration.test_conversations import (
    RecordingTutor,
    ask,
    events,
    headers,
)
from tests.integration.test_conversations import chat as base_chat  # noqa: F401
from tests.integration.test_courses import _LifespanClient

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

THRESHOLD = 0.05
QUESTION = "velocidad"


@pytest.fixture
async def chat(base_chat):  # noqa: F811
    """The shared two-course conversation fixture (real ingestion, FTS app, RecordingTutor)."""
    return base_chat


def _seed_embeddings(chat, embedder, text: str) -> None:
    """Embed every chunk of the course A corpus as `text`, so that question `text` matches at 0."""
    corpus = chat["corpora"][chat["a"]]
    vector = "[" + ",".join(repr(value) for value in embedder.vector_for(text)) + "]"
    with psycopg.connect(str(chat["settings"].database_url)) as connection:
        rows = connection.execute(
            "SELECT id, course_id FROM chunks WHERE corpus_version_id=%s", (corpus,)
        ).fetchall()
        assert rows
        for chunk_id, course_id in rows:
            connection.execute(
                "INSERT INTO chunk_embeddings"
                "(chunk_id,course_id,corpus_version_id,embedding_version,dims,embedding) "
                "VALUES(%s,%s,%s,%s,%s,%s::vector)",
                (chunk_id, course_id, corpus, embedder.version, embedder.dimensions, vector),
            )


@asynccontextmanager
async def _dense_client(chat, embedder, tutor):
    retriever = DenseRetriever(Database(str(chat["settings"].database_url)), embedder, THRESHOLD)
    app = create_app(
        settings=chat["settings"],
        identity_provider=chat["provider"],
        tutor_model=tutor,
        retriever=retriever,
    )
    async with _LifespanClient(app) as client:
        yield client


async def _ask(client, chat, question, key):
    response = await client.post(
        f"/api/v1/conversations/{chat['conversation']}/messages",
        json={"question": question},
        headers=headers("student", key),
    )
    return events(response)[-1][1]


def _message(chat, message_id):
    with psycopg.connect(str(chat["settings"].database_url)) as connection:
        return connection.execute(
            "SELECT question, state, failure_code, retrieval_config_version "
            "FROM messages WHERE id=%s",
            (message_id,),
        ).fetchone()


async def test_unavailable_embedding_fails_safely_after_one_call_keeping_the_question(chat):
    embedder = FakeEmbedder(fail_with=EmbeddingUnavailable("provider down"))
    tutor = RecordingTutor()
    async with _dense_client(chat, embedder, tutor) as client:
        terminal = await _ask(client, chat, QUESTION, "dense-unavailable")

    assert terminal["state"] == "failed" and terminal["failure_code"] == "RETRIEVAL_UNAVAILABLE"
    assert embedder.calls == 1
    assert tutor.requests == []
    question, state, code, _version = _message(chat, terminal["id"])
    assert (question, state, code) == (QUESTION, "failed", "RETRIEVAL_UNAVAILABLE")


async def test_missing_index_fails_safely_without_falling_back_or_calling_the_model(chat):
    embedder = FakeEmbedder()
    tutor = RecordingTutor()
    async with _dense_client(chat, embedder, tutor) as client:
        terminal = await _ask(client, chat, QUESTION, "dense-index-missing")

    assert terminal["state"] == "failed" and terminal["failure_code"] == "RETRIEVAL_INDEX_MISSING"
    assert embedder.calls == 1
    assert tutor.requests == []
    assert _message(chat, terminal["id"])[0] == QUESTION


async def test_no_close_chunk_abstains_without_calling_the_model(chat):
    embedder = FakeEmbedder()
    _seed_embeddings(chat, embedder, QUESTION)
    tutor = RecordingTutor()
    async with _dense_client(chat, embedder, tutor) as client:
        terminal = await _ask(client, chat, "ajedrez en Reikiavik", "dense-abstain")

    assert terminal["state"] == "completed" and terminal["response"]["mode"] == "abstain"
    assert terminal["response"]["citations"] == []
    assert embedder.calls == 1
    assert tutor.requests == []


async def test_dense_turn_persists_the_real_dense_retrieval_version(chat):
    embedder = FakeEmbedder()
    _seed_embeddings(chat, embedder, QUESTION)
    tutor = RecordingTutor()
    async with _dense_client(chat, embedder, tutor) as client:
        terminal = await _ask(client, chat, QUESTION, "dense-version")

    assert terminal["state"] == "completed" and terminal["response"]["grounded"]
    assert len(tutor.requests) == 1
    assert _message(chat, terminal["id"])[3] == (
        f"dense-cosine-top5-d{THRESHOLD}-{embedder.version}"
    )


async def test_fts_turn_persists_the_fts_retrieval_version(chat):
    terminal = events(await ask(chat, "velocidad", key="fts-version"))[-1][1]

    assert terminal["state"] == "completed"
    assert _message(chat, terminal["id"])[3] == "spanish-fts-coverage-top5-v2"


async def test_logs_never_contain_the_question_vector_or_keys(chat, caplog):
    caplog.set_level(logging.DEBUG)
    question = "consulta privada sobre velocidad zx81"
    key = "idempotency-sentinel-7731"
    secret = "provider-secret-sentinel-5521"
    embedder = FakeEmbedder()
    _seed_embeddings(chat, embedder, question)
    vector = embedder.vector_for(question)
    failing = FakeEmbedder(fail_with=EmbeddingUnavailable(secret))
    async with _dense_client(chat, embedder, RecordingTutor()) as client:
        completed = await _ask(client, chat, question, key)
    async with _dense_client(chat, failing, RecordingTutor()) as client:
        failed = await _ask(client, chat, question + " otra", key + "-2")

    assert completed["state"] == "completed"
    assert failed["failure_code"] == "RETRIEVAL_UNAVAILABLE"
    for sentinel in (question, key, secret, repr(vector[0]), repr(vector[1])):
        assert sentinel not in caplog.text
