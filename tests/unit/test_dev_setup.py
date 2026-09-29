import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values
from scripts import dev

TEST_WEB_ORIGIN = "http://127.0.0.1:13000"
TEST_ISSUER = "http://localhost:18080"


def save_profile(root, profile=None):
    example = (dev.ROOT / ".env.example").read_text()
    (root / ".env.example").write_text(example)
    profile = profile if profile is not None else dev.new_environment(root)
    path = root / ".devcontainer/.env"
    dev.private_write(path, example)
    dev.update_env(path, profile)
    return profile


def interactive(monkeypatch, answers, secrets):
    replies = iter(answers)
    hidden = iter(secrets)
    monkeypatch.setattr(dev.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda label: next(replies))
    monkeypatch.setattr(dev.getpass, "getpass", lambda label: next(hidden))
    monkeypatch.setattr(dev, "guard_environment_creation", lambda root=dev.ROOT: None)


def test_existing_environment_does_not_require_volume_inspection(tmp_path, monkeypatch):
    path = tmp_path / ".devcontainer/.env"
    path.parent.mkdir()
    path.write_text("existing private configuration")

    def unexpected(*args, **kwargs):
        pytest.fail("Existing configuration must not be inspected or replaced")

    monkeypatch.setattr(subprocess, "run", unexpected)
    dev.guard_environment_creation(tmp_path)
    assert path.read_text() == "existing private configuration"


@pytest.mark.parametrize("volume", ["docta-dev-iam_postgres-data", "docta-dev_minio-data"])
def test_missing_environment_with_existing_data_fails_before_creation(
    tmp_path, monkeypatch, volume
):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout=volume + "\n", stderr="private-payload"
        ),
    )
    with pytest.raises(dev.DevError, match="Restore their original managed environment") as error:
        dev.guard_environment_creation(tmp_path)
    assert "private-payload" not in str(error.value)
    assert not (tmp_path / ".devcontainer").exists()


def test_new_environment_ignores_dependency_and_unrelated_volumes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="docta-venv-checkout\nother_postgres-data\n", stderr=""
        ),
    )
    dev.guard_environment_creation(tmp_path)
    assert not (tmp_path / ".devcontainer/.env").exists()
    save_profile(tmp_path)
    assert dev.initialize(tmp_path)["DOCTA_DEV_MANAGED"] == "1"


def test_volume_inspection_failure_does_not_generate_environment(tmp_path, monkeypatch):
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="private-payload")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(dev.DevError, match="make Docker available") as error:
        dev.guard_environment_creation(tmp_path)
    assert "private-payload" not in str(error.value)
    assert not (tmp_path / ".devcontainer").exists()


def test_cli_guards_missing_environment_before_initialization(monkeypatch, capsys, tmp_path):
    def reject():
        raise dev.DevError("Existing volumes require original configuration")

    def unexpected():
        pytest.fail("Credentials must not be generated after a failed guard")

    monkeypatch.setattr(dev, "guard_environment_creation", reject)
    monkeypatch.setattr(dev, "migrate_legacy", lambda root: False)
    monkeypatch.setattr(dev, "initialize", unexpected)
    monkeypatch.setenv("DOCTA_DEV_HOME", str(tmp_path / "private-state"))
    monkeypatch.setattr(dev.sys, "argv", ["dev.py", "bootstrap"])
    assert dev.main() == 1
    assert "Existing volumes require original configuration" in capsys.readouterr().err


def test_fresh_profile_does_not_copy_host_credentials_and_keeps_stable_secrets(tmp_path):
    original = (
        "# User settings\nDOCTA_DATABASE_URL=postgresql://private-db/live\n"
        "DOCTA_TUTOR_API_KEY='private-key'\nCUSTOM_VALUE='keep me'\n"
    )
    (tmp_path / ".env").write_text(original)
    first = save_profile(tmp_path)
    managed = tmp_path / ".devcontainer/.env"
    snapshot = managed.read_bytes()
    assert first == dev.initialize(tmp_path)
    assert snapshot == managed.read_bytes()
    assert (tmp_path / ".env").read_text() == original
    assert "CUSTOM_VALUE" not in first
    assert "DOCTA_TUTOR_API_KEY" not in first
    assert "127.0.0.1:15432/" in first["DOCTA_DATABASE_URL"]
    assert first["DOCTA_S3_SECRET_KEY"] == first["MINIO_ROOT_PASSWORD"]
    assert len(first["DOCTA_DEV_IAM_MASTERKEY"]) == 32
    assert len(first["DOCTA_WEB_SESSION_SECRET"]) >= 32
    assert "DOCTA_OIDC_AUDIENCE" not in first


