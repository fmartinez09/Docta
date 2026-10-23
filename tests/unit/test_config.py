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


@pytest.mark.parametrize(
    "values",
    [
        {"tutor_model": "configured-model"},
        {"tutor_endpoint_url": "https://model.test/v1/chat/completions"},
        {"tutor_timeout_seconds": 90},
        {"tutor_max_output_tokens": 0},
        {
            "tutor_model": "model",
            "tutor_api_key": " ",
            "tutor_endpoint_url": "https://model.test/v1/chat/completions",
        },
        {
            "tutor_model": "model",
            "tutor_api_key": "test-key",
            "tutor_endpoint_url": "https://model.test/v1/chat/completions?token=secret",
        },
    ],
)
def test_tutor_configuration_fails_closed(values):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql://docta:secret@localhost:5432/docta",
            minio_health_url="http://localhost:9000/health",
            **values,
        )


def test_tutor_configuration_accepts_a_timeout_near_three_minutes() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://docta:secret@localhost:5432/docta",
        minio_health_url="http://localhost:9000/health",
        tutor_timeout_seconds=175,
        message_timeout_seconds=180,
    )

    assert settings.tutor_timeout_seconds == 175


def test_tutor_timeout_must_remain_below_message_timeout() -> None:
    with pytest.raises(ValidationError, match="shorter than message timeout"):
        Settings(
            _env_file=None,
            database_url="postgresql://docta:secret@localhost:5432/docta",
            minio_health_url="http://localhost:9000/health",
            tutor_timeout_seconds=175,
            message_timeout_seconds=175,
        )


def test_retrieval_mode_defaults_to_fts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCTA_RETRIEVAL_MODE", raising=False)
    settings = Settings(
        _env_file=None,
        database_url="postgresql://docta:secret@localhost:5432/docta",
        minio_health_url="http://localhost:9000/health",
    )

    assert settings.retrieval_mode == "fts"


@pytest.mark.parametrize("value", ["", "bogus"])
def test_retrieval_mode_rejects_unsupported_values(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql://docta:secret@localhost:5432/docta",
            minio_health_url="http://localhost:9000/health",
            retrieval_mode=value,  # type: ignore[arg-type]
        )


def embed_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": "postgresql://docta:secret@localhost:5432/docta",
        "minio_health_url": "http://localhost:9000/health",
        "embed_endpoint_url": "https://embed.test/v1/embeddings",
        "embed_model": "vendor/model",
        "embed_api_key": "embed-secret",
        "embed_dimensions": 8,
    }
    return Settings(**(values | overrides))  # type: ignore[arg-type]


def production(**overrides: object) -> Settings:
    return embed_settings(
        environment="production",
        oidc_issuer="https://issuer.test",
        oidc_audience="project",
        oidc_jwks_url="https://issuer.test/keys",
        s3_endpoint_url="https://s3.test",
        s3_access_key="access",
        s3_secret_key="secret",
        **overrides,
    )


def test_embedder_defaults_and_secret_key() -> None:
    settings = embed_settings()

    assert settings.embed_timeout_seconds == 10
    assert settings.embed_batch_size == 32
    assert settings.embed_query_template == "{text}"
    assert settings.embed_input_type == "off"
    assert settings.embed_provider_routing is None
    assert settings.embedder_configured
    assert "embed-secret" not in repr(settings)


def test_embedder_absent_by_default() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://docta:secret@localhost:5432/docta",
        minio_health_url="http://localhost:9000/health",
    )

    assert not settings.embedder_configured


@pytest.mark.parametrize(
    "missing", ["embed_endpoint_url", "embed_model", "embed_api_key", "embed_dimensions"]
)
def test_embedder_is_all_or_nothing(missing: str) -> None:
    with pytest.raises(ValidationError, match="configured together"):
        embed_settings(**{missing: None})


def test_embedder_rejects_blank_key() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        embed_settings(embed_api_key="  ")


