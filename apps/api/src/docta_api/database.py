from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def sqlalchemy_database_url(database_url: str) -> str:
    return database_url.replace("postgresql://", "postgresql+psycopg://", 1)


class Database:
    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_engine(
            sqlalchemy_database_url(database_url),
            pool_pre_ping=True,
        )
        self._sessions = sessionmaker(bind=self._engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions() as session:
            with session.begin():
                yield session

    def close(self) -> None:
        self._engine.dispose()
