"""DenseRetriever against PostgreSQL + pgvector with embeddings seeded from the fake embedder.

Courses are indexed through the real upload path (one chunk per short page); `chunk_embeddings`
rows are then inserted directly with controlled vectors, so ranking and the distance threshold are
exact. The retriever is called directly; no provider or model is involved.
"""

import math
from contextlib import contextmanager
from uuid import UUID, uuid4

import psycopg
import pytest

from docta_api.config import Settings
from docta_api.database import Database
from docta_api.dense_retriever import DenseRetriever
from docta_api.embedding import EmbeddingUnavailable
from docta_api.identity import AuthenticatedIdentity, DeterministicIdentityProvider
from docta_api.main import create_app
from docta_api.rag import RAGFailure
from tests.embedder_fakes import FakeEmbedder
from tests.integration.test_courses import _LifespanClient, _upgrade_database
from tests.integration.test_documents import _storage
from tests.integration.test_retrieval_recall import _index, _seed

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

THRESHOLD = 0.3
DIMS = 8
VERSION = FakeEmbedder(dimensions=DIMS).version
OTHER_VERSION = f"other-model|{DIMS}|cafebabe"
OTHER_DIMS_VERSION = "other-model|4|cafebabe"

TOPICS = (
    "fotosíntesis",
    "mitosis",
    "tectónica",
    "termodinámica",
    "estequiometría",
    "genética",
    "óptica",
)
PAGES = tuple(
    f"Apunte {number}. La {topic} es un tema del curso de ciencias. Este texto describe sus ideas "
    f"principales con ejemplos y ejercicios de práctica para el laboratorio número {number}."
    for number, topic in enumerate(TOPICS)
)
EXTRA_PAGES = (
    "Apunte extra uno. La geografía física describe relieves, ríos y climas de cada región del "
    "planeta con mapas y ejercicios de lectura para la clase semanal.",
    "Apunte extra dos. La historia económica compara talleres, mercados y rutas comerciales a lo "
    "largo de los siglos con tablas y cuestionarios breves para repasar.",
)

# Angle (radians) of each chunk's vector from the query axis, by ordinal; distance is 1 - cos.
ANGLES = {0: 0.9, 1: 0.1, 2: 0.5, 3: 0.1, 4: 0.3, 5: 0.7, 6: 0.2}
# Within THRESHOLD: ordinals 1,3 (tie), 6, 4, 2, 5; ordinal 0 is too far and 5 falls off the limit.
EXPECTED_ORDER = ("chunk-1", "chunk-3", "chunk-6", "chunk-4", "chunk-2")
QUESTION = "¿Qué describe el apunte?"


def _axis(angle: float, dims: int = DIMS) -> list[float]:
    return [math.cos(angle), math.sin(angle), *([0.0] * (dims - 2))]


QUERY = _axis(0.0)


class ScriptedEmbedder(FakeEmbedder):
    """Fake whose query vector is fixed by the test; `on_query` runs while the call is in flight."""

    def __init__(self, query_vector: list[float] | None = None, **kwargs) -> None:
        super().__init__(dimensions=DIMS, **kwargs)
        self.query_vector = QUERY if query_vector is None else query_vector
        self.on_query = None

    def embed_query(self, text: str) -> list[float]:
        self._maybe_fail()
        self.query_calls.append(text)
        if self.on_query:
            self.on_query()
        return list(self.query_vector)


@pytest.fixture
async def dense(ingestion_runtime):
    _upgrade_database()
    settings = Settings()
    suffix = uuid4().hex
    identities = {
        token: AuthenticatedIdentity("https://dense.test/", f"{token}-{suffix}")
        for token in ("teacher", "student")
    }
    provider = DeterministicIdentityProvider(identities)
    app = create_app(
        settings=settings, identity_provider=provider, object_storage=_storage(settings)
    )
    async with _LifespanClient(app) as client:
        student_user = uuid4()
        with psycopg.connect(str(settings.database_url)) as connection:
            connection.execute(
                "INSERT INTO users(id,oidc_issuer,oidc_subject) VALUES(%s,%s,%s)",
                (student_user, identities["student"].issuer, identities["student"].subject),
            )
        env = {
            "client": client,
            "app": app,
            "worker": ingestion_runtime,
            "settings": settings,
            "identity": identities["student"],
            "student_user": student_user,
        }
        env["a"] = await _seed(env, "Dense A", PAGES)
        env["b"] = await _seed(env, "Dense B", PAGES)
        yield env