def test_dense_requires_embedder() -> None:
    with pytest.raises(ValidationError, match="requires an embedder"):
        Settings(
            _env_file=None,
            database_url="postgresql://docta:secret@localhost:5432/docta",
            minio_health_url="http://localhost:9000/health",
            retrieval_mode="dense",
        )
    assert embed_settings(retrieval_mode="dense").retrieval_mode == "dense"


def test_dense_requires_embed_and_tutor_below_message_timeout() -> None:
    embed_settings(
        retrieval_mode="dense",
        embed_timeout_seconds=10,
        tutor_timeout_seconds=79,
        message_timeout_seconds=90,
    )
    with pytest.raises(ValidationError, match="Embedding plus tutor"):
        embed_settings(
            retrieval_mode="dense",
            embed_timeout_seconds=10,
            tutor_timeout_seconds=80,
            message_timeout_seconds=90,
        )
    # fts does not depend on the embedder timeout
    embed_settings(embed_timeout_seconds=10, tutor_timeout_seconds=80, message_timeout_seconds=90)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:pw@embed.test/v1/embeddings",
        "https://embed.test/v1/embeddings?key=1",
        "https://embed.test/v1/embeddings#frag",
    ],
)
def test_embedder_endpoint_rejects_credentials_query_and_fragment(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="credentials, query or fragment"):
        embed_settings(embed_endpoint_url=endpoint)


def test_embedder_endpoint_requires_https_in_production() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        production(embed_endpoint_url="http://embed.test/v1/embeddings")
    assert production().environment == "production"
    embed_settings(embed_endpoint_url="http://localhost:8081/v1/embeddings")


@pytest.mark.parametrize("model", ["vendor/m:free", "Vendor/M:FREE", " vendor/m :Free "])
def test_free_model_variants_are_rejected_in_production(model: str) -> None:
    with pytest.raises(ValidationError, match="Free embedding model"):
        production(embed_model=model)
    assert embed_settings(embed_model=model).embed_model == model


def test_free_validator_does_not_apply_without_embedder() -> None:
    assert production(
        embed_endpoint_url=None, embed_model=None, embed_api_key=None, embed_dimensions=None
    ).environment == "production"


@pytest.mark.parametrize("template", ["no placeholder", "{text} and {text}"])
def test_templates_require_a_single_text_placeholder(template: str) -> None:
    with pytest.raises(ValidationError, match="exactly once"):
        embed_settings(embed_query_template=template)
    with pytest.raises(ValidationError, match="exactly once"):
        embed_settings(embed_document_template=template)


def test_embed_model_length_is_capped_for_the_stored_version() -> None:
    from docta_api.embedding import MAX_EMBED_MODEL_LENGTH, derive_embedding_version

    longest = embed_settings(embed_model="m" * MAX_EMBED_MODEL_LENGTH, embed_dimensions=16000)
    assert len(derive_embedding_version(model=longest.embed_model, dimensions=16000)) <= 200
    with pytest.raises(ValidationError):
        embed_settings(embed_model="m" * (MAX_EMBED_MODEL_LENGTH + 1))


def test_provider_routing_must_disable_fallbacks() -> None:
    routing = {"order": ["nvidia"], "allow_fallbacks": False}
    assert embed_settings(embed_provider_routing=routing).embed_provider_routing == routing
    for invalid in ({}, {"order": ["nvidia"]}, {"allow_fallbacks": True}):
        with pytest.raises(ValidationError, match="allow_fallbacks"):
            embed_settings(embed_provider_routing=invalid)


def test_embedder_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCTA_EMBED_PROVIDER_ROUTING", '{"order":["a"],"allow_fallbacks":false}')
    monkeypatch.setenv("DOCTA_EMBED_INPUT_TYPE", "nvidia")

    settings = embed_settings()

    assert settings.embed_provider_routing == {"order": ["a"], "allow_fallbacks": False}
    assert settings.embed_input_type == "nvidia"


def test_dense_max_distance_is_bounded() -> None:
    assert embed_settings(dense_max_distance=0.5).dense_max_distance == 0.5
    for invalid in (0, -1, 2.5):
        with pytest.raises(ValidationError):
            embed_settings(dense_max_distance=invalid)
