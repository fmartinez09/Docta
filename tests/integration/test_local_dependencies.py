from urllib.parse import quote
from uuid import uuid4

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql

from docta_api.config import Settings, get_settings
from docta_api.main import create_app

pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings()


def test_migrations_rebuild_database_to_current_revision() -> None:
    settings = _settings()
    alembic_config = Config("apps/api/alembic.ini")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    with psycopg.connect(str(settings.database_url)) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert revision == ("20261023_0009",)


def test_migrations_accept_percent_encoded_database_password(monkeypatch) -> None:
    settings = _settings()
    admin_url = str(settings.database_url).rsplit("/", 1)[0] + "/postgres"
    suffix = uuid4().hex
    role, database = f"docta_test_role_{suffix}", f"docta_test_db_{suffix}"
    password = "test:password@with%characters"
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN SUPERUSER PASSWORD {}").format(
            sql.Identifier(role), sql.Literal(password)
        ))
        connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(database), sql.Identifier(role)
        ))
    try:
        host = settings.database_url.hosts()[0]
        target = (
            f"postgresql://{role}:{quote(password, safe='')}"
            f"@{host['host']}:{host['port']}/{database}"
        )
        monkeypatch.setenv("DOCTA_DATABASE_URL", target)
        get_settings.cache_clear()
        command.upgrade(Config("apps/api/alembic.ini"), "head")
        with psycopg.connect(target) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20261023_0009",
            )
    finally:
        get_settings.cache_clear()
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.mark.anyio
async def test_real_postgres_and_storage_make_api_ready() -> None:
    settings = _settings()

    async with httpx.AsyncClient(timeout=settings.dependency_timeout_seconds) as client:
        storage_response = await client.get(str(settings.storage_health_url))
    assert storage_response.status_code == 200

    app = create_app(settings=settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"postgres": "ok", "object_storage": "ok"},
    }


@pytest.mark.anyio
async def test_storage_cors_allows_browser_upload() -> None:
    settings = _settings()
    assert settings.s3_endpoint_url is not None
    async with httpx.AsyncClient() as client:
        response = await client.options(
            str(settings.s3_endpoint_url),
            headers={"Origin": "http://127.0.0.1:13000", "Access-Control-Request-Method": "PUT",
                     "Access-Control-Request-Headers": "content-type,x-amz-meta-sha256"},
        )
        assert response.headers.get("access-control-allow-origin") in {
            "*", "http://127.0.0.1:13000",
        }
