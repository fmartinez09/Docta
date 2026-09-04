from dataclasses import dataclass

import httpx
import pytest

from docta_api.config import Settings
from docta_api.health import ReadinessProbes
from docta_api.main import create_app

pytestmark = pytest.mark.anyio


@dataclass(frozen=True)
class StubProbe:
    error: Exception | None = None

    async def check(self) -> None:
        if self.error is not None:
            raise self.error


async def test_liveness_is_reachable_and_propagates_correlation_id() -> None:
    app = create_app(settings=_settings(), probes=_probes())

    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            response = await client.get(
                "/api/v1/health/live",
                headers={"x-request-id": "test-request-123"},
            )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "docta-api"}
    assert response.headers["x-request-id"] == "test-request-123"


async def test_readiness_reports_all_required_dependencies() -> None:
    app = create_app(settings=_settings(), probes=_probes())

    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"postgres": "ok", "object_storage": "ok"},
    }


async def test_readiness_fails_safely_without_provider_details() -> None:
    probes = ReadinessProbes(
        postgres=StubProbe(error=RuntimeError("password=must-not-leak")),
        object_storage=StubProbe(),
    )
    app = create_app(settings=_settings(), probes=probes)

    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "code": "service_not_ready",
        "message": "One or more required dependencies are unavailable.",
        "checks": {"postgres": "unavailable", "object_storage": "ok"},
    }
    assert "must-not-leak" not in response.text


def _settings() -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql://docta:secret@localhost:5432/docta",
        minio_health_url="http://localhost:9000/minio/health/ready",
    )


def _probes() -> ReadinessProbes:
    return ReadinessProbes(postgres=StubProbe(), object_storage=StubProbe())


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")
