from functools import lru_cache
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    AnyUrl,
    Field,
    PostgresDsn,
    SecretStr,
    UrlConstraints,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

OIDCIssuerUrl = Annotated[
    AnyUrl,
    UrlConstraints(
        max_length=2083,
        allowed_schemes=["http", "https"],
        host_required=True,
        preserve_empty_path=True,
    ),
]


class Settings(BaseSettings):
    """Validated process configuration for the API."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DOCTA_",
        extra="ignore",
        frozen=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: PostgresDsn
    minio_health_url: AnyHttpUrl
    dependency_timeout_seconds: int = Field(default=2, gt=0, le=30)
    oidc_issuer: OIDCIssuerUrl | None = None
    oidc_audience: str | None = Field(default=None, min_length=1)
    oidc_jwks_url: AnyHttpUrl | None = None
    s3_endpoint_url: AnyHttpUrl | None = None
    s3_access_key: str | None = Field(default=None, min_length=1)
    s3_secret_key: SecretStr | None = None
    s3_bucket: str = Field(default="docta-documents", min_length=3, max_length=63)
    s3_region: str = Field(default="us-east-1", min_length=1)
    upload_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    max_document_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_document_pages: int = Field(default=200, gt=0, le=2000)
    chunk_size_characters: int = Field(default=1600, ge=400, le=8000)
    chunk_overlap_characters: int = Field(default=160, ge=0, le=1000)
    pipeline_version: str = Field(default="phase0-pymupdf-v1", min_length=1, max_length=100)
    redis_url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
    redis_stream: str = Field(default="docta:jobs:ingestion:v1", min_length=1)
    redis_group: str = Field(default="docta-ingestion-workers-v1", min_length=1)
    job_lease_seconds: int = Field(default=300, ge=1, le=3600)
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    job_retry_base_seconds: float = Field(default=2, gt=0, le=60)
    job_reconcile_seconds: int = Field(default=30, ge=1, le=300)
    tutor_endpoint_url: AnyHttpUrl | None = None
    tutor_model: str | None = Field(default=None, min_length=1, max_length=255)
    tutor_api_key: SecretStr | None = None
    tutor_timeout_seconds: int = Field(default=45, ge=1, le=120)
    tutor_max_output_tokens: int = Field(default=2000, ge=128, le=8000)
    tutor_schema_profile: Literal["standard", "llama_cpp"] = "standard"
    tutor_provider: Literal["chat_completions", "unsloth"] = "chat_completions"
    message_timeout_seconds: int = Field(default=90, ge=5, le=180)

    @model_validator(mode="after")
    def validate_tutor_configuration(self) -> "Settings":
        values = (self.tutor_endpoint_url, self.tutor_model, self.tutor_api_key)
        if sum(value is not None for value in values) not in (0, 3):
            raise ValueError("Tutor endpoint, model and API key must be configured together")
        if self.tutor_api_key is not None and not self.tutor_api_key.get_secret_value().strip():
            raise ValueError("Tutor API key must not be blank")
        if self.tutor_timeout_seconds >= self.message_timeout_seconds:
            raise ValueError("Tutor timeout must be shorter than message timeout")
        if self.tutor_endpoint_url is not None:
            url = self.tutor_endpoint_url
            if url.username or url.password or url.query or url.fragment:
                raise ValueError("Tutor endpoint must not contain credentials, query or fragment")
            if self.environment == "production" and url.scheme != "https":
                raise ValueError("Tutor endpoint requires HTTPS in production")
        return self

    @model_validator(mode="after")
    def validate_redis_configuration(self) -> "Settings":
        from urllib.parse import urlsplit

        parsed = urlsplit(self.redis_url.get_secret_value())
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError("Redis URL must use redis or rediss with a hostname")
        return self

    @model_validator(mode="after")
    def validate_oidc_configuration(self) -> "Settings":
        oidc_values = (self.oidc_issuer, self.oidc_audience, self.oidc_jwks_url)
        configured_values = sum(value is not None for value in oidc_values)
        if configured_values not in (0, len(oidc_values)):
            raise ValueError("OIDC issuer, audience and JWKS URL must be configured together")
        if self.environment == "production" and configured_values == 0:
            raise ValueError("OIDC configuration is required in production")
        storage_values = (self.s3_endpoint_url, self.s3_access_key, self.s3_secret_key)
        configured_storage_values = sum(value is not None for value in storage_values)
        if configured_storage_values not in (0, len(storage_values)):
            raise ValueError("S3 endpoint, access key and secret key must be configured together")
        if self.environment == "production" and configured_storage_values == 0:
            raise ValueError("S3-compatible object storage is required in production")
        if self.chunk_overlap_characters >= self.chunk_size_characters:
            raise ValueError("chunk overlap must be smaller than chunk size")
        return self

    @property
    def oidc_configured(self) -> bool:
        return all(
            value is not None
            for value in (self.oidc_issuer, self.oidc_audience, self.oidc_jwks_url)
        )

    @property
    def object_storage_configured(self) -> bool:
        return all(
            value is not None
            for value in (self.s3_endpoint_url, self.s3_access_key, self.s3_secret_key)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
