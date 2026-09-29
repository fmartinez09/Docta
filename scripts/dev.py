"""Isolated local development orchestration; never reset volumes or print secrets."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import dotenv_values
from jwt import PyJWK
from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from docta_api.config import Settings
from scripts.dev_state import StateError, environment_lock, migrate_legacy, profile_directory

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT
LEGACY_APP_NAME = "Docta public PKCE"
PORT_DEFAULTS = {
    "DOCTA_DEV_WEB_PORT": "13000",
    "DOCTA_DEV_API_PORT": "18000",
    "DOCTA_DEV_IAM_PORT": "18080",
    "DOCTA_DEV_CALLBACK_PORT": "18765",
    "POSTGRES_PORT": "15432",
    "REDIS_PORT": "16379",
    "MINIO_API_PORT": "19000",
    "MINIO_CONSOLE_PORT": "19001",
}
TUTOR_FIELDS = ("DOCTA_TUTOR_ENDPOINT_URL", "DOCTA_TUTOR_MODEL", "DOCTA_TUTOR_API_KEY")


def profile_path(name: str, root: Path | None = None) -> Path:
    selected_root = ROOT if root is None else root
    profile = (
        profile_directory(selected_root)
        if selected_root == REPOSITORY_ROOT
        else selected_root / ".devcontainer"
    )
    return profile / name


class DevError(Exception):
    """Safe diagnostics without subprocess/provider payloads."""


class IAMHTTPError(DevError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.touch(mode=0o600, exist_ok=True)
    temporary.chmod(0o600)
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def environment_text(text: str, values: dict[str, str], *, remove: tuple[str, ...] = ()) -> str:
    for key in remove:
        text = re.sub(rf"(?m)^(?:export )?{re.escape(key)}=.*\n?", "", text)
    for key, value in values.items():
        # Single quotes keep literal dollar expressions out of Compose's .env interpolation.
        literal = "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
        line = f"{key}={literal}"
        pattern = rf"(?m)^(?:export )?{re.escape(key)}=.*$"
        if re.search(pattern, text):
            text = re.sub(pattern, lambda _, replacement=line: replacement, text)
        else:
            text = text.rstrip() + "\n" + line + "\n"
    return text


def update_env(path: Path, values: dict[str, str], *, remove: tuple[str, ...] = ()) -> None:
    private_write(path, environment_text(path.read_text(encoding="utf-8"), values, remove=remove))


def read_environment(path: Path) -> dict[str, str]:
    return {
        key: value
        for key, value in dotenv_values(path, interpolate=False).items()
        if value is not None
    }


class DeveloperSettings(Settings):
    """Validate only supplied configuration, without reading the host's environment/files."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def derived_values(values: dict[str, str]) -> dict[str, str]:
    issuer = f"http://localhost:{values['DOCTA_DEV_IAM_PORT']}"
    storage = f"http://127.0.0.1:{values['MINIO_API_PORT']}"
    user, password, database = (
        quote(values[key], safe="") for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    )
    iam_user, iam_password, iam_database = (
        quote(values[key], safe="")
        for key in ("DOCTA_DEV_IAM_DB_USER", "DOCTA_DEV_IAM_DB_PASSWORD", "DOCTA_DEV_IAM_DB_NAME")
    )
    return {
        "DOCTA_DATABASE_URL": f"postgresql://{user}:{password}@127.0.0.1:"
        f"{values['POSTGRES_PORT']}/{database}",
        "DOCTA_DEV_WORKER_DATABASE_URL": f"postgresql://{user}:{password}@postgres:5432/{database}",
        "DOCTA_DEV_IAM_DATABASE_DSN": f"postgresql://{iam_user}:{iam_password}"
        f"@postgres:5432/{iam_database}?sslmode=disable",
        "DOCTA_API_BASE_URL": f"http://127.0.0.1:{values['DOCTA_DEV_API_PORT']}",
        "DOCTA_WEB_ORIGIN": f"http://127.0.0.1:{values['DOCTA_DEV_WEB_PORT']}",
        "DOCTA_S3_ENDPOINT_URL": storage,
        "DOCTA_MINIO_HEALTH_URL": storage + "/minio/health/ready",
        "DOCTA_S3_ACCESS_KEY": values["MINIO_ROOT_USER"],
        "DOCTA_S3_SECRET_KEY": values["MINIO_ROOT_PASSWORD"],
        "DOCTA_REDIS_URL": f"redis://127.0.0.1:{values['REDIS_PORT']}/0",
        "DOCTA_OIDC_ISSUER": issuer,
        "DOCTA_OIDC_JWKS_URL": issuer + "/oauth/v2/keys",
        "DOCTA_DEV_OIDC_ISSUER": issuer,
        "DOCTA_DEV_OIDC_REDIRECT_URI": f"http://127.0.0.1:{values['DOCTA_DEV_CALLBACK_PORT']}"
        "/callback",
        "DOCTA_DEV_OIDC_LOGIN_HINT": values["DOCTA_DEV_LOGIN_USERNAME"],
    }


def normalize_environment(values: dict[str, str]) -> dict[str, str]:
    """Backfill old managed profiles in memory; never replace their accounts or secrets."""
    if values.get("DOCTA_DEV_MANAGED") != "1":
        raise DevError("Existing .devcontainer/.env is not managed; preserve it before setup.")
    values = values.copy()
    old_origins = {
        "DOCTA_DEV_WEB_PORT": "DOCTA_WEB_ORIGIN",
        "DOCTA_DEV_API_PORT": "DOCTA_API_BASE_URL",
        "DOCTA_DEV_IAM_PORT": "DOCTA_OIDC_ISSUER",
        "DOCTA_DEV_CALLBACK_PORT": "DOCTA_DEV_OIDC_REDIRECT_URI",
    }
    for key, default in PORT_DEFAULTS.items():
        port = urlsplit(values.get(old_origins.get(key, ""), "")).port
        values.setdefault(key, str(port) if port else default)
    login = values.get("DOCTA_DEV_OIDC_LOGIN_HINT", "")
    legacy = {
        "DOCTA_DEV_LOGIN_USERNAME": login,
        "DOCTA_DEV_LOGIN_EMAIL": login,
        "DOCTA_DEV_ORG_NAME": "Docta Development",
        "DOCTA_DEV_APP_NAME": LEGACY_APP_NAME,
        "DOCTA_DEV_BOOTSTRAP_USERNAME": "docta-bootstrap",
        "DOCTA_DEV_LOGIN_SERVICE_USERNAME": "login-client",
        "DOCTA_DEV_IAM_DB_USER": "postgres",
        "DOCTA_DEV_IAM_DB_NAME": "zitadel",
    }
    for key, value in legacy.items():
        values.setdefault(key, value)
    for key in ("DOCTA_DEV_IAM_DATABASE_DSN", "DOCTA_DEV_WORKER_DATABASE_URL"):
        values.setdefault(key, derived_values(values)[key])
    return values


