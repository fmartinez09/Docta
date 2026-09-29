import subprocess
from typing import ClassVar

import pytest
from scripts import dev


def test_runtime_processes_do_not_receive_iam_administrator_secrets():
    values = {
        "DOCTA_DEV_MANAGED": "1",
        "DOCTA_DATABASE_URL": "postgresql://app",
        "DOCTA_OIDC_ISSUER": "http://localhost:18080",
        "DOCTA_OIDC_AUDIENCE": "project",
        "DOCTA_OIDC_JWKS_URL": "http://localhost:18080/oauth/v2/keys",
        "DOCTA_TUTOR_ENDPOINT_URL": "http://localhost:8080/v1/chat/completions",
        "DOCTA_TUTOR_MODEL": "local-model",
        "DOCTA_TUTOR_API_KEY": "private-model-key",
        "POSTGRES_PASSWORD": "database-password",
        "MINIO_ROOT_PASSWORD": "storage-password",
        "DOCTA_DEV_LOGIN_PASSWORD": "iam-password",
        "DOCTA_DEV_IAM_MASTERKEY": "12345678901234567890123456789012",
    }

    for component in ("api", "worker", "web"):
        runtime = dev.runtime_values(values, component)
        assert "DOCTA_DEV_LOGIN_PASSWORD" not in runtime
        assert "DOCTA_DEV_IAM_MASTERKEY" not in runtime
        assert "POSTGRES_PASSWORD" not in runtime
        assert "MINIO_ROOT_PASSWORD" not in runtime
    assert dev.runtime_values(values, "api")["DOCTA_TUTOR_API_KEY"] == "private-model-key"
    assert "DOCTA_TUTOR_API_KEY" not in dev.runtime_values(values, "worker")
    assert "DOCTA_TUTOR_API_KEY" not in dev.runtime_values(values, "web")


def test_process_environment_removes_host_service_credentials(monkeypatch):
    monkeypatch.setenv("DOCTA_DEV_LOGIN_PASSWORD", "host-iam-password")
    monkeypatch.setenv("POSTGRES_PASSWORD", "host-database-password")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "host-storage-password")
    environment = dev.process_environment({"DOCTA_DATABASE_URL": "postgresql://managed"})
    assert environment["DOCTA_DATABASE_URL"] == "postgresql://managed"
    assert "DOCTA_DEV_LOGIN_PASSWORD" not in environment
    assert "POSTGRES_PASSWORD" not in environment
    assert "MINIO_ROOT_PASSWORD" not in environment


def test_daily_start_rejects_unregistered_identity_before_touching_docker(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("daily start must not inspect or mutate Docker before OIDC validation")

    monkeypatch.setattr(dev.subprocess, "run", unexpected)
    with pytest.raises(dev.DevError, match="OIDC is not registered"):
        dev.start_existing({"DOCTA_DEV_MANAGED": "1"})


def _registered_values():
    return {
        "DOCTA_OIDC_AUDIENCE": "project",
        "DOCTA_DEV_OIDC_PROJECT_ID": "project",
        "DOCTA_WEB_OIDC_CLIENT_ID": "client",
        "DOCTA_DEV_OIDC_CLIENT_ID": "client",
    }


def test_daily_start_skips_compose_start_when_services_are_already_healthy(monkeypatch):
    inspected = []

    def docker_ps(command, **kwargs):
        project = command[command.index("--filter") + 1].rsplit("=", 1)[1]
        inspected.append(project)
        services = (
            ("proxy", "postgres", "zitadel-api", "zitadel-login")
            if project == "docta-dev-iam" else ("postgres", "redis", "minio")
        )
        output = "".join(f"{service}|running|Up 2 minutes (healthy)\n" for service in services)
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(dev.subprocess, "run", docker_ps)
    monkeypatch.setattr(dev, "run", lambda *args, **kwargs: pytest.fail("unexpected start"))
    monkeypatch.setattr(dev, "verify_oidc", lambda values: None)
    monkeypatch.setattr(dev, "verify_schema", lambda values: None)
    dev.start_existing(_registered_values())
    assert inspected == ["docta-dev-iam", "docta-dev"]


def test_daily_start_reports_failing_stack_without_docker_output(monkeypatch):
    outputs = iter([
        "proxy|exited|Exited (1)\npostgres|running|Up (healthy)\n"
        "zitadel-api|running|Up (healthy)\nzitadel-login|running|Up (healthy)\n",
        "proxy|exited|Exited (1)\npostgres|running|Up (healthy)\n"
        "zitadel-api|running|Up (healthy)\nzitadel-login|running|Up (healthy)\n",
    ])

    def docker_ps(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=next(outputs))

    def failed_start(*args, **kwargs):
        raise dev.DevError("private password and provider payload")

    monkeypatch.setattr(dev.subprocess, "run", docker_ps)
    monkeypatch.setattr(dev, "run", failed_start)
    with pytest.raises(dev.DevError, match="Cannot start docta-dev-iam") as error:
        dev.start_existing(_registered_values())
    assert "proxy=exited" in str(error.value)
    assert "private password" not in str(error.value)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Client:
    responses: ClassVar[list] = []
    requests: ClassVar[list] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.responses.pop(0)


def test_oidc_validation_rejects_malformed_public_metadata_without_auth_headers(monkeypatch):
    _Client.responses = [
        _Response(
            {
                "issuer": "http://localhost:18080",
                "jwks_uri": "http://localhost:18080/oauth/v2/keys",
                "code_challenge_methods_supported": "S256",
                "token_endpoint_auth_methods_supported": ["none"],
            }
        )
    ]
    _Client.requests = []
    monkeypatch.setattr(dev.httpx, "Client", _Client)
    values = {
        "DOCTA_OIDC_ISSUER": "http://localhost:18080",
        "DOCTA_OIDC_JWKS_URL": "http://localhost:18080/oauth/v2/keys",
    }
    with pytest.raises(dev.DevError, match="discovery/signing keys"):
        dev.verify_oidc(values)
    assert _Client.requests == [
        ("http://localhost:18080/.well-known/openid-configuration", {})
    ]


def test_oidc_validation_rejects_unusable_rsa_jwks(monkeypatch):
    _Client.responses = [
        _Response(
            {
                "issuer": "http://localhost:18080",
                "jwks_uri": "http://localhost:18080/oauth/v2/keys",
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
            }
        ),
        _Response({"keys": [{"kty": "RSA", "kid": "missing-modulus"}]}),
    ]
    _Client.requests = []
    monkeypatch.setattr(dev.httpx, "Client", _Client)
    values = {
        "DOCTA_OIDC_ISSUER": "http://localhost:18080",
        "DOCTA_OIDC_JWKS_URL": "http://localhost:18080/oauth/v2/keys",
    }
    with pytest.raises(dev.DevError, match="discovery/signing keys"):
        dev.verify_oidc(values)
    assert all(not options.get("headers") for _, options in _Client.requests)
