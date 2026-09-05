import pytest
from pydantic import ValidationError

from docta_api.config import Settings


def test_settings_accept_local_postgres_and_minio_urls() -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql://docta:secret@localhost:5432/docta",
        minio_health_url="http://localhost:9000/minio/health/ready",
    )

    assert settings.environment == "test"
    assert settings.dependency_timeout_seconds == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_url", "sqlite:///docta.db"),
        ("minio_health_url", "ftp://localhost/health"),
        ("dependency_timeout_seconds", 0),
        ("redis_url", "https://localhost:6379"),
        ("job_lease_seconds", 0),
        ("job_max_attempts", 0),
        ("job_retry_base_seconds", 0),
    ],
)
def test_settings_fail_closed_for_invalid_dependency_config(field: str, value: object) -> None:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": "postgresql://docta:secret@localhost:5432/docta",
        "minio_health_url": "http://localhost:9000/minio/health/ready",
    }
    values[field] = value

    with pytest.raises(ValidationError):
        Settings(**values)  # type: ignore[arg-type]


def test_oidc_configuration_must_be_complete_when_any_value_is_present() -> None:
    with pytest.raises(ValidationError, match="configured together"):
        Settings(
            _env_file=None,
            environment="test",
            database_url="postgresql://docta:secret@localhost:5432/docta",
            minio_health_url="http://localhost:9000/minio/health/ready",
            oidc_issuer="https://identity.example.com/",
        )


def test_oidc_issuer_preserves_an_empty_path_for_exact_claim_validation() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql://docta:secret@localhost:5432/docta",
        minio_health_url="http://localhost:9000/minio/health/ready",
        oidc_issuer="http://localhost:8080",
        oidc_audience="389218636289540099",
        oidc_jwks_url="http://localhost:8080/oauth/v2/keys",
    )

    assert str(settings.oidc_issuer) == "http://localhost:8080"


def test_production_requires_oidc_configuration() -> None:
    with pytest.raises(ValidationError, match="required in production"):
        Settings(
            _env_file=None,
            environment="production",
            database_url="postgresql://docta:secret@localhost:5432/docta",
            minio_health_url="http://localhost:9000/minio/health/ready",
        )


def test_s3_configuration_must_be_complete() -> None:
    with pytest.raises(ValidationError, match="configured together"):
        Settings(
            _env_file=None,
            environment="test",
            database_url="postgresql://docta:secret@localhost:5432/docta",
            minio_health_url="http://localhost:9000/minio/health/ready",
            s3_endpoint_url="http://localhost:9000",
        )


def test_production_requires_object_storage_after_oidc_is_configured() -> None:
    with pytest.raises(ValidationError, match="object storage is required"):
        Settings(
            _env_file=None,
            environment="production",
            database_url="postgresql://docta:secret@localhost:5432/docta",
            minio_health_url="http://localhost:9000/minio/health/ready",
            oidc_issuer="https://identity.example/",
            oidc_audience="docta-api",
            oidc_jwks_url="https://identity.example/.well-known/jwks.json",
        )


def test_chunk_overlap_must_be_smaller_than_chunk_size() -> None:
    with pytest.raises(ValidationError, match="overlap"):
        Settings(
            environment="test",
            database_url="postgresql://docta:secret@localhost:5432/docta",
            minio_health_url="http://localhost:9000/minio/health/ready",
            chunk_size_characters=400,
            chunk_overlap_characters=400,
        )