def _connect(env):
    return psycopg.connect(str(env["settings"].database_url))


def _chunks(env, corpus_id: UUID) -> list[tuple[UUID, UUID, int]]:
    with _connect(env) as connection:
        rows = connection.execute(
            "SELECT id, course_id, ordinal FROM chunks WHERE corpus_version_id=%s ORDER BY ordinal",
            (corpus_id,),
        ).fetchall()
    return rows


def _seed_embeddings(env, corpus_id: UUID, vector_for, version: str = VERSION, skip=()) -> None:
    """Insert one embedding per chunk; `vector_for(ordinal)` gives the stored vector."""
    with _connect(env) as connection:
        for chunk_id, course_id, ordinal in _chunks(env, corpus_id):
            if ordinal in skip:
                continue
            vector = vector_for(ordinal)
            connection.execute(
                "INSERT INTO chunk_embeddings"
                "(chunk_id,course_id,corpus_version_id,embedding_version,dims,embedding) "
                "VALUES(%s,%s,%s,%s,%s,%s::vector)",
                (
                    chunk_id,
                    course_id,
                    corpus_id,
                    version,
                    len(vector),
                    "[" + ",".join(repr(value) for value in vector) + "]",
                ),
            )


def _ranked(ordinal: int) -> list[float]:
    return _axis(ANGLES.get(ordinal, 1.2))


def _exact(_ordinal: int) -> list[float]:
    return QUERY


def _retriever(env, embedder) -> DenseRetriever:
    return DenseRetriever(Database(str(env["settings"].database_url)), embedder, THRESHOLD)


def _accept(env, course):
    runtime = env["app"].state.conversation_runtime
    accepted = runtime.service.accept(
        env["identity"], course.conversation_id, QUESTION, f"q-{uuid4().hex}", "dense-test"
    )
    return accepted, runtime.service.request(accepted, ()).scope


@contextmanager
def _pending(env, course):
    """A real pending message whose scope the retriever authorizes; failed afterwards."""
    accepted, scope = _accept(env, course)
    assert scope.corpus_version_id == course.corpus_id
    try:
        yield scope
    finally:
        env["app"].state.conversation_runtime.service.fail(
            accepted.message.id, "PROCESSING_INTERRUPTED"
        )


def _fragments(evidence) -> tuple[str, ...]:
    return tuple(item.fragment for item in evidence)


async def test_order_follows_distance_then_ordinal_and_is_limited_to_five(dense):
    a = dense["a"]
    _seed_embeddings(dense, a.corpus_id, _ranked)
    embedder = ScriptedEmbedder()

    with _pending(dense, a) as scope:
        evidence = _retriever(dense, embedder).retrieve(scope, QUESTION)

    assert _fragments(evidence) == EXPECTED_ORDER
    assert all(item.corpus_version_id == a.corpus_id for item in evidence)
    assert all(item.course_id == a.course_id for item in evidence)
    assert all(item.document_version_id == a.version_id for item in evidence)
    assert {item.document_sha256 for item in evidence} == {a.pdf_sha256}
    assert embedder.calls == 1 and embedder.query_calls == [QUESTION]


async def test_other_course_and_other_embedding_version_are_never_returned(dense):
    a, b = dense["a"], dense["b"]
    _seed_embeddings(dense, a.corpus_id, _ranked)
    # Perfect matches that must stay invisible: another embedding version in the same corpus
    # and the same version in another course.
    _seed_embeddings(dense, a.corpus_id, _exact, version=OTHER_VERSION)
    _seed_embeddings(dense, b.corpus_id, _exact)

    with _pending(dense, a) as scope:
        evidence = _retriever(dense, ScriptedEmbedder()).retrieve(scope, QUESTION)

    assert _fragments(evidence) == EXPECTED_ORDER
    assert {item.corpus_version_id for item in evidence} == {a.corpus_id}
    assert {item.course_id for item in evidence} == {a.course_id}


async def test_inactive_corpus_of_the_same_course_is_never_returned(dense):
    a = dense["a"]
    _seed_embeddings(dense, a.corpus_id, _ranked)
    _, inactive, _ = await _index(dense, a.course_id, EXTRA_PAGES, activate=False)
    _seed_embeddings(dense, inactive, _exact)

    with _pending(dense, a) as scope:
        evidence = _retriever(dense, ScriptedEmbedder()).retrieve(scope, QUESTION)

    assert inactive != a.corpus_id
    assert _fragments(evidence) == EXPECTED_ORDER
    assert {item.corpus_version_id for item in evidence} == {a.corpus_id}