def test_clean_checkout_uses_example_and_rejects_unmanaged_file(tmp_path):
    with pytest.raises(dev.DevError, match="dev:configure"):
        dev.initialize(tmp_path)
    assert not (tmp_path / ".devcontainer/.env").exists()
    save_profile(tmp_path)
    (tmp_path / ".devcontainer/.env").write_text("PRIVATE_VALUE=secret\n")
    with pytest.raises(dev.DevError, match="not managed"):
        dev.initialize(tmp_path)
    assert (tmp_path / ".devcontainer/.env").read_text() == "PRIVATE_VALUE=secret\n"


def test_env_update_roundtrips_sensitive_characters_and_comments(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# retain comment\nVALUE=old\nOTHER=unchanged\n")
    value = 'a\\b"c\nline $d # fragment'
    dev.update_env(path, {"VALUE": value, "NEW": "added"})
    assert dotenv_values(path, interpolate=False)["VALUE"] == value
    assert "# retain comment" in path.read_text()
    assert "OTHER=unchanged" in path.read_text()


class FakeIAM:
    """HTTP port fake; assertions use wire contracts rather than a production mock."""

    def __init__(self):
        self.project = None
        self.app = None
        self.created = []
        self.updated = []
        self.fail_create = False
        self.instance_id = "instance-1"
        self.project_id = "101"
        self.app_id = "202"
        self.fail_project_read = None
        self.fail_instance_read = None

    def __call__(self, request):
        path = request.url.path.removeprefix("/management/v1")
        body = json.loads(request.content) if request.content else None
        assert request.headers["authorization"] == "Bearer private-pat"
        if path == "/admin/v1/instances/me":
            return httpx.Response(
                self.fail_instance_read or 200,
                json={"instance": {"id": self.instance_id}, "message": "private-payload"},
            )
        if path.endswith("/_search"):
            resource = self.app if "/apps" in path else self.project
            name = body["queries"][0]["nameQuery"]["name"]
            assert body["queries"][0]["nameQuery"]["method"] == "TEXT_QUERY_METHOD_EQUALS"
            result = [resource] if resource and resource["name"] == name else []
            return httpx.Response(200, json={"result": result})
        if request.method == "GET":
            if "/apps/" in path:
                app = self.app
                if not app or not path.endswith("/" + app["id"]):
                    return httpx.Response(404, json={"message": "private-payload"})
                if app and app["oidcConfig"].get("appType") == "OIDC_APP_TYPE_WEB":
                    # Match protobuf JSON: the zero/default enum value is omitted.
                    app = app | {
                        "oidcConfig": {
                            key: value
                            for key, value in app["oidcConfig"].items()
                            if key != "appType"
                        }
                    }
                return httpx.Response(200, json={"app": app})
            if self.fail_project_read == "timeout":
                raise httpx.ReadTimeout("private-payload", request=request)
            if self.fail_project_read:
                return httpx.Response(self.fail_project_read, json={"message": "private-payload"})
            if not self.project or not path.endswith("/" + self.project["id"]):
                return httpx.Response(404, json={"message": "private-payload"})
            return httpx.Response(200, json={"project": self.project})
        if request.method == "PUT":
            assert "version" not in body  # Management v1 update does not accept this create field.
            self.updated.append(body)
            self.app["oidcConfig"].update(body)
            return httpx.Response(200, json={})
        self.created.append(path)
        if path == "/projects":
            self.project = {
                "id": self.project_id, "name": body["name"], "state": "PROJECT_STATE_ACTIVE"
            }
            response = {"id": self.project_id}
        else:
            assert path == f"/projects/{self.project_id}/apps/oidc"
            assert body["accessTokenType"] == "OIDC_TOKEN_TYPE_JWT"
            assert body["appType"] == "OIDC_APP_TYPE_WEB"
            assert body["authMethodType"] == "OIDC_AUTH_METHOD_TYPE_NONE"
            assert body["grantTypes"] == ["OIDC_GRANT_TYPE_AUTHORIZATION_CODE"]
            assert body["redirectUris"] == [
                TEST_WEB_ORIGIN + "/auth/callback",
                "http://127.0.0.1:18765/callback",
            ]
            self.app = {
                "id": self.app_id,
                "name": body.pop("name"),
                "state": "APP_STATE_ACTIVE",
                "oidcConfig": body | {"clientId": "actual-client@docta"},
            }
            response = {"appId": self.app_id, "clientId": "actual-client@docta"}
        if self.fail_create:
            raise httpx.ReadTimeout("private provider payload", request=request)
        return httpx.Response(200, json=response)


def provisioner(tmp_path, fake):
    client = httpx.Client(
        base_url=TEST_ISSUER,
        transport=httpx.MockTransport(fake),
        headers={"Authorization": "Bearer private-pat"},
    )
    return dev.Provisioner(client, tmp_path / "identity.json")


def values():
    return {
        "DOCTA_DEV_PROJECT_NAME": "isolated-project",
        "DOCTA_DEV_APP_NAME": "isolated-application",
        "DOCTA_WEB_ORIGIN": TEST_WEB_ORIGIN,
        "DOCTA_DEV_OIDC_REDIRECT_URI": "http://127.0.0.1:18765/callback",
    }


def test_provision_creates_jwt_pkce_app_and_recovers_ids_on_rerun(tmp_path):
    fake = FakeIAM()
    first = provisioner(tmp_path, fake).provision(values())
    second = provisioner(tmp_path, fake).provision(values())
    assert first == second
    assert first["DOCTA_OIDC_AUDIENCE"] == "101"
    assert first["DOCTA_WEB_OIDC_CLIENT_ID"] == "actual-client@docta"
    assert first["DOCTA_DEV_OIDC_CLIENT_ID"] != "202"
    assert len(fake.created) == 2
    assert not fake.updated
    assert json.loads((tmp_path / "identity.json").read_text())["instance_id"] == "instance-1"
    assert "private-pat" not in (tmp_path / "identity.json").read_text()


def legacy_state(tmp_path, *, pending=None):
    path = tmp_path / "identity.json"
    state = {"project_id": "old-project", "app_id": "old-app"}
    if pending:
        state["pending"] = pending
    path.write_text(json.dumps(state))
    return path, path.read_bytes()


def mock_volumes(monkeypatch, volumes=()):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="\n".join(volumes), stderr="private-payload"
        ),
    )


