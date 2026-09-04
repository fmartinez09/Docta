import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config

from docta_api.config import Settings
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

    assert revision == ("20260903_0005",)


@pytest.mark.anyio
async def test_real_postgres_and_minio_make_api_ready() -> None:
    settings = _settings()

    async with httpx.AsyncClient(timeout=settings.dependency_timeout_seconds) as client:
        minio_response = await client.get(str(settings.minio_health_url))
    assert minio_response.status_code == 200

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
