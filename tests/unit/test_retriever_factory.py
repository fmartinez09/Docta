import pytest
from tests.embedder_fakes import FakeEmbedder

from docta_api.config import Settings
from docta_api.database import Database
from docta_api.dense_retriever import DenseRetriever
from docta_api.postgres_retriever import PostgresRetriever
from docta_api.retriever_factory import build_retriever

DATABASE_URL = "postgresql://docta:secret@localhost:5432/docta"


def _settings(**values: object) -> Settings:
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        storage_health_url="http://localhost:9000/health",
        **values,  # type: ignore[arg-type]
    )


def test_fts_mode_builds_the_postgres_retriever() -> None:
    retriever = build_retriever(_settings(), Database(DATABASE_URL))

    assert isinstance(retriever, PostgresRetriever)
    assert retriever.version == "spanish-fts-coverage-top5-v2"


def test_unsupported_mode_fails_explicitly() -> None:
    # Settings only accepts known modes; a copy bypasses validation to prove the factory refuses.
    settings = _settings().model_copy(update={"retrieval_mode": "hybrid"})

    with pytest.raises(ValueError, match="Unsupported retrieval mode"):
        build_retriever(settings, Database(DATABASE_URL))


def _dense_settings(**overrides: object) -> Settings:
    return _settings().model_copy(
        update={"retrieval_mode": "dense", "dense_max_distance": 0.4, **overrides}
    )


def test_dense_mode_builds_the_dense_retriever() -> None:
    embedder = FakeEmbedder()

    retriever = build_retriever(_dense_settings(), Database(DATABASE_URL), embedder)

    assert isinstance(retriever, DenseRetriever)
    assert retriever.version == f"dense-cosine-top5-d0.4-{embedder.version}"


def test_dense_mode_requires_an_embedder() -> None:
    with pytest.raises(ValueError, match="embedder"):
        build_retriever(_dense_settings(), Database(DATABASE_URL))


def test_dense_mode_requires_a_distance_threshold() -> None:
    with pytest.raises(ValueError, match="dense_max_distance"):
        build_retriever(
            _dense_settings(dense_max_distance=None), Database(DATABASE_URL), FakeEmbedder()
        )


def test_dense_retriever_rejects_a_version_longer_than_the_stored_column() -> None:
    embedder = FakeEmbedder(model="m" * 120)

    with pytest.raises(ValueError, match="stored length"):
        build_retriever(_dense_settings(), Database(DATABASE_URL), embedder)