def validate_environment(values: dict[str, str]) -> None:
    if values.get("DOCTA_DEV_MANAGED") != "1":
        raise DevError("Existing .devcontainer/.env is not managed; preserve it before setup.")
    ports = [int(values[key]) for key in PORT_DEFAULTS]
    if any(port < 1024 or port > 65535 for port in ports) or len(set(ports)) != len(ports):
        raise DevError("Development ports must be distinct integers between 1024 and 65535.")
    for key in ("POSTGRES_USER", "POSTGRES_DB", "DOCTA_DEV_IAM_DB_USER", "DOCTA_DEV_IAM_DB_NAME"):
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", values.get(key, "")):
            raise DevError(f"Invalid {key}; use a lowercase PostgreSQL identifier.")
    for key in (
        "POSTGRES_PASSWORD",
        "MINIO_ROOT_PASSWORD",
        "DOCTA_DEV_IAM_DB_PASSWORD",
        "DOCTA_DEV_LOGIN_PASSWORD",
        "DOCTA_WEB_SESSION_SECRET",
        "DOCTA_DEV_IAM_MASTERKEY",
    ):
        if not values.get(key) or any(char in values[key] for char in "\r\n\x00"):
            raise DevError(f"Missing or invalid secret in {key}; values are not displayed.")
    if len(values["DOCTA_WEB_SESSION_SECRET"]) < 32:
        raise DevError("DOCTA_WEB_SESSION_SECRET requires at least 32 characters.")
    if len(values["DOCTA_DEV_IAM_MASTERKEY"].encode("utf-8")) != 32:
        raise DevError("DOCTA_DEV_IAM_MASTERKEY requires exactly 32 bytes.")
    if len(values.get("MINIO_ROOT_USER", "")) < 3 or len(values["MINIO_ROOT_PASSWORD"]) < 8:
        raise DevError("MinIO requires an access key of 3+ and a secret of 8+ characters.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", values.get("DOCTA_S3_BUCKET", "")):
        raise DevError("Use a document bucket of 3-63 lowercase letters, digits or hyphens.")
    for key in (
        "DOCTA_DEV_LOGIN_USERNAME",
        "DOCTA_DEV_LOGIN_EMAIL",
        "DOCTA_DEV_ORG_NAME",
        "DOCTA_DEV_PROJECT_NAME",
        "DOCTA_DEV_APP_NAME",
        "DOCTA_DEV_BOOTSTRAP_USERNAME",
        "DOCTA_DEV_LOGIN_SERVICE_USERNAME",
    ):
        if not values.get(key, "").strip() or any(ord(char) < 32 for char in values[key]):
            raise DevError(f"Missing or invalid {key}.")
    if "@" not in values["DOCTA_DEV_LOGIN_EMAIL"]:
        raise DevError("The local administrator needs an email address.")
    mismatched = [key for key, value in derived_values(values).items() if values.get(key) != value]
    if mismatched:
        raise DevError("Managed endpoints/credentials disagree: " + ", ".join(mismatched) + ".")
    settings = {
        key.removeprefix("DOCTA_").lower(): value
        for key, value in values.items()
        if key.removeprefix("DOCTA_").lower() in Settings.model_fields and value != ""
    }
    if not values.get("DOCTA_OIDC_AUDIENCE"):
        for key in ("oidc_issuer", "oidc_jwks_url"):
            settings.pop(key, None)  # Bootstrap has yet to allocate the project ID.
    try:
        DeveloperSettings(**settings)
    except ValidationError as error:
        fields = sorted({str(item["loc"][0]) for item in error.errors() if item["loc"]})
        raise DevError(
            "Invalid application configuration"
            + (" (" + ", ".join(fields) + ")" if fields else "")
            + "; check tutor endpoint/model/key, provider/schema and timeouts. Values are hidden."
        ) from error


