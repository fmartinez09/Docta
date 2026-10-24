"""Select the Retriever implementation from configuration; performs no I/O."""

from docta_api.config import Settings
from docta_api.database import Database
from docta_api.dense_retriever import DenseRetriever
from docta_api.embedding import Embedder
from docta_api.postgres_retriever import PostgresRetriever
from docta_api.rag import Retriever


def build_retriever(
    settings: Settings, database: Database, embedder: Embedder | None = None
) -> Retriever:
    if settings.retrieval_mode == "fts":
        return PostgresRetriever(database)
    if settings.retrieval_mode == "dense":
        if embedder is None:
            raise ValueError("Dense retrieval requires an embedder")
        if settings.dense_max_distance is None:
            raise ValueError("Dense retrieval requires dense_max_distance")
        return DenseRetriever(database, embedder, settings.dense_max_distance)
    raise ValueError(f"Unsupported retrieval mode: {settings.retrieval_mode!r}")