def test_legacy_missing_project_explains_recovery_without_changing_state(tmp_path):
    fake = FakeIAM()
    path, original = legacy_state(tmp_path)
    with pytest.raises(dev.DevError, match="dev:recover-identity") as error:
        provisioner(tmp_path, fake).provision(values())
    assert path.read_bytes() == original
    assert not fake.created
    assert "private-payload" not in str(error.value)


def test_explicit_legacy_recovery_archives_identity_and_environment(tmp_path, monkeypatch):
    fake = FakeIAM()
    path, original = legacy_state(tmp_path)
    env_path = tmp_path / "managed.env"
    env_path.write_text("DOCTA_TUTOR_API_KEY='private-key'\nPOSTGRES_PASSWORD='private-db'\n")
    env_snapshot = env_path.read_bytes()
    mock_volumes(monkeypatch, ("docta-dev-iam_postgres-data", "other_postgres-data"))
    provision = provisioner(tmp_path, fake)
    provision.env_path = env_path
    result = provision.provision(values(), recover_identity=True)
    assert result["DOCTA_OIDC_AUDIENCE"] == "101"
    assert next(tmp_path.glob("identity-before-recovery-*.json")).read_bytes() == original
    assert next(tmp_path.glob("env-before-identity-recovery-*")).read_bytes() == env_snapshot
    assert env_path.read_bytes() == env_snapshot
    assert json.loads(path.read_text())["instance_id"] == "instance-1"
    assert len(fake.created) == 2
    assert "private-key" not in path.read_text()


