"""Select the Retriever implementation from configuration; performs no I/O."""

from docta_api.config import Settings
from docta_api.database import Database
from docta_api.postgres_retriever import PostgresRetriever
from docta_api.rag import Retriever


def build_retriever(settings: Settings, database: Database) -> Retriever:
    if settings.retrieval_mode == "fts":
        return PostgresRetriever(database)
    raise ValueError(f"Unsupported retrieval mode: {settings.retrieval_mode!r}")
