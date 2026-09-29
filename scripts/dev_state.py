"""Private development state shared by checkouts of one active environment."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import dotenv_values

# Mutable tutor settings and derived URLs do not identify the persisted environment.
# Compare only fields present in both profiles because older profiles lack newer keys.
PERSISTED_FIELDS = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "DOCTA_S3_BUCKET",
    "DOCTA_DEV_IAM_DB_USER",
    "DOCTA_DEV_IAM_DB_PASSWORD",
    "DOCTA_DEV_IAM_DB_NAME",
    "DOCTA_DEV_IAM_MASTERKEY",
    "DOCTA_DEV_LOGIN_USERNAME",
    "DOCTA_DEV_LOGIN_PASSWORD",
    "DOCTA_DEV_ORG_NAME",
    "DOCTA_DEV_BOOTSTRAP_USERNAME",
    "DOCTA_DEV_LOGIN_SERVICE_USERNAME",
    "DOCTA_DEV_OIDC_PROJECT_ID",
    "DOCTA_DEV_OIDC_CLIENT_ID",
    "DOCTA_WEB_OIDC_CLIENT_ID",
    "DOCTA_WEB_SESSION_SECRET",
)


class StateError(Exception):
    """Safe diagnostics that never include configuration values or file contents."""


def profile_directory(root: Path) -> Path:
    """Keep the legacy location usable outside the configured development container."""
    configured = os.environ.get("DOCTA_DEV_HOME")
    return Path(configured) if configured else root / ".devcontainer"


@contextmanager
def environment_lock(profile: Path) -> Iterator[int]:
    """Own the environment until this descriptor and inherited copies are closed.

    Long-lived children must receive the yielded descriptor through ``pass_fds``.
    Closing, rather than explicitly unlocking, preserves their lock if the parent dies.
    The caller holds this lock while migrating, configuring, starting or stopping.
    """
    descriptor: int | None = None
    try:
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(
            profile / ".environment.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StateError(
                "Another development command is using this environment. "
                "Stop its running application processes before switching checkouts."
            ) from None
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        raise StateError(
            "Cannot lock the private development profile; state was preserved."
        ) from None
    except StateError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _managed_values(path: Path) -> dict[str, str | None]:
    if path.is_symlink() or not path.is_file():
        raise StateError("Private development configuration must be a regular managed file.")
    # python-dotenv parser diagnostics contain line numbers only, never secret values.
    values = dict(dotenv_values(path, interpolate=False))
    if values.get("DOCTA_DEV_MANAGED") != "1":
        raise StateError("Existing development configuration is not managed; preserve it first.")
    return values


def _identity(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise StateError("Private identity state must be a regular file; state was preserved.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise StateError("Cannot read private identity state; state was preserved.") from None
    if not isinstance(value, dict):
        raise StateError("Invalid private identity state; state was preserved.")
    return value


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _source_fingerprint(legacy: Path) -> str:
    digest = hashlib.sha256()
    state = legacy / "state"
    if state.is_symlink():
        raise StateError("Legacy private state contains a symbolic link; preserve it first.")
    if state.exists() and not state.is_dir():
        raise StateError("Legacy private state must be a directory; state was preserved.")
    files = [legacy / ".env"]
    if state.exists():
        files.extend(sorted(state.rglob("*")))
    for path in files:
        if path.is_symlink():
            raise StateError("Legacy private state contains a symbolic link; preserve it first.")
        if path.is_dir():
            continue
        if not path.is_file():
            raise StateError(
                "Legacy private state contains an unsupported file; preserve it first."
            )
        name = path.relative_to(legacy).as_posix().encode("utf-8")
        value = path.read_bytes()
        digest.update(len(name).to_bytes(8) + name + len(value).to_bytes(8) + value)
    return digest.hexdigest()


def _previous_source(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise StateError("Invalid private migration record; state was preserved.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise StateError("Cannot read private migration record; state was preserved.") from None
    if not isinstance(value, dict) or value.get("version") != 1:
        raise StateError("Invalid private migration record; state was preserved.")
    fingerprint = value.get("sha256")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise StateError("Invalid private migration record; state was preserved.")
    return fingerprint


def _copy_private(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise StateError("Legacy private state contains a symbolic link; preserve it first.")
    if source.is_dir():
        destination.mkdir(mode=0o700)
        for child in source.iterdir():
            _copy_private(child, destination / child.name)
        _sync_directory(destination)
    elif source.is_file():
        with destination.open("xb") as output:
            destination.chmod(0o600)
            output.write(source.read_bytes())
            output.flush()
            os.fsync(output.fileno())
    else:
        raise StateError("Legacy private state contains an unsupported file; preserve it first.")


def migrate_legacy(root: Path) -> bool:
    """Import a managed checkout profile once, without overwriting either source.

    Call while holding ``environment_lock(profile_directory(root))``. The environment
    file is published last: a partial import never appears to be a configured profile.
    A crash after publishing state but before publishing .env requires explicit recovery.
    Existing shared configuration remains authoritative for mutable settings.
    """
    profile = profile_directory(root)
    legacy = root / ".devcontainer"
    if profile.resolve() == legacy.resolve():
        return False
    try:
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        target_env = profile / ".env"
        target_state = profile / "state"
        provenance = profile / ".legacy-import.json"
        source_env = legacy / ".env"
        if target_env.is_symlink() or target_state.is_symlink() or provenance.is_symlink():
            raise StateError("Shared private state contains a symbolic link; preserve it first.")
        if not target_env.exists() and (target_state.exists() or provenance.exists()):
            raise StateError(
                "Incomplete shared development profile: state exists without configuration. "
                "Restore the matching managed .env before setup; no files were replaced."
            )
        shared_values = _managed_values(target_env) if target_env.exists() else None
        if not source_env.exists() and not source_env.is_symlink():
            return False
        legacy_values = _managed_values(source_env)
        fingerprint = _source_fingerprint(legacy)
        if shared_values is not None:
            # A previously imported checkout is an archive, including after explicit
            # identity recovery. Never compare that unchanged archive to newer state.
            if _previous_source(provenance) == fingerprint:
                return False
            conflicting = any(
                legacy_values[key] != shared_values[key]
                for key in PERSISTED_FIELDS
                if key in legacy_values and key in shared_values
            )
            legacy_identity = _identity(legacy / "state/identity.json")
            shared_identity = _identity(target_state / "identity.json")
            conflicting |= any(
                legacy_identity[key] != shared_identity[key]
                for key in ("instance_id", "project_id", "app_id")
                if key in legacy_identity and key in shared_identity
            )
            if conflicting:
                raise StateError(
                    "Legacy and shared development profiles identify different persisted "
                    "environments. Preserve both and restore the matching profile; "
                    "no files were replaced."
                )
            return False
        with TemporaryDirectory(prefix=".legacy-import-", dir=profile) as temporary:
            staging = Path(temporary)
            source_state = legacy / "state"
            if source_state.exists() or source_state.is_symlink():
                _copy_private(source_state, staging / "state")
            _copy_private(source_env, staging / ".env")
            staged_provenance = staging / provenance.name
            with staged_provenance.open("x", encoding="utf-8") as output:
                staged_provenance.chmod(0o600)
                json.dump({"version": 1, "sha256": fingerprint}, output)
                output.flush()
                os.fsync(output.fileno())
            _sync_directory(staging)
            if (staging / "state").exists():
                (staging / "state").rename(target_state)
                _sync_directory(profile)
            staged_provenance.rename(provenance)
            _sync_directory(profile)
            (staging / ".env").rename(target_env)
            _sync_directory(profile)
        return True
    except (OSError, UnicodeError):
        raise StateError(
            "Cannot import the legacy development profile; original files were preserved. "
            "Check private profile storage and restore matching configuration/state if needed."
        ) from None