def test_confirmed_new_instance_recovers_without_reusing_previous_ids(tmp_path, monkeypatch):
    fake = FakeIAM()
    provisioner(tmp_path, fake).provision(values())
    path = tmp_path / "identity.json"
    original = path.read_bytes()
    fake.instance_id = "instance-2"
    fake.project = fake.app = None
    fake.project_id, fake.app_id = "303", "404"
    mock_volumes(monkeypatch)
    result = provisioner(tmp_path, fake).provision(values())
    assert result["DOCTA_OIDC_AUDIENCE"] == "303"
    assert json.loads(path.read_text()) == {
        "instance_id": "instance-2", "project_id": "303", "app_id": "404"
    }
    assert next(tmp_path.glob("identity-before-recovery-*.json")).read_bytes() == original
    assert len(fake.created) == 4


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("volume", ["postgres-data", "minio-data", "redis-data"])
def test_identity_recovery_refuses_retained_docta_data(tmp_path, monkeypatch, volume, bound):
    fake = FakeIAM()
    path, original = legacy_state(tmp_path)
    if bound:
        path.write_text(json.dumps({"instance_id": "old-instance", "project_id": "old-project"}))
        original = path.read_bytes()
    mock_volumes(monkeypatch, ("docta-dev_" + volume,))
    with pytest.raises(dev.DevError, match="Retained data uses"):
        provisioner(tmp_path, fake).provision(values(), recover_identity=not bound)
    assert path.read_bytes() == original
    assert not fake.created
    assert not list(tmp_path.glob("*before*"))


def test_identity_recovery_requires_available_docker(tmp_path, monkeypatch):
    fake = FakeIAM()
    path, original = legacy_state(tmp_path)

    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="private-payload")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(dev.DevError, match="Cannot check Docta data volumes") as error:
        provisioner(tmp_path, fake).provision(values(), recover_identity=True)
    assert path.read_bytes() == original
    assert "private-payload" not in str(error.value)
    assert not fake.created


@pytest.mark.parametrize("failure", [401, 403, 500, "timeout"])
def test_legacy_recovery_does_not_treat_request_failure_as_missing_project(tmp_path, failure):
    fake = FakeIAM()
    fake.fail_project_read = failure
    path, original = legacy_state(tmp_path)
    with pytest.raises(dev.DevError) as error:
        provisioner(tmp_path, fake).provision(values(), recover_identity=True)
    assert path.read_bytes() == original
    assert not fake.created
    assert "private-payload" not in str(error.value)


@pytest.mark.parametrize("failure", [401, 403, 500])
def test_instance_read_failure_preserves_state_without_mutations(tmp_path, failure):
    fake = FakeIAM()
    fake.fail_instance_read = failure
    path, original = legacy_state(tmp_path)
    with pytest.raises(dev.DevError) as error:
        provisioner(tmp_path, fake).provision(values(), recover_identity=True)
    assert path.read_bytes() == original
    assert not fake.created
    assert "private-payload" not in str(error.value)


def test_legacy_recovery_preserves_ambiguous_creation(tmp_path):
    fake = FakeIAM()
    path, original = legacy_state(tmp_path, pending="app_id")
    with pytest.raises(dev.DevError, match="no pending creation"):
        provisioner(tmp_path, fake).provision(values(), recover_identity=True)
    assert path.read_bytes() == original
    assert not fake.created


def test_deleted_project_in_same_instance_is_not_automatically_recreated(tmp_path):
    fake = FakeIAM()
    provisioner(tmp_path, fake).provision(values())
    path = tmp_path / "identity.json"
    original = path.read_bytes()
    fake.project = None
    with pytest.raises(dev.DevError, match="same ZITADEL instance"):
        provisioner(tmp_path, fake).provision(values())
    with pytest.raises(dev.DevError, match="requires legacy state"):
        provisioner(tmp_path, fake).provision(values(), recover_identity=True)
    assert path.read_bytes() == original
    assert len(fake.created) == 2


def test_legacy_existing_project_binds_instance_and_keeps_resource_ids(tmp_path):
    fake = FakeIAM()
    first = provisioner(tmp_path, fake).provision(values())
    path = tmp_path / "identity.json"
    state = json.loads(path.read_text())
    state.pop("instance_id")
    path.write_text(json.dumps(state))
    original = path.read_bytes()
    with pytest.raises(dev.DevError, match="still exists"):
        provisioner(tmp_path, fake).provision(values(), recover_identity=True)
    assert path.read_bytes() == original
    assert provisioner(tmp_path, fake).provision(values()) == first
    assert len(fake.created) == 2
    assert json.loads(path.read_text())["instance_id"] == fake.instance_id


def test_lost_state_file_does_not_discard_environment_project_id(tmp_path):
    fake = FakeIAM()
    with pytest.raises(dev.DevError, match="dev:recover-identity"):
        provisioner(tmp_path, fake).provision(
            values() | {"DOCTA_DEV_OIDC_PROJECT_ID": "old-project"}
        )
    assert not fake.created
    assert not (tmp_path / "identity.json").exists()


