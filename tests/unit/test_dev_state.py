import json
import os
import stat
import subprocess
import sys

import pytest
from scripts import dev_state


@pytest.fixture
def shared_profile(tmp_path, monkeypatch):
    profile = tmp_path / "shared"
    monkeypatch.setenv("DOCTA_DEV_HOME", str(profile))
    return profile


def legacy_profile(root, *, password="original-private-value"):
    legacy = root / ".devcontainer"
    legacy.mkdir(parents=True)
    (legacy / ".env").write_text(
        f"DOCTA_DEV_MANAGED=1\nPOSTGRES_PASSWORD='{password}'\n"
        "DOCTA_TUTOR_MODEL=old-model\n",
        encoding="utf-8",
    )
    (legacy / "state").mkdir()
    (legacy / "state/identity.json").write_text(
        json.dumps({"instance_id": "instance-1", "project_id": "project-1"}), encoding="utf-8"
    )
    (legacy / "state/admin.pat").write_text("private-pat", encoding="utf-8")
    return legacy


def test_profile_directory_defaults_to_legacy_location(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCTA_DEV_HOME", raising=False)
    assert dev_state.profile_directory(tmp_path) == tmp_path / ".devcontainer"
    assert not dev_state.migrate_legacy(tmp_path)


def test_migration_preserves_sources_and_imports_private_state_once(tmp_path, shared_profile):
    legacy = legacy_profile(tmp_path / "checkout")
    before = (legacy / ".env").read_bytes()
    with dev_state.environment_lock(shared_profile):
        assert dev_state.migrate_legacy(legacy.parent)
        assert not dev_state.migrate_legacy(legacy.parent)
    assert (legacy / ".env").read_bytes() == before
    assert (shared_profile / ".env").read_bytes() == before
    assert (shared_profile / "state/admin.pat").read_text() == "private-pat"
    assert stat.S_IMODE((shared_profile / ".env").stat().st_mode) == 0o600
    assert stat.S_IMODE((shared_profile / "state").stat().st_mode) == 0o700
    assert stat.S_IMODE((shared_profile / "state/admin.pat").stat().st_mode) == 0o600


def test_another_clean_worktree_reuses_shared_profile(tmp_path, shared_profile):
    legacy = legacy_profile(tmp_path / "first")
    dev_state.migrate_legacy(legacy.parent)
    assert dev_state.profile_directory(tmp_path / "second") == shared_profile
    assert not dev_state.migrate_legacy(tmp_path / "second")


def test_existing_shared_configuration_wins_over_legacy_tutor(tmp_path, shared_profile):
    legacy = legacy_profile(tmp_path / "checkout")
    dev_state.migrate_legacy(legacy.parent)
    current = shared_profile / ".env"
    updated = current.read_text().replace("old-model", "new-model")
    current.write_text(updated)
    assert not dev_state.migrate_legacy(legacy.parent)
    assert current.read_text() == updated
    assert "old-model" in (legacy / ".env").read_text()


def test_conflicting_legacy_credentials_fail_without_values_or_overwrite(tmp_path, shared_profile):
    first = legacy_profile(tmp_path / "first")
    dev_state.migrate_legacy(first.parent)
    second = legacy_profile(tmp_path / "second", password="different-private-value")
    original = (shared_profile / ".env").read_bytes()
    with pytest.raises(dev_state.StateError, match="different persisted environments") as error:
        dev_state.migrate_legacy(second.parent)
    assert "private-value" not in str(error.value)
    assert (shared_profile / ".env").read_bytes() == original
    assert "different-private-value" in (second / ".env").read_text()


def test_conflicting_legacy_identity_fails_even_with_same_credentials(tmp_path, shared_profile):
    legacy = legacy_profile(tmp_path / "checkout")
    dev_state.migrate_legacy(legacy.parent)
    (legacy / "state/identity.json").write_text('{"instance_id": "other-instance"}')
    with pytest.raises(dev_state.StateError, match="different persisted environments"):
        dev_state.migrate_legacy(legacy.parent)


def test_unchanged_imported_legacy_does_not_conflict_after_identity_recovery(
    tmp_path, shared_profile
):
    legacy = legacy_profile(tmp_path / "checkout")
    dev_state.migrate_legacy(legacy.parent)
    (shared_profile / "state/identity.json").write_text('{"instance_id": "recovered-instance"}')
    with (shared_profile / ".env").open("a") as output:
        output.write("DOCTA_DEV_OIDC_PROJECT_ID=new-project\n")
    assert not dev_state.migrate_legacy(legacy.parent)
    assert "recovered-instance" in (shared_profile / "state/identity.json").read_text()
    record = (shared_profile / ".legacy-import.json").read_text()
    assert "private" not in record
    assert "instance" not in record
    assert stat.S_IMODE((shared_profile / ".legacy-import.json").stat().st_mode) == 0o600


def test_unmanaged_legacy_is_never_imported(tmp_path, shared_profile):
    legacy = legacy_profile(tmp_path / "checkout")
    (legacy / ".env").write_text("SECRET=do-not-display")
    with pytest.raises(dev_state.StateError, match="not managed") as error:
        dev_state.migrate_legacy(legacy.parent)
    assert "do-not-display" not in str(error.value)
    assert not (shared_profile / ".env").exists()
    assert not (shared_profile / "state").exists()


def test_symlink_in_legacy_state_is_rejected_before_publish(tmp_path, shared_profile):
    legacy = legacy_profile(tmp_path / "checkout")
    (legacy / "state/link").symlink_to(legacy / ".env")
    with pytest.raises(dev_state.StateError, match="symbolic link"):
        dev_state.migrate_legacy(legacy.parent)
    assert not (shared_profile / ".env").exists()
    assert not (shared_profile / "state").exists()


def test_interrupted_publish_fails_closed_and_preserves_source(
    tmp_path, shared_profile, monkeypatch
):
    legacy = legacy_profile(tmp_path / "checkout")
    path_type = type(shared_profile)
    rename = path_type.rename

    def interrupted(path, target):
        if path.name == ".env":
            raise OSError("private exception details")
        return rename(path, target)

    monkeypatch.setattr(path_type, "rename", interrupted)
    with pytest.raises(dev_state.StateError, match="original files were preserved") as error:
        dev_state.migrate_legacy(legacy.parent)
    assert "private exception details" not in str(error.value)
    assert (legacy / ".env").exists()
    assert (shared_profile / "state/admin.pat").read_text() == "private-pat"
    assert not (shared_profile / ".env").exists()
    with pytest.raises(dev_state.StateError, match="Incomplete shared development profile"):
        dev_state.migrate_legacy(legacy.parent)
    with pytest.raises(dev_state.StateError, match="Incomplete shared development profile"):
        dev_state.migrate_legacy(tmp_path / "another-checkout")


def test_lock_rejects_concurrent_operations_and_releases_after_failure(shared_profile):
    with pytest.raises(RuntimeError, match="operation failed"):
        with dev_state.environment_lock(shared_profile):
            with pytest.raises(dev_state.StateError, match="Another development command"):
                with dev_state.environment_lock(shared_profile):
                    pytest.fail("A second operation must not acquire the same environment")
            raise RuntimeError("operation failed")
    with dev_state.environment_lock(shared_profile):
        assert stat.S_IMODE((shared_profile / ".environment.lock").stat().st_mode) == 0o600


def test_child_keeps_lock_until_it_exits(shared_profile):
    child = None
    try:
        with dev_state.environment_lock(shared_profile) as descriptor:
            child = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdin.read()"],
                stdin=subprocess.PIPE,
                pass_fds=(descriptor,),
            )
            assert not os.get_inheritable(descriptor)
        with pytest.raises(dev_state.StateError, match="Another development command"):
            with dev_state.environment_lock(shared_profile):
                pytest.fail("An inherited lock must survive its parent's descriptor closing")
        child.communicate(timeout=10)
        with dev_state.environment_lock(shared_profile):
            assert child.returncode == 0
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
