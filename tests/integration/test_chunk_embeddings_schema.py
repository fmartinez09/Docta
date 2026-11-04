"""Schema of chunk_embeddings: pgvector extension, constraints, cascade and migration symmetry."""

from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import errors

from docta_api.config import Settings

pytestmark = pytest.mark.integration

PREVIOUS_REVISION = "20260907_0008"
HEAD_REVISION = "20261023_0009"


def _alembic() -> Config:
    return Config("apps/api/alembic.ini")


@pytest.fixture
def connection():
    command.upgrade(_alembic(), "head")
    with psycopg.connect(str(Settings().database_url), autocommit=True) as conn:
        yield conn


def _seed_chunk(conn, corpus=None, course=None) -> tuple:
    """Insert a minimal course, corpus version and chunk; return (course, corpus, chunk)."""
    course = course or uuid4()
    corpus = corpus or uuid4()
    user, document, version, chunk = uuid4(), uuid4(), uuid4(), uuid4()
    conn.execute(
        "INSERT INTO users (id, oidc_issuer, oidc_subject) VALUES (%s, 'https://schema.test/', %s)",
        (user, f"user-{user}"),
    )
    conn.execute("INSERT INTO courses (id, title) VALUES (%s, 'Schema course')", (course,))
    conn.execute(
        "INSERT INTO corpus_versions (id, course_id, state, pipeline_version, page_count,"
        " chunk_count) VALUES (%s, %s, 'READY', 'test', 1, 1)",
        (corpus, course),
    )
    conn.execute(
        "INSERT INTO documents (id, course_id, title, created_by_user_id)"
        " VALUES (%s, %s, 'Doc', %s)",
        (document, course, user),
    )
    conn.execute(
        "INSERT INTO document_versions (id, course_id, document_id, storage_key,"
        " original_filename, expected_size_bytes, expected_sha256, media_type, state,"
        " pipeline_version, upload_idempotency_key, upload_request_hash)"
        " VALUES (%s, %s, %s, %s, 'a.pdf', 1, %s, 'application/pdf', 'INDEXED', 'test', %s, %s)",
        (version, course, document, f"key/{version}", "0" * 64, f"idem-{version}", "0" * 64),
    )
    conn.execute(
        "INSERT INTO chunks (id, course_id, corpus_version_id, document_version_id,"
        " page_start, page_end, ordinal, content, content_hash)"
        " VALUES (%s, %s, %s, %s, 1, 1, 0, 'texto', %s)",
        (chunk, course, corpus, version, "1" * 64),
    )
    return course, corpus, chunk


def _insert_embedding(conn, scope, version, dims, vector=None):
    course, corpus, chunk = scope
    vector = vector or "[" + ",".join("0.5" for _ in range(dims)) + "]"
    conn.execute(
        "INSERT INTO chunk_embeddings (chunk_id, course_id, corpus_version_id,"
        " embedding_version, dims, embedding) VALUES (%s, %s, %s, %s, %s, %s::vector)",
        (chunk, course, corpus, version, dims, vector),
    )


def _count(conn, chunk) -> int:
    row = conn.execute(
        "SELECT count(*) FROM chunk_embeddings WHERE chunk_id = %s", (chunk,)
    ).fetchone()
    return row[0]


def test_upgrade_installs_extension_and_table(connection) -> None:
    extension = connection.execute(
        "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
    ).fetchone()
    assert extension is not None
    revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert revision == (HEAD_REVISION,)
    column = connection.execute(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute"
        " WHERE attrelid = 'chunk_embeddings'::regclass AND attname = 'embedding'"
    ).fetchone()
    assert column == ("vector",)
    length = connection.execute(
        "SELECT character_maximum_length FROM information_schema.columns"
        " WHERE table_name = 'chunk_embeddings' AND column_name = 'embedding_version'"
    ).fetchone()
    assert length == (200,)


def test_dims_must_match_vector_and_be_positive(connection) -> None:
    scope = _seed_chunk(connection)
    with pytest.raises(errors.CheckViolation):
        _insert_embedding(connection, scope, "model|3|aaaaaaaa", dims=4, vector="[1,2,3]")
    with pytest.raises(errors.CheckViolation):
        _insert_embedding(connection, scope, "model|0|aaaaaaaa", dims=0, vector="[1]")
    assert _count(connection, scope[2]) == 0


def test_foreign_key_rejects_cross_scope_embedding(connection) -> None:
    course, corpus, chunk = _seed_chunk(connection)
    other_course, other_corpus, _ = _seed_chunk(connection)
    for wrong in ((other_course, corpus, chunk), (course, other_corpus, chunk)):
        with pytest.raises(errors.ForeignKeyViolation):
            _insert_embedding(connection, wrong, "model|3|aaaaaaaa", dims=3)
    assert _count(connection, chunk) == 0


def test_primary_key_is_per_version_and_allows_different_dims(connection) -> None:
    scope = _seed_chunk(connection)
    _insert_embedding(connection, scope, "model-a|3|aaaaaaaa", dims=3)
    _insert_embedding(connection, scope, "model-b|5|bbbbbbbb", dims=5)
    assert _count(connection, scope[2]) == 2
    with pytest.raises(errors.UniqueViolation):
        _insert_embedding(connection, scope, "model-a|3|aaaaaaaa", dims=3)


def test_deleting_chunk_cascades_to_embeddings(connection) -> None:
    scope = _seed_chunk(connection)
    _insert_embedding(connection, scope, "model-a|3|aaaaaaaa", dims=3)
    _insert_embedding(connection, scope, "model-b|5|bbbbbbbb", dims=5)
    connection.execute("DELETE FROM chunks WHERE id = %s", (scope[2],))
    assert _count(connection, scope[2]) == 0


def test_scope_index_covers_course_corpus_and_version(connection) -> None:
    columns = connection.execute(
        "SELECT a.attname FROM pg_index i"
        " JOIN pg_class c ON c.oid = i.indexrelid"
        " JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, position) ON true"
        " JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum"
        " WHERE c.relname = 'ix_chunk_embeddings_scope' ORDER BY k.position"
    ).fetchall()
    assert [name for (name,) in columns] == ["course_id", "corpus_version_id", "embedding_version"]


def test_migration_history_has_a_single_head() -> None:
    script = ScriptDirectory.from_config(_alembic())
    assert script.get_heads() == [HEAD_REVISION]
    assert script.get_revision(HEAD_REVISION).down_revision == PREVIOUS_REVISION


def test_downgrade_drops_table_and_extension_then_upgrade_restores_them() -> None:
    config = _alembic()
    command.upgrade(config, "head")
    try:
        command.downgrade(config, PREVIOUS_REVISION)
        with psycopg.connect(str(Settings().database_url)) as conn:
            table = conn.execute("SELECT to_regclass('chunk_embeddings')").fetchone()
            extension = conn.execute(
                "SELECT count(*) FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
        assert table == (None,)
        assert extension == (0,)
    finally:
        command.upgrade(config, "head")
    with psycopg.connect(str(Settings().database_url)) as conn:
        assert conn.execute("SELECT to_regclass('chunk_embeddings')").fetchone() != (None,)