def test_configuration_drift_repairs_only_managed_app(tmp_path):
    fake = FakeIAM()
    provisioner(tmp_path, fake).provision(values())
    fake.app["oidcConfig"]["accessTokenType"] = "OIDC_TOKEN_TYPE_BEARER"
    provisioner(tmp_path, fake).provision(values())
    assert fake.updated[0]["accessTokenType"] == "OIDC_TOKEN_TYPE_JWT"
    assert len(fake.created) == 2


def test_existing_user_agent_app_migrates_to_web_without_changing_identity(tmp_path):
    fake = FakeIAM()
    provision = provisioner(tmp_path, fake)
    original = provision.provision(values())
    saved_state = (tmp_path / "identity.json").read_bytes()
    fake.app["oidcConfig"]["appType"] = "OIDC_APP_TYPE_USER_AGENT"

    assert provision.provision(values()) == original
    assert provision.provision(values()) == original
    assert (tmp_path / "identity.json").read_bytes() == saved_state
    assert len(fake.created) == 2
    assert len(fake.updated) == 1
    assert fake.updated[0]["appType"] == "OIDC_APP_TYPE_WEB"
    assert fake.updated[0]["authMethodType"] == "OIDC_AUTH_METHOD_TYPE_NONE"
    assert fake.updated[0]["accessTokenType"] == "OIDC_TOKEN_TYPE_JWT"
    assert fake.updated[0]["devMode"] is True


def test_lost_creation_response_is_reconciled_without_duplicate(tmp_path):
    fake = FakeIAM()
    fake.fail_create = True
    with pytest.raises(dev.DevError) as error:
        provisioner(tmp_path, fake).provision(values())
    assert "private provider payload" not in str(error.value)
    assert json.loads((tmp_path / "identity.json").read_text())["pending"] == "project_id"
    fake.fail_create = False
    provisioner(tmp_path, fake).provision(values())
    assert len(fake.created) == 2


def test_unknown_creation_outcome_is_not_repeated(tmp_path):
    fake = FakeIAM()
    (tmp_path / "identity.json").write_text(json.dumps({"pending": "project_id"}))
    with pytest.raises(dev.DevError, match="unknown outcome"):
        provisioner(tmp_path, fake).provision(values())
    assert not fake.created


def test_inactive_or_foreign_saved_project_fails_closed(tmp_path):
    fake = FakeIAM()
    provisioner(tmp_path, fake).provision(values())
    fake.project["name"] = "foreign-project"
    with pytest.raises(dev.DevError, match="does not match"):
        provisioner(tmp_path, fake).provision(values())
    assert len(fake.created) == 2


def test_subprocess_failure_does_not_expose_captured_secrets(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="private-pat provider-payload")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(dev.DevError) as error:
        dev.run(["docker", "info"], {}, quiet=True)
    assert "private-pat" not in str(error.value)


@pytest.mark.parametrize(
    ("logs", "expected"),
    [
        (
            'FATAL: password authentication failed for user "postgres" (SQLSTATE 28P01)',
            "PostgreSQL rejected its password",
        ),
        ("unclassified provider failure", "check the docta-dev-iam service status"),
    ],
)
def test_iam_startup_classifies_failure_without_printing_logs(monkeypatch, logs, expected):
    def fail(*args, **kwargs):
        raise dev.DevError("Command failed (docker)")

    inspections = []

    def inspect(command, **kwargs):
        inspections.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0, stdout=logs + " private-pat secret-dsn", stderr="private-password"
        )

    monkeypatch.setattr(dev, "run", fail)
    monkeypatch.setattr(subprocess, "run", inspect)
    with pytest.raises(dev.DevError, match=expected) as error:
        dev.start_iam({})
    for secret in ("private-pat", "secret-dsn", "private-password", logs):
        assert secret not in str(error.value)
    assert len(inspections) == 1
    command, options = inspections[0]
    assert command[-5:] == ["logs", "--no-color", "--tail", "50", "zitadel-api"]
    assert options["capture_output"] is True
    assert options["timeout"] == 15


def test_iam_failure_remains_safe_when_log_inspection_times_out(monkeypatch):
    def fail(*args, **kwargs):
        raise dev.DevError("Command failed (docker)")

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 15, output="private-pat")

    monkeypatch.setattr(dev, "run", fail)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(dev.DevError, match="check the docta-dev-iam service status") as error:
        dev.start_iam({})
    assert "private-pat" not in str(error.value)