async def test_snapshot_corpus_is_used_even_if_another_corpus_is_activated_midway(dense):
    a = dense["a"]
    _seed_embeddings(dense, a.corpus_id, _ranked)
    accepted, scope = _accept(dense, a)
    try:
        _, newer, _ = await _index(dense, a.course_id, EXTRA_PAGES, version=1, activate=True)
        _seed_embeddings(dense, newer, _exact)

        evidence = _retriever(dense, ScriptedEmbedder()).retrieve(scope, QUESTION)
    finally:
        dense["app"].state.conversation_runtime.service.fail(
            accepted.message.id, "PROCESSING_INTERRUPTED"
        )

    assert newer != a.corpus_id
    assert _fragments(evidence) == EXPECTED_ORDER
    assert {item.corpus_version_id for item in evidence} == {a.corpus_id}


async def test_chunk_without_embedding_for_the_configured_version_is_index_missing(dense):
    a = dense["a"]
    embedder = ScriptedEmbedder()
    _seed_embeddings(dense, a.corpus_id, _ranked, skip=(3,))

    with _pending(dense, a) as scope, pytest.raises(RAGFailure) as partial:
        _retriever(dense, embedder).retrieve(scope, QUESTION)
    assert partial.value.code == "RETRIEVAL_INDEX_MISSING"

    # Embeddings of a different version do not count as an index for the configured one.
    _seed_embeddings(dense, a.corpus_id, _exact, version=OTHER_VERSION, skip=(1,))
    with _pending(dense, a) as scope, pytest.raises(RAGFailure) as foreign:
        _retriever(dense, embedder).retrieve(scope, QUESTION)
    assert foreign.value.code == "RETRIEVAL_INDEX_MISSING"


async def test_off_topic_question_returns_no_rows(dense):
    a = dense["a"]
    _seed_embeddings(dense, a.corpus_id, _ranked)
    embedder = ScriptedEmbedder(query_vector=_axis(math.pi))

    with _pending(dense, a) as scope:
        evidence = _retriever(dense, embedder).retrieve(scope, QUESTION)

    assert evidence == ()
    assert embedder.calls == 1


async def test_rows_of_other_dimensions_coexist_without_breaking_the_distance_operator(dense):
    a, b = dense["a"], dense["b"]
    _seed_embeddings(dense, a.corpus_id, _ranked)
    # Same chunks under another version and dimension count.
    _seed_embeddings(dense, a.corpus_id, lambda _o: _axis(0.0, 4), version=OTHER_DIMS_VERSION)
    # Same version string but 4 dimensions, in another course and in an inactive corpus.
    _seed_embeddings(dense, b.corpus_id, lambda _o: _axis(0.0, 4))
    _, inactive, _ = await _index(dense, a.course_id, EXTRA_PAGES, activate=False)
    _seed_embeddings(dense, inactive, lambda _o: _axis(0.0, 4))

    with _pending(dense, a) as scope:
        evidence = _retriever(dense, ScriptedEmbedder()).retrieve(scope, QUESTION)

    assert _fragments(evidence) == EXPECTED_ORDER


async def test_authorization_revoked_while_embedding_fails_the_search_session(dense):
    a = dense["a"]
    _seed_embeddings(dense, a.corpus_id, _exact)
    embedder = ScriptedEmbedder()

    def revoke() -> None:
        with _connect(dense) as connection:
            connection.execute(
                "DELETE FROM course_memberships WHERE course_id=%s AND user_id=%s",
                (a.course_id, dense["student_user"]),
            )

    embedder.on_query = revoke
    with _pending(dense, a) as scope, pytest.raises(RAGFailure) as error:
        _retriever(dense, embedder).retrieve(scope, QUESTION)

    assert error.value.code == "RETRIEVAL_SCOPE_INVALID"
    assert embedder.calls == 1


async def test_embedding_failure_or_wrong_dimensions_fail_closed_after_a_single_call(dense):
    a = dense["a"]
    _seed_embeddings(dense, a.corpus_id, _exact)
    for embedder in (
        ScriptedEmbedder(fail_with=EmbeddingUnavailable()),
        ScriptedEmbedder(query_vector=_axis(0.0, 4)),
    ):
        with _pending(dense, a) as scope, pytest.raises(RAGFailure) as error:
            _retriever(dense, embedder).retrieve(scope, QUESTION)
        assert error.value.code == "RETRIEVAL_UNAVAILABLE"
        assert embedder.calls == 1