def new_environment(root: Path) -> dict[str, str]:
    """Neutral defaults only. Root .env is never imported implicitly."""
    values = read_environment(root / ".env.example")
    for key in (
        *TUTOR_FIELDS,
        "DOCTA_OIDC_AUDIENCE",
        "DOCTA_DEV_OIDC_PROJECT_ID",
        "DOCTA_DEV_OIDC_CLIENT_ID",
        "DOCTA_WEB_OIDC_CLIENT_ID",
        "DOCTA_WEB_OIDC_SCOPES",
    ):
        values.pop(key, None)
    suffix = secrets.token_hex(4)
    values |= PORT_DEFAULTS | {
        "DOCTA_DEV_MANAGED": "1",
        "DOCTA_ENVIRONMENT": "development",
        "POSTGRES_DB": "docta_" + suffix,
        "POSTGRES_USER": "docta_" + suffix,
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "MINIO_ROOT_USER": "docta-" + suffix,
        "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(32),
        "DOCTA_S3_BUCKET": "docta-documents-" + suffix,
        "DOCTA_WEB_SESSION_SECRET": secrets.token_urlsafe(48),
        "DOCTA_DEV_LOGIN_USERNAME": "developer-" + suffix + "@docta.local",
        "DOCTA_DEV_LOGIN_EMAIL": "developer-" + suffix + "@docta.local",
        "DOCTA_DEV_LOGIN_PASSWORD": secrets.token_urlsafe(24) + "Aa1!",
        "DOCTA_DEV_PROJECT_NAME": "Docta Dev " + suffix,
        "DOCTA_DEV_APP_NAME": "Docta PKCE " + suffix,
        "DOCTA_DEV_ORG_NAME": "Docta Development " + suffix,
        "DOCTA_DEV_BOOTSTRAP_USERNAME": "bootstrap-" + suffix,
        "DOCTA_DEV_LOGIN_SERVICE_USERNAME": "login-" + suffix,
        "DOCTA_DEV_IAM_DB_USER": "iam_" + suffix,
        "DOCTA_DEV_IAM_DB_NAME": "iam_" + suffix,
        "DOCTA_DEV_IAM_DB_PASSWORD": secrets.token_urlsafe(32),
        "DOCTA_DEV_IAM_MASTERKEY": secrets.token_hex(16),
        "DOCTA_DEV_PAT_EXPIRATION": (datetime.now(UTC) + timedelta(days=365)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    return values | derived_values(values)


def guard_environment_creation(root: Path = ROOT) -> None:
    """A missing ignored environment does not imply that Docker data is new."""
    if (profile_path(".env", root)).is_file():
        return
    try:
        result = subprocess.run(
            ["docker", "volume", "ls", "--filter", "name=docta-dev", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise DevError(
            "Cannot check existing development volumes; make Docker available before setup. "
            "No new environment was generated."
        ) from error
    durable_volumes = {
        "docta-dev-iam_bootstrap",
        "docta-dev-iam_postgres-data",
        "docta-dev_postgres-data",
        "docta-dev_minio-data",
        "docta-dev_redis-data",
    }
    if durable_volumes.intersection(result.stdout.splitlines()):
        raise DevError(
            "Existing Docta development volumes were found but .devcontainer/.env is missing. "
            "Restore their original managed environment and identity state before setup. "
            "No new credentials were generated; volumes were preserved."
        )


def initialize(root: Path = ROOT) -> dict[str, str]:
    path = profile_path(".env", root)
    if not path.is_file():
        raise DevError("Run npm run dev:configure inside the container to configure development.")
    values = normalize_environment(read_environment(path))
    validate_environment(values)
    return values


def guard_identity_recovery() -> None:
    """New identity subjects must not be attached to retained application data."""
    try:
        result = subprocess.run(
            ["docker", "volume", "ls", "--filter", "name=docta-dev_", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise DevError(
            "Cannot check Docta data volumes; make Docker available before identity recovery. "
            "Local identity state was preserved."
        ) from error
    if {
        "docta-dev_postgres-data",
        "docta-dev_minio-data",
        "docta-dev_redis-data",
    }.intersection(result.stdout.splitlines()):
        raise DevError(
            "ZITADEL identity recovery requires absent Docta data volumes. Retained data uses "
            "the original identity subjects; restore the matching IAM database/bootstrap volumes "
            "and private configuration instead. No volumes or identity state were replaced."
        )


def prompt(label: str, default: str = "", *, choices: tuple[str, ...] = ()) -> str:
    while True:
        value = input(label + (f" [{default}]" if default else "") + ": ").strip() or default
        if (
            value
            and not any(ord(char) < 32 for char in value)
            and (not choices or value in choices)
        ):
            return value
        print("Enter a valid value" + (": " + ", ".join(choices) if choices else "") + ".")


def prompt_secret(label: str, default: str = "") -> str:
    import warnings

    while True:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                suffix = " [Enter keeps/generated secret]" if default else ""
                value = getpass.getpass(label + suffix + ": ")
            except getpass.GetPassWarning as error:
                raise DevError("This terminal does not support hidden secret input.") from error
        value = value or default
        if value and not any(char in value for char in "\r\n\x00"):
            return value
        print("A nonempty secret is required; its value will not be displayed.")


def configure_tutor(values: dict[str, str], *, existing: bool) -> dict[str, str]:
    print("Tutor: keep (existing only), google, llama_cpp, unsloth, custom, none.")
    choices = ("google", "llama_cpp", "unsloth", "custom", "none")
    if existing:
        choices = ("keep", *choices)
    profile = prompt("Tutor service", "keep" if existing else "none", choices=choices)
    if profile == "keep":
        return values
    old = values.copy()
    values = {key: value for key, value in values.items() if key not in TUTOR_FIELDS}
    values["DOCTA_TUTOR_PROVIDER"] = "unsloth" if profile == "unsloth" else "chat_completions"
    values["DOCTA_TUTOR_SCHEMA_PROFILE"] = (
        "llama_cpp" if profile in ("llama_cpp", "unsloth") else "standard"
    )
    if profile == "none":
        return values
    # A service preset is a protocol default, never a selected developer model or API key.
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        if profile == "google"
        else ""
    )
    values["DOCTA_TUTOR_ENDPOINT_URL"] = prompt("Full Chat Completions endpoint", endpoint)
    values["DOCTA_TUTOR_MODEL"] = prompt("Model ID / server alias")
    same_endpoint = values["DOCTA_TUTOR_ENDPOINT_URL"] == old.get("DOCTA_TUTOR_ENDPOINT_URL")
    values["DOCTA_TUTOR_API_KEY"] = prompt_secret(
        "Tutor API key (local servers still require an explicit value)",
        old.get("DOCTA_TUTOR_API_KEY", "") if same_endpoint else "",
    )
    if profile == "custom":
        values["DOCTA_TUTOR_PROVIDER"] = prompt(
            "Provider", "chat_completions", choices=("chat_completions", "unsloth")
        )
        values["DOCTA_TUTOR_SCHEMA_PROFILE"] = prompt(
            "Sampling schema", "standard", choices=("standard", "llama_cpp")
        )
    for key, label, default in (
        ("DOCTA_TUTOR_TIMEOUT_SECONDS", "Tutor timeout seconds (1-179)", "45"),
        ("DOCTA_MESSAGE_TIMEOUT_SECONDS", "Message deadline (5-180s, above tutor timeout)", "90"),
        ("DOCTA_TUTOR_MAX_OUTPUT_TOKENS", "Completion budget (128-8000)", "2000"),
    ):
        values[key] = prompt(label, old.get(key, default))
    return values


def show_status(values: dict[str, str]) -> None:
    print(
        f"Private configuration: {profile_path('.env')}; "
        f"recovery state: {profile_path('state')}."
    )
    print(
        "Managed profile: "
        + (
            "OIDC registration recorded (public availability is checked by dev:all)."
            if values.get("DOCTA_WEB_OIDC_CLIENT_ID")
            else "OIDC registration pending; run npm run dev:setup."
        )
    )
    print(
        f"Web: {values['DOCTA_WEB_ORIGIN']}; API: {values['DOCTA_API_BASE_URL']}; "
        f"ZITADEL: {values['DOCTA_OIDC_ISSUER']}/ui/console."
    )
    for label, key in (
        ("Administrator login", "DOCTA_DEV_LOGIN_USERNAME"),
        ("Bootstrap service account", "DOCTA_DEV_BOOTSTRAP_USERNAME"),
        ("Login V2 service account", "DOCTA_DEV_LOGIN_SERVICE_USERNAME"),
        ("Docta PostgreSQL role", "POSTGRES_USER"),
        ("Docta database", "POSTGRES_DB"),
        ("IAM PostgreSQL role", "DOCTA_DEV_IAM_DB_USER"),
        ("IAM database", "DOCTA_DEV_IAM_DB_NAME"),
        ("ZITADEL project", "DOCTA_DEV_PROJECT_NAME"),
        ("Project ID", "DOCTA_OIDC_AUDIENCE"),
        ("OIDC Client ID", "DOCTA_WEB_OIDC_CLIENT_ID"),
    ):
        print(f"{label}: {values.get(key) or 'allocated by dev:setup'}.")
    print("MinIO access/secret keys: MINIO_ROOT_USER / MINIO_ROOT_PASSWORD (private environment).")
    print(
        "Secrets: DOCTA_DEV_LOGIN_PASSWORD, POSTGRES_PASSWORD, MINIO_ROOT_PASSWORD, "
        "DOCTA_DEV_IAM_DB_PASSWORD, DOCTA_DEV_IAM_MASTERKEY, DOCTA_WEB_SESSION_SECRET "
        "(private environment); admin PAT: state/admin.pat."
    )
    enabled = all(values.get(key) for key in TUTOR_FIELDS)
    print(
        f"Tutor: {'configured' if enabled else 'not configured'}; "
        "API key: DOCTA_TUTOR_API_KEY (never displayed)."
    )


def show_services() -> None:
    for project in ("docta-dev-iam", "docta-dev"):
        try:
            result = subprocess.run(
                ["docker", "ps", "--all", "--filter", f"label=com.docker.compose.project={project}",
                 "--format", '{{.Label "com.docker.compose.service"}}: {{.Status}}'],
                capture_output=True, text=True, timeout=10, check=True,
                env=process_environment({}),
            )
            print(f"{project}:\n{result.stdout.strip() or 'No containers; run npm run dev:setup.'}")
        except (OSError, subprocess.SubprocessError):
            print(f"{project}: Docker status unavailable.")


def configure(root: Path = ROOT, *, source: Path | None = None) -> dict[str, str]:
    path = profile_path(".env", root)
    if source is not None:
        if path.exists():
            raise DevError("A managed environment already exists; import cannot replace it.")
        values = normalize_environment(read_environment(source))
        validate_environment(values)
        private_write(path, source.read_text(encoding="utf-8"))
        print("Original managed credentials restored. Restore matching identity state too.")
        show_status(values)
        return values
    if not sys.stdin.isatty():
        raise DevError("Run dev:configure in an interactive terminal with hidden secret input.")
    guard_environment_creation(root)
    existing = path.is_file()
    values = normalize_environment(read_environment(path)) if existing else new_environment(root)
    if existing:
        print(
            "Reusing infrastructure, users and identity. Only tutor settings can be edited; "
            "service password rotation requires updating the service itself."
        )
    else:
        values["DOCTA_DEV_LOGIN_USERNAME"] = prompt(
            "Local ZITADEL administrator login/email", values["DOCTA_DEV_LOGIN_USERNAME"]
        )
        values["DOCTA_DEV_LOGIN_EMAIL"] = values["DOCTA_DEV_LOGIN_USERNAME"]
        values["DOCTA_DEV_LOGIN_PASSWORD"] = prompt_secret(
            "Local administrator password (8+ characters, upper/lower/digit/symbol)",
            values["DOCTA_DEV_LOGIN_PASSWORD"],
        )
        password = values["DOCTA_DEV_LOGIN_PASSWORD"]
        if len(password) < 8 or any(
            not re.search(pattern, password)
            for pattern in (r"[A-Z]", r"[a-z]", r"[0-9]", r"[^A-Za-z0-9]")
        ):
            raise DevError("Administrator password does not meet the policy; nothing was saved.")
        mode = prompt(
            "Infrastructure credentials: generated or custom",
            "generated",
            choices=("generated", "custom"),
        )
        if mode == "custom":
            for key, label in (
                ("POSTGRES_DB", "Docta database name"),
                ("POSTGRES_USER", "Docta database role"),
                ("MINIO_ROOT_USER", "MinIO access key"),
                ("DOCTA_S3_BUCKET", "Document bucket"),
                ("DOCTA_DEV_IAM_DB_USER", "ZITADEL database role"),
                ("DOCTA_DEV_IAM_DB_NAME", "ZITADEL database name"),
                ("DOCTA_DEV_ORG_NAME", "ZITADEL organization"),
                ("DOCTA_DEV_PROJECT_NAME", "ZITADEL project"),
                ("DOCTA_DEV_APP_NAME", "ZITADEL application"),
                ("DOCTA_DEV_BOOTSTRAP_USERNAME", "Provisioning service account"),
                ("DOCTA_DEV_LOGIN_SERVICE_USERNAME", "Login V2 service account"),
            ):
                values[key] = prompt(label, values[key])
            for key, label in (
                ("POSTGRES_PASSWORD", "Docta database password"),
                ("MINIO_ROOT_PASSWORD", "MinIO secret key (8+ characters)"),
                ("DOCTA_DEV_IAM_DB_PASSWORD", "ZITADEL database password"),
                ("DOCTA_DEV_IAM_MASTERKEY", "ZITADEL master key (exactly 32 bytes)"),
                ("DOCTA_WEB_SESSION_SECRET", "Web session secret (32+ characters)"),
            ):
                values[key] = prompt_secret(label, values[key])
        if prompt("Customize local ports? yes/no", "no", choices=("yes", "no")) == "yes":
            for key, default in PORT_DEFAULTS.items():
                values[key] = prompt(key, default)
        values |= derived_values(values)
    values = configure_tutor(values, existing=existing)
    validate_environment(values)
    if existing:
        original = path.read_text(encoding="utf-8")
        private_write(
            profile_path("state", root) / ("env-before-configure-" + secrets.token_hex(8)),
            original,
        )
    else:
        original = (root / ".env.example").read_text(encoding="utf-8")
    private_write(
        path,
        environment_text(
            original, values, remove=tuple(key for key in TUTOR_FIELDS if key not in values)
        ),
    )
    print("Configuration saved; no services/model calls started. Run npm run dev:setup first.")
    show_status(values)
    return values


def run(
    command: list[str],
    values: dict[str, str],
    *,
    quiet: bool = False,
    inherit_identity: bool = True,
) -> None:
    environment = process_environment(values, clean=not inherit_identity
                                      or values.get("DOCTA_DEV_MANAGED") == "1")
    try:
        subprocess.run(
            command, cwd=ROOT, env=environment, check=True, capture_output=quiet, text=True
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise DevError(
            f"Command failed ({Path(command[0]).name}); check availability/configuration."
        ) from error


def process_environment(values: dict[str, str], *, clean: bool = True) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items()
        if not clean or not key.startswith(("DOCTA_", "POSTGRES_", "MINIO_", "ZITADEL_"))
    }
    return environment | values


def runtime_values(values: dict[str, str], component: str = "api") -> dict[str, str]:
    """IAM administration credentials belong only to development provisioning."""
    allowed = {"DOCTA_" + name.upper() for name in Settings.model_fields}
    if component == "worker":
        allowed = {key for key in allowed if not key.startswith(("DOCTA_OIDC_", "DOCTA_TUTOR_"))}
    elif component == "web":
        allowed = {
            "DOCTA_API_BASE_URL", "DOCTA_WEB_ORIGIN", "DOCTA_WEB_SESSION_SECRET",
            "DOCTA_WEB_OIDC_CLIENT_ID", "DOCTA_WEB_OIDC_SCOPES",
            "DOCTA_OIDC_ISSUER", "DOCTA_OIDC_AUDIENCE",
        }
    elif component == "migration":
        allowed = {"DOCTA_DATABASE_URL", "DOCTA_MINIO_HEALTH_URL"}
    elif component == "helper":
        allowed = {"DOCTA_API_BASE_URL", "DOCTA_OIDC_ISSUER", "DOCTA_OIDC_AUDIENCE",
                   "DOCTA_OIDC_JWKS_URL"}
        allowed |= {key for key in values if key.startswith("DOCTA_DEV_OIDC_")}
    return {key: value for key, value in values.items() if key in allowed} | {
        "DOCTA_DEV_MANAGED": "1"
    }


def compose(stack: str) -> list[str]:
    if stack == "test":
        return [
            "docker", "compose", "-p", "docta-test", "-f", str(ROOT / "infra/compose.test.yaml")
        ]
    base = ["docker", "compose", "--env-file", str(profile_path(".env"))]
    if stack == "iam":
        return [*base, "-p", "docta-dev-iam", "-f", str(ROOT / ".devcontainer/zitadel.yaml")]
    return [
        *base,
        "-p",
        "docta-dev",
        "-f",
        str(ROOT / "infra/compose.yaml"),
        "-f",
        str(ROOT / ".devcontainer/infra.override.yaml"),
    ]


def identifier(document: dict[str, Any], key: str) -> str:
    value = document.get(key) if isinstance(document, dict) else None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9@._-]{1,200}", value):
        raise DevError("ZITADEL returned a missing or invalid resource identifier.")
    return value


class Provisioner:
    """Version-pinned management API with durable ambiguous-create protection."""

    def __init__(
        self, client: httpx.Client, state_path: Path, *, env_path: Path | None = None
    ) -> None:
        self.client = client
        self.path = state_path
        self.env_path = env_path
        self.state: dict[str, str] = (
            json.loads(state_path.read_text()) if state_path.exists() else {}
        )

    def save(self) -> None:
        private_write(self.path, json.dumps(self.state, indent=2) + "\n")

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        api: str = "/management/v1",
    ) -> dict:
        try:
            response = self.client.request(method, api + path, json=body)
            response.raise_for_status()
            document = response.json()
            if not isinstance(document, dict):
                raise ValueError
            return document
        except httpx.HTTPStatusError as error:
            raise IAMHTTPError(
                f"ZITADEL {method} {path} failed (HTTP {error.response.status_code}); "
                "no mutation was retried. Check local readiness/admin permissions.",
                error.response.status_code,
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise DevError(
                "ZITADEL provisioning failed; no mutation was retried. "
                "Check service readiness and the local admin PAT."
            ) from error

    def find(self, path: str, name: str) -> dict | None:
        document = self.request(
            "POST",
            path + "/_search",
            {
                "query": {"limit": 100},
                "queries": [{"nameQuery": {"name": name, "method": "TEXT_QUERY_METHOD_EQUALS"}}],
            },
        )
        rows = document.get("result", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise DevError("Invalid ZITADEL resource search result.")
        matches = [row for row in rows if row.get("name") == name]
        if len(matches) > 1 or int(document.get("details", {}).get("totalResult", 0)) > 100:
            raise DevError("Ambiguous ZITADEL resources; resolve them before provisioning.")
        return matches[0] if matches else None

    def ensure(
        self,
        path: str,
        name: str,
        *,
        state_key: str,
        create_path: str,
        body: dict[str, Any],
        result_key: str,
    ) -> str:
        if state_key in self.state:
            return identifier(self.state, state_key)
        found = self.find(path, name)
        if found:
            resource_id = identifier(found, "id")
        else:
            if self.state.get("pending"):
                raise DevError(
                    "A previous creation has an unknown outcome. Inspect the local "
                    "ZITADEL project/application before retrying; state was preserved."
                )
            self.state["pending"] = state_key
            self.save()
            created = self.request("POST", create_path, {"name": name} | body)
            resource_id = identifier(created, result_key)
        self.state[state_key] = resource_id
        self.state.pop("pending", None)
        self.save()
        return resource_id

    def recover(self, instance_id: str) -> None:
        guard_identity_recovery()
        suffix = secrets.token_hex(8)
        if self.path.exists():
            private_write(
                self.path.with_name(f"identity-before-recovery-{suffix}.json"),
                self.path.read_text(encoding="utf-8"),
            )
        if self.env_path is not None:
            private_write(
                self.path.with_name(f"env-before-identity-recovery-{suffix}"),
                self.env_path.read_text(encoding="utf-8"),
            )
        self.state = {"instance_id": instance_id}
        self.save()
        print("Fresh ZITADEL instance: previous local identity archived; credentials preserved.")

    def provision(
        self, values: dict[str, str], *, recover_identity: bool = False
    ) -> dict[str, str]:
        instance_id = identifier(
            self.request("GET", "/instances/me", api="/admin/v1").get("instance", {}), "id"
        )
        # A lost state file must not silently discard the environment's previous project ID.
        if not self.state and values.get("DOCTA_DEV_OIDC_PROJECT_ID"):
            self.state["project_id"] = identifier(values, "DOCTA_DEV_OIDC_PROJECT_ID")
        previous_instance = self.state.get("instance_id")
        if previous_instance and previous_instance != instance_id:
            self.recover(instance_id)
        elif recover_identity:
            if previous_instance or self.state.get("pending") or "project_id" not in self.state:
                raise DevError(
                    "Identity recovery requires legacy state with a missing project and no "
                    "pending creation. Inspect the named resource or restore matching IAM "
                    "volumes; local state was preserved."
                )
            try:
                self.request("GET", "/projects/" + identifier(self.state, "project_id"))
            except IAMHTTPError as error:
                if error.status_code != 404:
                    raise
                self.recover(instance_id)
            else:
                raise DevError("Saved ZITADEL project still exists; local state was preserved.")
        if not self.state:
            self.state["instance_id"] = instance_id
            self.save()
        project_id = self.ensure(
            "/projects",
            values["DOCTA_DEV_PROJECT_NAME"],
            state_key="project_id",
            create_path="/projects",
            body={},
            result_key="id",
        )
        try:
            project = self.request("GET", f"/projects/{project_id}").get("project", {})
        except IAMHTTPError as error:
            if error.status_code != 404:
                raise
            message = (
                "Saved ZITADEL project no longer exists. Local files still reference old IDs; "
                "Docta infrastructure was not started. "
                if not previous_instance
                else "Saved project is missing from the same ZITADEL instance. "
            )
            raise DevError(
                message
                + (
                    "After intentionally deleting both IAM and Docta data volumes, run "
                    "npm run dev:recover-identity. Otherwise restore the matching IAM backup."
                    if not previous_instance
                    else "Inspect the named resource or restore the matching IAM backup; "
                    "local state was preserved."
                )
            ) from error
        if (
            project.get("name") != values["DOCTA_DEV_PROJECT_NAME"]
            or project.get("state") != "PROJECT_STATE_ACTIVE"
        ):
            raise DevError("Saved project is missing, inactive or does not match this workspace.")
        if "instance_id" not in self.state:
            self.state["instance_id"] = instance_id
            self.save()
        config = {
            "redirectUris": [
                values["DOCTA_WEB_ORIGIN"] + "/auth/callback",
                values["DOCTA_DEV_OIDC_REDIRECT_URI"],
            ],
            "postLogoutRedirectUris": [values["DOCTA_WEB_ORIGIN"]],
            "responseTypes": ["OIDC_RESPONSE_TYPE_CODE"],
            "grantTypes": ["OIDC_GRANT_TYPE_AUTHORIZATION_CODE"],
            "appType": "OIDC_APP_TYPE_WEB",
            "authMethodType": "OIDC_AUTH_METHOD_TYPE_NONE",
            "devMode": True,
            "accessTokenType": "OIDC_TOKEN_TYPE_JWT",
        }
        apps_path = f"/projects/{project_id}/apps"
        app_id = self.ensure(
            apps_path,
            values["DOCTA_DEV_APP_NAME"],
            state_key="app_id",
            create_path=apps_path + "/oidc",
            body=config | {"version": "OIDC_VERSION_1_0"},
            result_key="appId",
        )
        app = self.request("GET", apps_path + f"/{app_id}").get("app", {})
        if (
            app.get("name") != values["DOCTA_DEV_APP_NAME"]
            or app.get("state") != "APP_STATE_ACTIVE"
        ):
            raise DevError("Saved application is missing, inactive or mismatched.")
        actual = app.get("oidcConfig", {})
        if not actual:
            raise DevError("Saved application is not OIDC.")
        # Management API protobuf JSON omits Web, the zero/default app type.
        actual.setdefault("appType", "OIDC_APP_TYPE_WEB")
        if any(actual.get(key) != value for key, value in config.items()):
            self.request("PUT", apps_path + f"/{app_id}/oidc_config", config)
            actual = (
                self.request("GET", apps_path + f"/{app_id}").get("app", {}).get("oidcConfig", {})
            )
            actual.setdefault("appType", "OIDC_APP_TYPE_WEB")
            if any(actual.get(key) != value for key, value in config.items()):
                raise DevError("ZITADEL did not confirm the requested PKCE/JWT configuration.")
        client_id = identifier(actual, "clientId")
        return {
            "DOCTA_OIDC_AUDIENCE": project_id,
            "DOCTA_DEV_OIDC_PROJECT_ID": project_id,
            "DOCTA_WEB_OIDC_CLIENT_ID": client_id,
            "DOCTA_DEV_OIDC_CLIENT_ID": client_id,
            "DOCTA_WEB_OIDC_SCOPES": (
                f"openid profile urn:zitadel:iam:org:project:id:{project_id}:aud"
            ),
        }


def start_iam(values: dict[str, str]) -> None:
    try:
        run([*compose("iam"), "up", "-d", "--wait", "--wait-timeout", "600"], values, quiet=True)
    except DevError as error:
        message = "ZITADEL startup failed; check the docta-dev-iam service status."
        # Classify known failures without exposing raw logs, DSNs or generated credentials.
        try:
            result = subprocess.run(
                [*compose("iam"), "logs", "--no-color", "--tail", "50", "zitadel-api"],
                cwd=ROOT,
                env=process_environment(values),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if "password authentication failed" in (result.stdout + result.stderr).lower():
                message = (
                    "ZITADEL startup failed: PostgreSQL rejected its password. Existing Docker "
                    "volumes require the original .devcontainer/.env, including the IAM master "
                    "key. Windows and WSL checkouts share these development Compose projects; "
                    "preserve the managed environment and identity state when moving a checkout."
                )
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise DevError(message) from error


def bootstrap(values: dict[str, str], *, recover_identity: bool = False) -> dict[str, str]:
    run(["docker", "info"], values, quiet=True)
    for stack in ("iam", "infra"):
        run([*compose(stack), "config", "--quiet"], values, quiet=True)
    print(
        "Starting isolated ZITADEL (first image pull/setup can take several minutes)...", flush=True
    )
    start_iam(values)
    state_dir = profile_path("state")
    state_dir.mkdir(parents=True, exist_ok=True)
    pat_path = state_dir / "admin.pat"
    run(
        [*compose("iam"), "cp", "zitadel-api:/bootstrap/admin.pat", str(pat_path)],
        values,
        quiet=True,
    )
    pat_path.chmod(0o600)
    token = pat_path.read_text().strip()
    if not token:
        raise DevError("Local bootstrap PAT is empty; preserve volumes and inspect initialization.")
    with httpx.Client(
        base_url=values["DOCTA_OIDC_ISSUER"],
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        updates = Provisioner(
            client, state_dir / "identity.json", env_path=profile_path(".env")
        ).provision(values, recover_identity=recover_identity)
    update_env(profile_path(".env"), updates)
    values |= updates
    print("Starting isolated PostgreSQL, Redis and MinIO; applying migrations...", flush=True)
    run([*compose("infra"), "up", "-d", "--wait"], values, quiet=True)
    run([sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"],
        runtime_values(values, "migration"), quiet=True)
    print("Infrastructure ready. Start servers with npm run dev:all.")
    show_status(values)
    return values


def registered_identity(values: dict[str, str]) -> None:
    project = values.get("DOCTA_OIDC_AUDIENCE")
    client = values.get("DOCTA_WEB_OIDC_CLIENT_ID")
    if not project or not client:
        raise DevError("OIDC is not registered. Run npm run dev:setup before dev:all.")
    if (project != values.get("DOCTA_DEV_OIDC_PROJECT_ID")
            or client != values.get("DOCTA_DEV_OIDC_CLIENT_ID")):
        raise DevError("Saved OIDC configuration disagrees. Run npm run dev:setup.")


def verify_oidc(values: dict[str, str]) -> None:
    """Only public discovery and signing keys; daily startup never needs an admin PAT."""
    try:
        with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
            response = client.get(values["DOCTA_OIDC_ISSUER"] + "/.well-known/openid-configuration")
            response.raise_for_status()
            document = response.json()
            if not isinstance(document, dict):
                raise ValueError
            code_methods = document.get("code_challenge_methods_supported")
            auth_methods = document.get("token_endpoint_auth_methods_supported")
            if (document.get("issuer") != values["DOCTA_OIDC_ISSUER"]
                    or document.get("jwks_uri") != values["DOCTA_OIDC_JWKS_URL"]
                    or not isinstance(code_methods, list)
                    or not all(isinstance(item, str) for item in code_methods)
                    or "S256" not in code_methods
                    or not isinstance(auth_methods, list)
                    or not all(isinstance(item, str) for item in auth_methods)
                    or "none" not in auth_methods):
                raise ValueError
            response = client.get(values["DOCTA_OIDC_JWKS_URL"])
            response.raise_for_status()
            jwks = response.json()
            keys = jwks.get("keys") if isinstance(jwks, dict) else None
            usable_key = False
            if isinstance(keys, list):
                for key in keys:
                    if not isinstance(key, dict) or key.get("kty") != "RSA":
                        continue
                    if not isinstance(key.get("kid"), str) or not key["kid"]:
                        continue
                    try:
                        PyJWK.from_dict(key)
                    except Exception:
                        continue
                    usable_key = True
                    break
            if not usable_key:
                raise ValueError
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise DevError("IAM discovery/signing keys are unavailable or incompatible. "
                       "Check the provider; no identity was changed.") from None


def verify_schema(values: dict[str, str]) -> None:
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "apps/api/alembic.ini")))
    expected = set(scripts.get_heads())
    try:
        with psycopg.connect(values["DOCTA_DATABASE_URL"], connect_timeout=5) as connection:
            actual = {
                row[0] for row in connection.execute("SELECT version_num FROM alembic_version")
            }
    except psycopg.Error:
        raise DevError("Cannot verify the database schema. Check PostgreSQL and run "
                       "npm run dev:setup if this environment has not been prepared.") from None
    if actual != expected:
        raise DevError(
            "Database schema does not match this checkout. Stop applications and "
            "run npm run dev:setup from a compatible branch; no downgrade was attempted."
        )


def start_existing(values: dict[str, str]) -> None:
    registered_identity(values)
    for stack, project, required in (
        ("iam", "docta-dev-iam", {"proxy", "postgres", "zitadel-api", "zitadel-login"}),
        ("infra", "docta-dev", {"postgres", "redis", "minio"}),
    ):
        try:
            result = subprocess.run(
                ["docker", "ps", "--all", "--filter", f"label=com.docker.compose.project={project}",
                 "--format", '{{.Label "com.docker.compose.service"}}'],
                capture_output=True, text=True, timeout=15, check=True,
                env=process_environment({}),
            )
        except (OSError, subprocess.SubprocessError):
            raise DevError(
                "Cannot inspect development services. Check Docker availability."
            ) from None
        if not required.issubset(result.stdout.splitlines()):
            raise DevError("Development containers are missing. Run npm run dev:setup; "
                           "existing configuration and volumes will be reused.")
        run([*compose(stack), "start", "--wait", "--wait-timeout", "120"], values, quiet=True)
    verify_oidc(values)
    verify_schema(values)


def serve(values: dict[str, str], *, lock_descriptor: int) -> None:
    commands = [
        [
            sys.executable,
            "-m",
            "uvicorn",
            "docta_api.main:app",
            "--app-dir",
            "apps/api/src",
            "--host",
            "127.0.0.1",
            "--port",
            values["DOCTA_DEV_API_PORT"],
            "--reload",
            "--reload-dir",
            "apps/api/src",
        ],
        [sys.executable, "-m", "docta_api.worker"],
        [
            "npm",
            "run",
            "dev",
            "--workspace",
            "@docta/web",
            "--",
            "--hostname",
            "127.0.0.1",
            "--port",
            values["DOCTA_DEV_WEB_PORT"],
        ],
    ]
    processes: list[subprocess.Popen] = []
    try:
        for command, component in zip(commands, ("api", "worker", "web"), strict=True):
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=process_environment(runtime_values(values, component)),
                    start_new_session=True,
                    pass_fds=(lock_descriptor,),
                )
            )
        while True:
            if any(process.poll() is not None for process in processes):
                raise DevError("A development process exited; all three processes were stopped.")
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def check(values: dict[str, str], *, full: bool) -> None:
    # Configuration tests require absent credentials; integration fixtures own their scope.
    test_values = {
        key: value
        for key, value in (os.environ | values).items()
        if key.startswith("DOCTA_TEST_") or key == "DOCTA_E2E_BROWSER_CHANNEL"
    }
    # Import-time API settings must also work in a clean checkout with no root .env.
    # Integration fixtures replace these endpoints with their per-run resources.
    test_values |= {
        "DOCTA_DEV_MANAGED": "1",  # Test children must also ignore the developer's root .env.
        "DOCTA_DATABASE_URL": "postgresql://docta_test:docta_test_only@127.0.0.1:"
        f"{test_values.get('DOCTA_TEST_POSTGRES_PORT', '55432')}/postgres",
        "DOCTA_MINIO_HEALTH_URL": "http://127.0.0.1:"
        f"{test_values.get('DOCTA_TEST_MINIO_PORT', '59000')}/minio/health/ready",
    }
    commands = [
        [sys.executable, "-m", "pytest", "tests/unit", "-p", "no:cacheprovider"],
        [sys.executable, "-m", "ruff", "check", "--no-cache", "apps/api/src", "scripts", "tests"],
        ["npm", "run", "check:web"],
    ]
    for command in commands:
        run(command, test_values, inherit_identity=False)
    if full:
        test_overrides = {
            key: value for key, value in test_values.items() if key.startswith("DOCTA_TEST_")
        }
        run([*compose("test"), "up", "-d", "--wait"], values | test_overrides, quiet=True)
        run(["docker", "build", "-f", "infra/worker.Dockerfile", "-t", "docta-worker:latest", "."],
            test_values, inherit_identity=False)
        run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"],
            test_values,
            inherit_identity=False,
        )
    run(["git", "diff", "--check"], values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("bootstrap", "start", "stop", "post-start", "status", "recover-identity"):
        actions.add_parser(action)
    settings = actions.add_parser("configure", aliases=["init"])
    settings.add_argument("--from-env", type=Path, help="Restore an original managed environment")
    checks = actions.add_parser("check")
    checks.add_argument(
        "--full", action="store_true", help="Include serial integration/browser checks"
    )
    execute = actions.add_parser("run")
    execute.add_argument("command", nargs=argparse.REMAINDER, help="Command after run --")
    args = parser.parse_args()
    try:
        if args.action in ("post-start", "status"):
            if profile_path(".env").is_file():
                show_status(initialize())
                print("Start this checkout with npm run dev:all. Use dev:setup for migrations.")
            else:
                print("Tools ready. Run npm run dev:setup in an interactive terminal. "
                      "Existing checkout configuration will be preserved and imported if present.")
            if args.action == "status":
                show_services()
            return 0
        if args.action == "check":
            check(initialize(), full=args.full)
            return 0
        if args.action == "run":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            if not command:
                raise DevError("Specify a command after run --.")
            command_values = initialize()
            safe_values = (
                runtime_values(command_values, "helper")
                if command_values.get("DOCTA_DEV_MANAGED") == "1"
                else command_values
            )
            run(command, safe_values)
            return 0
        with environment_lock(profile_directory(ROOT)) as descriptor:
            if args.action in ("configure", "init", "bootstrap", "recover-identity"):
                if migrate_legacy(ROOT):
                    print("Imported the original managed configuration and identity state; "
                          "checkout files were preserved.")
            if args.action in ("configure", "init"):
                configure(source=args.from_env)
                return 0
            guard_environment_creation()
            if args.action == "bootstrap" and not profile_path(".env").is_file():
                configure()
            values = initialize()
            if args.action == "bootstrap":
                bootstrap(values)
            elif args.action == "recover-identity":
                bootstrap(values, recover_identity=True)
            elif args.action == "start":
                start_existing(values)
                serve(values, lock_descriptor=descriptor)
            elif args.action == "stop":
                for stack in ("infra", "iam"):
                    run([*compose(stack), "stop"], values, quiet=True)
        return 0
    except (DevError, StateError, OSError, ValueError, KeyError, EOFError) as error:
        print(
            str(error) if isinstance(error, (DevError, StateError))
            else "Invalid local setup/state; preserved.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("Configuration cancelled; existing environment/data were preserved.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