def test_full_checks_flag_and_managed_command_environment(monkeypatch):
    monkeypatch.setattr(dev, "guard_environment_creation", lambda: None)
    monkeypatch.setattr(dev, "initialize", lambda: {"LOCAL": "managed"})
    checks = []
    monkeypatch.setattr(dev, "check", lambda env, full: checks.append((env, full)))
    monkeypatch.setattr(dev.sys, "argv", ["dev.py", "check", "--full"])
    assert dev.main() == 0
    assert checks == [({"LOCAL": "managed"}, True)]
    runs = []
    monkeypatch.setattr(dev, "run", lambda command, env: runs.append((command, env)))
    monkeypatch.setattr(dev.sys, "argv", ["dev.py", "run", "--", "python", "-V"])
    assert dev.main() == 0
    assert runs == [(["python", "-V"], {"LOCAL": "managed"})]


def test_checks_remove_development_credentials_and_keep_test_ports(monkeypatch):
    commands = []
    monkeypatch.setenv("DOCTA_DEV_OIDC_CLIENT_ID", "host-client")
    monkeypatch.setenv("DOCTA_TEST_POSTGRES_PORT", "25432")

    def capture(command, env, **kwargs):
        commands.append((command, env, kwargs))

    monkeypatch.setattr(dev, "run", capture)
    dev.check({"DOCTA_OIDC_AUDIENCE": "dev-project"}, full=True)
    test_setup = next(item for item in commands if "docta-test" in item[0])
    assert test_setup[1]["DOCTA_TEST_POSTGRES_PORT"] == "25432"
    pytest_commands = [item for item in commands if "pytest" in item[0]]
    assert len(pytest_commands) == 2
    for _, env, options in pytest_commands:
        assert env == {
            "DOCTA_DEV_MANAGED": "1",
            "DOCTA_TEST_POSTGRES_PORT": "25432",
            "DOCTA_DATABASE_URL": (
                "postgresql://docta_test:docta_test_only@127.0.0.1:25432/postgres"
            ),
            "DOCTA_MINIO_HEALTH_URL": "http://127.0.0.1:59000/minio/health/ready",
        }
        assert options["inherit_identity"] is False


def test_first_configuration_accepts_own_accounts_and_google_without_starting_services(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / ".env.example").write_text((dev.ROOT / ".env.example").read_text())
    (tmp_path / ".env").write_text("DOCTA_TUTOR_API_KEY=do-not-copy-this-key\n")
    interactive(
        monkeypatch,
        [
            "teacher@example.test",
            "custom",
            "course_db",
            "course_user",
            "my-storage-user",
            "my-documents",
            "iam_user",
            "iam_db",
            "My Organization",
            "My Project",
            "My App",
            "my-bootstrap",
            "my-login",
            "no",
            "google",
            "",
            "my-selected-model",
            "60",
            "90",
            "4000",
        ],
        [
            "Teacher1!private",
            "db:p@ss/$#",
            "Storage1!private",
            "iam:p@ss/$#",
            "m" * 32,
            "s" * 48,
            "my-private-google-key",
        ],
    )
    profile = dev.configure(tmp_path)
    assert profile["DOCTA_DEV_LOGIN_USERNAME"] == "teacher@example.test"
    assert profile["DOCTA_DEV_APP_NAME"] == "My App"
    assert profile["DOCTA_DEV_BOOTSTRAP_USERNAME"] == "my-bootstrap"
    assert profile["DOCTA_DATABASE_URL"].endswith(
        "course_user:db%3Ap%40ss%2F%24%23@127.0.0.1:15432/course_db"
    )
    assert (
        "iam_user:iam%3Ap%40ss%2F%24%23@postgres:5432/iam_db"
        in profile["DOCTA_DEV_IAM_DATABASE_DSN"]
    )
    assert profile["DOCTA_TUTOR_PROVIDER"] == "chat_completions"
    assert profile["DOCTA_TUTOR_SCHEMA_PROFILE"] == "standard"
    assert profile["DOCTA_TUTOR_MODEL"] == "my-selected-model"
    assert profile == dev.initialize(tmp_path)
    output = capsys.readouterr().out
    for secret in (
        "my-private-google-key",
        "Teacher1!private",
        "db:p@ss",
        "Storage1!private",
        "my-storage-user",
    ):
        assert secret not in output
    assert "do-not-copy-this-key" not in (tmp_path / ".devcontainer/.env").read_text()


