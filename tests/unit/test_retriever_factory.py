import pytest

from docta_api.config import Settings
from docta_api.database import Database
from docta_api.postgres_retriever import PostgresRetriever
from docta_api.retriever_factory import build_retriever

DATABASE_URL = "postgresql://docta:secret@localhost:5432/docta"


def _settings(**values: object) -> Settings:
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        minio_health_url="http://localhost:9000/health",
        **values,  # type: ignore[arg-type]
    )


def test_fts_mode_builds_the_postgres_retriever() -> None:
    retriever = build_retriever(_settings(), Database(DATABASE_URL))

    assert isinstance(retriever, PostgresRetriever)
    assert retriever.version == "spanish-fts-coverage-top5-v2"


def test_unsupported_mode_fails_explicitly() -> None:
    # Settings rejects other modes today; bypass validation to prove the factory also refuses.
    settings = _settings().model_copy(update={"retrieval_mode": "dense"})

    with pytest.raises(ValueError, match="Unsupported retrieval mode"):
        build_retriever(settings, Database(DATABASE_URL))
