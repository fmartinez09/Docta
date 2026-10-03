"""Retrieval recall controls C0-C6 (ledger: any-term FTS recall with lexeme coverage floor).

The corpus is original synthetic Spanish text rendered to a real PDF in the test, uploaded and
indexed through the real path. The retriever is called directly; no model or provider is used.
Every question is accepted as a real pending message so the retriever's authorization recheck
passes, then failed to free the conversation.
"""

import re
import textwrap
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

import psycopg
import pymupdf
import pytest

from docta_api.config import Settings
from docta_api.identity import AuthenticatedIdentity, DeterministicIdentityProvider
from docta_api.main import create_app
from tests.integration.test_conversations import headers
from tests.integration.test_courses import _LifespanClient, _upgrade_database
from tests.integration.test_documents import (
    _create_course,
    _create_upload,
    _finish_ingestion,
    _put_direct,
    _storage,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

TOP = 5

# One chunk per page (pages are far shorter than the chunk size). The trailing ZETA* word is a
# unique literal marker used to identify each chunk in the database.
PAGES = (
    # page 1: enzymes; shares the common word "velocidad" with page 2 (C3 distractor)
    "Las enzimas son proteínas que aceleran las reacciones químicas dentro de una célula. "
    "La velocidad de una reacción enzimática depende de la temperatura, del pH y de la "
    "concentración del sustrato. Si el calor es excesivo, la enzima pierde su forma y la "
    "velocidad cae de manera brusca. Algunas enzimas necesitan un cofactor metálico para "
    "funcionar. En el laboratorio de bioquímica se mide la reacción con un espectrofotómetro, "
    "registrando el cambio de color cada diez segundos. Código interno ZETAUNO.",
    # page 2: Newton's second law
    "En mecánica clásica se estudia cómo se mueven los cuerpos bajo la acción de fuerzas. "
    "La segunda ley de Newton establece que la aceleración de un carrito de laboratorio es "
    "proporcional a la fuerza neta aplicada e inversamente proporcional a su masa. Para "
    "comprobarlo, el grupo coloca un carrito sobre un riel horizontal y lo conecta con un "
    "hilo a una pesa pequeña. Al soltar la pesa, un sensor registra la posición en cada "
    "instante. La velocidad del carrito crece de modo uniforme mientras actúa la fuerza. "
    "Código interno ZETADOS.",
    # page 3: cell energy
    "Las células obtienen energía de dos procesos complementarios. La fotosíntesis ocurre en "
    "los cloroplastos, donde la clorofila absorbe luz roja y azul para transformar agua y "
    "dióxido de carbono en glucosa y oxígeno. La glucosa almacena esa energía en enlaces "
    "químicos. En la respiración, la glucosa se degrada paso a paso y la energía liberada se "
    "guarda en una molécula portadora llamada ATP. Código interno ZETATRES.",
    # page 4: invented person and instrument (C6 entity)
    "En 1931 la ingeniera Veltrana Okumbe diseñó un manómetro de mercurio para medir la "
    "presión de los gases en el taller. El aparato usa un tubo en forma de U: una rama queda "
    "abierta al aire y la otra conectada al recipiente. La diferencia de altura entre ambas "
    "columnas indica la presión relativa. Código interno ZETACUATRO.",
)
MARKERS = ("ZETAUNO", "ZETADOS", "ZETATRES", "ZETACUATRO")

# C5: every lexeme is present in page 2 (asserted in setup). C0 adds one absent word.
EXACT = "Aceleración de un carrito de laboratorio bajo la fuerza neta aplicada y la masa"
EXACT_PLUS_ABSENT = EXACT + " y la ecuación"
# C1: partial vocabulary on page 3, one absent word ("diagrama").
PARTIAL = "La clorofila absorbe luz roja y azul en la fotosíntesis del diagrama"
# C3: page 2 has most lexemes; page 1 shares only "velocidad"; one absent word ("fórmula").
DISTRACTOR = "Velocidad del carrito sobre el riel horizontal con la pesa pequeña y la fórmula"
OFF_TOPIC = "¿Quién ganó el campeonato mundial de ajedrez en Reikiavik?"
# C6: "Veltrane Okumbi" misspell the page 4 name; only "diseñó" and "ingeniera" are present.
MISSPELLED = "Instrumento diseñó la ingeniera Veltrane Okumbi"


def _pdf(pages) -> bytes:
    document = pymupdf.open()
    try:
        for text in pages:
            page = document.new_page()
            page.insert_text((50, 60), "\n".join(textwrap.wrap(text, 90)), fontsize=9)
        return document.tobytes()
    finally:
        document.close()


@dataclass(frozen=True)
class Course:
    course_id: UUID
    corpus_id: UUID
    version_id: UUID
    conversation_id: UUID
    pdf_sha256: str


async def _index(env, course_id, pages, *, version=0, activate=True):
    """Upload through the real path, run the worker, optionally activate; return JSON + bytes."""
    client, runtime = env["client"], env["worker"]
    content = _pdf(pages)
    tag = uuid4().hex
    upload = await _create_upload(client, course_id, "teacher", f"up-{tag}", content)
    await _put_direct(upload, content)
    confirmation = await client.post(
        f"/api/v1/courses/{course_id}/documents/{upload['document_id']}/versions/"
        f"{upload['version_id']}/complete",
        headers=headers("teacher", f"confirm-{tag}"),
    )
    done = await _finish_ingestion(client, confirmation, "teacher", runtime)
    assert done.json()["state"] == "INDEXED"
    corpus = done.json()["corpus_version_id"]
    if activate:
        activation = await client.post(
            f"/api/v1/courses/{course_id}/corpus/activate",
            json={"corpus_version_id": corpus, "expected_course_version": version},
            headers=headers("teacher"),
        )
        assert activation.status_code == 200
    return UUID(upload["version_id"]), UUID(corpus), content


async def _seed(env, title, pages) -> Course:
    client, settings = env["client"], env["settings"]
    course_id = await _create_course(client, "teacher", title)
    with psycopg.connect(str(settings.database_url)) as connection:
        connection.execute(
            "INSERT INTO course_memberships(course_id,user_id,role) VALUES(%s,%s,'student')",
            (course_id, env["student_user"]),
        )
    version, corpus, content = await _index(env, course_id, pages)
    conversation = await client.post(
        f"/api/v1/courses/{course_id}/conversations",
        headers=headers("student", f"conv-{uuid4().hex}"),
    )
    assert conversation.status_code == 201
    return Course(
        course_id, corpus, version, UUID(conversation.json()["id"]), sha256(content).hexdigest()
    )


@pytest.fixture
async def recall(ingestion_runtime):
    _upgrade_database()
    settings = Settings()
    suffix = uuid4().hex
    identities = {
        token: AuthenticatedIdentity("https://recall.test/", f"{token}-{suffix}")
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
        env["a"] = await _seed(env, "Recall A", PAGES)
        env["b"] = await _seed(env, "Recall B", PAGES)
        yield env


def _retrieve(env, course: Course, question: str):
    runtime = env["app"].state.conversation_runtime
    accepted = runtime.service.accept(
        env["identity"], course.conversation_id, question, f"q-{uuid4().hex}", "recall-test"
    )
    try:
        request = runtime.service.request(accepted, ())
        assert request.scope.corpus_version_id == course.corpus_id
        return runtime.retriever.retrieve(request.scope, question)
    finally:
        runtime.service.fail(accepted.message.id, "PROCESSING_INTERRUPTED")


def _connect(env):
    return psycopg.connect(str(env["settings"].database_url))


def _query_lexemes(env, question: str) -> set[str]:
    with _connect(env) as connection:
        text = connection.execute("SELECT plainto_tsquery('spanish', %s)::text", (question,))
        return set(re.findall(r"'([^']+)'", text.fetchone()[0]))


def _chunk_lexemes(env, chunk_id: UUID) -> set[str]:
    with _connect(env) as connection:
        row = connection.execute(
            "SELECT tsvector_to_array(search_vector) FROM chunks WHERE id=%s", (chunk_id,)
        ).fetchone()
        return set(row[0])


def _chunk_id(env, course: Course, marker: str) -> UUID:
    with _connect(env) as connection:
        rows = connection.execute(
            "SELECT id FROM chunks WHERE corpus_version_id=%s AND content LIKE %s",
            (course.corpus_id, f"%{marker}%"),
        ).fetchall()
    assert len(rows) == 1
    return rows[0][0]


def _threshold(count: int) -> int:
    return -(-2 * count // 3)


def _coverage(env, course: Course, question: str) -> dict[str, int]:
    query = _query_lexemes(env, question)
    return {
        marker: len(query & _chunk_lexemes(env, _chunk_id(env, course, marker)))
        for marker in MARKERS
    }


def _ids(evidence) -> list[UUID]:
    return [item.chunk_id for item in evidence]


async def test_c0_one_absent_content_word_still_retrieves_target_in_top_five(recall):
    """C0: exact question plus one absent content word retrieves the target (RED under AND)."""
    a = recall["a"]
    base = _query_lexemes(recall, EXACT)
    query = _query_lexemes(recall, EXACT_PLUS_ABSENT)
    target = _chunk_id(recall, a, "ZETADOS")
    present = query & _chunk_lexemes(recall, target)
    assert len(query) == len(base) + 1 and present == base
    assert len(present) >= _threshold(len(query))
    assert _chunk_id(recall, a, "ZETADOS") in _ids(_retrieve(recall, a, EXACT_PLUS_ABSENT))[:TOP]


async def test_c1_partial_vocabulary_question_retrieves_its_chunk(recall):
    """C1: >=3 lexemes with exactly one absent still retrieves the page 3 chunk (RED)."""
    a = recall["a"]
    query = _query_lexemes(recall, PARTIAL)
    target = _chunk_id(recall, a, "ZETATRES")
    present = query & _chunk_lexemes(recall, target)
    assert len(query) >= 3 and len(query - present) == 1
    assert len(present) >= _threshold(len(query))
    assert target in _ids(_retrieve(recall, a, PARTIAL))[:TOP]


async def test_c2_off_topic_question_returns_no_evidence(recall):
    """C2: a question with no course evidence returns an empty tuple."""
    assert max(_coverage(recall, recall["a"], OFF_TOPIC).values()) == 0
    assert _retrieve(recall, recall["a"], OFF_TOPIC) == ()


async def test_c3_distractor_sharing_one_common_word_does_not_outrank_target(recall):
    """C3: page 1 shares only "velocidad"; page 2 must be rank 1 and page 1 not returned (RED).

    Red today only because the question contains one absent word (AND returns zero rows).
    """
    a = recall["a"]
    query = _query_lexemes(recall, DISTRACTOR)
    target = _chunk_id(recall, a, "ZETADOS")
    distractor = _chunk_id(recall, a, "ZETAUNO")
    shared = query & _chunk_lexemes(recall, distractor)
    assert len(shared) == 1 and len(query) >= 3
    assert len(query & _chunk_lexemes(recall, target)) >= _threshold(len(query))
    ids = _ids(_retrieve(recall, a, DISTRACTOR))
    assert ids[:1] == [target]
    assert distractor not in ids


async def test_c4_pure_isolation_exact_question_stays_inside_asking_course_snapshot(recall):
    """C4 (isolation, passes today): results never leave the asking course or its snapshot."""
    a, b = recall["a"], recall["b"]
    # A second, inactive corpus version of course A with the same text must stay invisible.
    inactive_version, inactive_corpus, _ = await _index(
        recall, a.course_id, PAGES, version=1, activate=False
    )
    assert inactive_corpus != a.corpus_id
    for asking, other in ((a, b), (b, a)):
        evidence = _retrieve(recall, asking, EXACT)
        assert evidence
        assert {e.course_id for e in evidence} == {asking.course_id}
        assert {e.corpus_version_id for e in evidence} == {asking.corpus_id}
        assert {e.document_version_id for e in evidence} == {asking.version_id}
        assert not {e.chunk_id for e in evidence} & {
            _chunk_id(recall, other, marker) for marker in MARKERS
        }
    assert inactive_version not in {e.document_version_id for e in _retrieve(recall, a, EXACT)}


async def test_c4_positive_absent_word_question_retrieves_only_own_course_chunks(recall):
    """C4 (any-term path, RED today): each course retrieves its own target and none of the other."""
    a, b = recall["a"], recall["b"]
    for asking, other in ((a, b), (b, a)):
        evidence = _retrieve(recall, asking, EXACT_PLUS_ABSENT)
        assert _chunk_id(recall, asking, "ZETADOS") in _ids(evidence)[:TOP]
        assert {e.course_id for e in evidence} == {asking.course_id}
        assert {e.corpus_version_id for e in evidence} == {asking.corpus_id}
        assert not {e.chunk_id for e in evidence} & {
            _chunk_id(recall, other, marker) for marker in MARKERS
        }


async def test_c5_exact_question_keeps_rank_one_and_provenance(recall):
    """C5: every lexeme present (asserted); rank 1 and unchanged provenance fields."""
    a = recall["a"]
    target = _chunk_id(recall, a, "ZETADOS")
    query = _query_lexemes(recall, EXACT)
    assert query and query <= _chunk_lexemes(recall, target)
    evidence = _retrieve(recall, a, EXACT)
    assert evidence[0].chunk_id == target
    with _connect(recall) as connection:
        ordinal, start, end, content, content_hash, title, object_sha = connection.execute(
            "SELECT c.ordinal, c.page_start, c.page_end, c.content, c.content_hash,"
            " d.title, v.object_sha256 FROM chunks c"
            " JOIN document_versions v ON v.id=c.document_version_id"
            " JOIN documents d ON d.id=v.document_id WHERE c.id=%s",
            (target,),
        ).fetchone()
    first = evidence[0]
    assert (first.page_start, first.page_end) == (2, 2) == (start, end)
    assert first.fragment == f"chunk-{ordinal}"
    assert first.content == content
    assert first.content_hash == content_hash == sha256(content.encode()).hexdigest()
    assert first.document_title == title
    assert first.document_sha256 == object_sha == a.pdf_sha256
    assert first.course_id == a.course_id
    assert first.corpus_version_id == a.corpus_id
    assert first.document_version_id == a.version_id


async def test_c6_misspelled_entity_below_coverage_floor_returns_no_evidence(recall):
    """C6: one misspelled entity; coverage below 2/3 (but above zero) returns an empty tuple."""
    a = recall["a"]
    query = _query_lexemes(recall, MISSPELLED)
    coverage = _coverage(recall, a, MISSPELLED)
    assert len(query) >= 4 and 0 < coverage["ZETACUATRO"] < _threshold(len(query))
    assert max(coverage.values()) < _threshold(len(query))
    assert _retrieve(recall, a, MISSPELLED) == ()


async def test_evidence_is_capped_at_five_with_unique_chunks(recall):
    """Broad question over 7 matching chunks returns exactly 5 unique chunks (RED today)."""
    pages = [
        f"La energía de un sistema se relaciona con el calor y la temperatura. Ejemplo {word}: "
        f"{word} aparece solo en esta página. Código interno ZETAMUCHOS{index}."
        for index, word in enumerate(
            ("guitarra", "montaña", "cascada", "bosque", "desierto", "volcán", "glaciar")
        )
    ]
    many = await _seed(recall, "Recall many", pages)
    question = "Relación entre la energía, el calor, la temperatura y el teorema"
    query = _query_lexemes(recall, question)
    with _connect(recall) as connection:
        rows = connection.execute(
            "SELECT tsvector_to_array(search_vector) FROM chunks WHERE corpus_version_id=%s",
            (many.corpus_id,),
        ).fetchall()
    assert sum(len(query & set(row[0])) >= _threshold(len(query)) for row in rows) == 7
    evidence = _retrieve(recall, many, question)
    assert len(evidence) == TOP
    assert len(set(_ids(evidence))) == len(evidence)
    assert {e.corpus_version_id for e in evidence} == {many.corpus_id}


SPECIAL_CHARS = "' \\ \" & | ! : ( ) <-> *"
SPECIAL_QUESTION = (
    "Aceleración del carrito (laboratorio) & fuerza | neta! aplicada: masa <-> riel * hilo, "
    "pesa \"sensor\" 'cuerpo' \\ " + "'; DROP TABLE chunks; --"
)
SPECIAL_ONLY = SPECIAL_CHARS + " ; -- de la y el que"


def _chunk_count(env) -> int:
    with _connect(env) as connection:
        return connection.execute("SELECT count(*) FROM chunks").fetchone()[0]


async def test_c7_special_characters_do_not_break_or_widen_the_query(recall):
    """C7: operators/quotes/SQL text are inert; real lexemes still retrieve; no lexemes, no rows."""
    a = recall["a"]
    target = _chunk_id(recall, a, "ZETADOS")
    query = _query_lexemes(recall, SPECIAL_QUESTION)
    present = query & _chunk_lexemes(recall, target)
    assert len(query - present) >= 1 and len(present) >= _threshold(len(query))
    assert _query_lexemes(recall, SPECIAL_ONLY) == set()
    before = _chunk_count(recall)
    assert target in _ids(_retrieve(recall, a, SPECIAL_QUESTION))[:TOP]
    assert _retrieve(recall, a, SPECIAL_ONLY) == ()
    assert _chunk_count(recall) == before
    assert _chunk_count(recall) > 0