def test_existing_configuration_preserves_identity_and_credentials_when_switching_provider(
    tmp_path, monkeypatch, capsys
):
    profile = save_profile(tmp_path)
    dev.update_env(
        tmp_path / ".devcontainer/.env",
        {
            "DOCTA_TUTOR_ENDPOINT_URL": "http://127.0.0.1:8085/v1/chat/completions",
            "DOCTA_TUTOR_MODEL": "old-model",
            "DOCTA_TUTOR_API_KEY": "old-private-key",
            "DOCTA_TUTOR_PROVIDER": "unsloth",
            "DOCTA_TUTOR_SCHEMA_PROFILE": "llama_cpp",
        },
    )
    path = tmp_path / ".devcontainer/.env"
    previous = path.read_bytes()
    identity = path.parent / "state/identity.json"
    dev.private_write(identity, '{"project_id":"101","app_id":"202"}\n')
    interactive(monkeypatch, ["google", "", "my-model", "45", "90", "2000"], ["new-private-key"])
    updated = dev.configure(tmp_path)
    for key, value in profile.items():
        if not key.startswith("DOCTA_TUTOR_"):
            assert updated[key] == value
    assert updated["DOCTA_TUTOR_API_KEY"] == "new-private-key"
    assert updated["DOCTA_TUTOR_SCHEMA_PROFILE"] == "standard"
    assert identity.read_text() == '{"project_id":"101","app_id":"202"}\n'
    backups = list(identity.parent.glob("env-before-configure-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == previous
    output = capsys.readouterr().out
    assert "new-private-key" not in output and "old-private-key" not in output


def test_invalid_reconfiguration_does_not_change_environment_or_state(
    tmp_path, monkeypatch, capsys
):
    save_profile(tmp_path)
    path = tmp_path / ".devcontainer/.env"
    previous = path.read_bytes()
    interactive(
        monkeypatch,
        [
            "custom",
            "https://example.test?key=private-query",
            "model",
            "chat_completions",
            "standard",
            "90",
            "90",
            "2000",
        ],
        ["private-input-key"],
    )
    with pytest.raises(dev.DevError, match="Invalid application configuration") as error:
        dev.configure(tmp_path)
    assert "private" not in str(error.value)
    assert path.read_bytes() == previous
    assert not (path.parent / "state").exists()
    assert "private-input-key" not in capsys.readouterr().out


def test_disabling_tutor_removes_key_instead_of_retaining_old_provider(tmp_path, monkeypatch):
    save_profile(tmp_path)
    path = tmp_path / ".devcontainer/.env"
    dev.update_env(
        path,
        dict(
            zip(
                dev.TUTOR_FIELDS,
                ["http://localhost:8085/v1/chat/completions", "model", "old-private-key"],
                strict=True,
            )
        ),
    )
    interactive(monkeypatch, ["none"], [])
    profile = dev.configure(tmp_path)
    assert all(key not in profile for key in dev.TUTOR_FIELDS)
    assert "old-private-key" not in path.read_text()
    assert dev.initialize(tmp_path) == profile


def test_legacy_profile_backfills_in_memory_without_changing_existing_secrets(tmp_path):
    profile = save_profile(tmp_path)
    legacy = {
        key: value
        for key, value in profile.items()
        if key
        not in {
            "DOCTA_DEV_LOGIN_USERNAME",
            "DOCTA_DEV_LOGIN_EMAIL",
            "DOCTA_DEV_ORG_NAME",
            "DOCTA_DEV_APP_NAME",
            "DOCTA_DEV_BOOTSTRAP_USERNAME",
            "DOCTA_DEV_LOGIN_SERVICE_USERNAME",
            "DOCTA_DEV_IAM_DB_USER",
            "DOCTA_DEV_IAM_DB_NAME",
            "DOCTA_DEV_IAM_DATABASE_DSN",
            "DOCTA_DEV_WORKER_DATABASE_URL",
            "DOCTA_DEV_WEB_PORT",
            "DOCTA_DEV_API_PORT",
            "DOCTA_DEV_IAM_PORT",
            "DOCTA_DEV_CALLBACK_PORT",
        }
    }
    path = tmp_path / ".devcontainer/.env"
    dev.private_write(path, "")
    dev.update_env(path, legacy)
    previous = path.read_bytes()
    restored = dev.initialize(tmp_path)
    assert restored["DOCTA_DEV_LOGIN_USERNAME"] == legacy["DOCTA_DEV_OIDC_LOGIN_HINT"]
    assert restored["DOCTA_DEV_APP_NAME"] == dev.LEGACY_APP_NAME
    assert restored["DOCTA_DEV_IAM_DB_USER"] == "postgres"
    assert restored["POSTGRES_PASSWORD"] == legacy["POSTGRES_PASSWORD"]
    assert path.read_bytes() == previous


def test_custom_ports_propagate_to_oidc_uploads_and_server_origins(tmp_path):
    profile = save_profile(tmp_path)
    profile |= {key: str(21000 + index) for index, key in enumerate(dev.PORT_DEFAULTS)}
    profile |= dev.derived_values(profile)
    save_profile(tmp_path, profile)
    assert dev.initialize(tmp_path) == profile
    assert profile["DOCTA_WEB_ORIGIN"] == "http://127.0.0.1:21000"
    assert profile["DOCTA_OIDC_ISSUER"] == "http://localhost:21002"
    assert profile["DOCTA_S3_ENDPOINT_URL"] == "http://127.0.0.1:21006"
    profile["POSTGRES_PORT"] = profile["MINIO_API_PORT"]
    with pytest.raises(dev.DevError, match="distinct"):
        dev.validate_environment(profile)


def test_recovery_import_restores_credentials_without_generating_or_overwriting(
    tmp_path, monkeypatch
):
    other = tmp_path / "original"
    other.mkdir()
    original = save_profile(other)
    source = other / ".devcontainer/.env"
    monkeypatch.setattr(dev.sys.stdin, "isatty", lambda: False)
    assert dev.configure(tmp_path, source=source) == original
    assert source.read_bytes() == (tmp_path / ".devcontainer/.env").read_bytes()
    with pytest.raises(dev.DevError, match="cannot replace"):
        dev.configure(tmp_path, source=source)


def test_secret_prompt_refuses_echo_fallback(monkeypatch):
    import warnings

    def fallback(label):
        warnings.warn("fallback", dev.getpass.GetPassWarning, stacklevel=2)
        pytest.fail("Must not reach echo fallback")

    monkeypatch.setattr(dev.getpass, "getpass", fallback)
    with pytest.raises(dev.DevError, match="hidden secret input"):
        dev.prompt_secret("Key")


def test_noninteractive_configure_and_fresh_container_hook_do_not_create_files(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dev.sys.stdin, "isatty", lambda: False)
    with pytest.raises(dev.DevError, match="interactive terminal"):
        dev.configure(tmp_path)
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev.sys, "argv", ["dev.py", "post-start"])
    assert dev.main() == 0
    assert not (tmp_path / ".devcontainer/.env").exists()


def test_managed_api_and_token_helper_do_not_fall_back_to_root_environment(tmp_path):
    (tmp_path / ".env").write_text(
        "DOCTA_TUTOR_ENDPOINT_URL=https://private.test/v1/chat/completions\n"
        "DOCTA_TUTOR_MODEL=do-not-import\nDOCTA_TUTOR_API_KEY=do-not-import-key\n"
        "DOCTA_DEV_OIDC_PROJECT_ID=123\nDOCTA_DEV_OIDC_LOGIN_HINT=personal-user\n"
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith("DOCTA_")}
    environment |= {
        "DOCTA_DEV_MANAGED": "1",
        "DOCTA_DATABASE_URL": "postgresql://unit:unit@127.0.0.1:5432/unit",
        "DOCTA_MINIO_HEALTH_URL": "http://127.0.0.1:9000/minio/health/ready",
        "DOCTA_DEV_OIDC_CLIENT_ID": "synthetic-client",
        "PYTHONPATH": str(Path(dev.ROOT) / "apps/api/src"),
    }
    code = (
        "from docta_api.config import Settings; from docta_api.dev_token import DevTokenSettings; "
        "from pydantic import ValidationError; "
        "s=Settings(); assert s.tutor_api_key is None and s.tutor_model is None;\n"
        "try: DevTokenSettings()\n"
        "except ValidationError as e: assert any(x['loc']==('project_id',) for x in e.errors())\n"
        "else: raise AssertionError('Personal project was imported')\n"
    )
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=environment, check=True)
