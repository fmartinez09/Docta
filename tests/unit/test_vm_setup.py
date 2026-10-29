from pathlib import Path

import pytest
import yaml
from scripts.init_vm import initialize


def config():
    return dict(web_domain="app.example.com", storage_domain="storage.example.com",
                issuer="https://identity.example.com", jwks="https://identity.example.com/keys",
                audience="project", client_id="client")


def test_generated_vm_configuration_is_private_and_idempotent(tmp_path: Path):
    target = tmp_path / ".env.vm"
    assert initialize(target, **config())
    original = target.read_bytes()
    assert target.stat().st_mode & 0o077 == 0
    assert not initialize(target, **config())
    assert target.read_bytes() == original
    with pytest.raises(ValueError, match="differs"):
        initialize(target, **(config() | {"audience": "different"}))
    assert target.read_bytes() == original


def test_vm_rejects_invalid_domains_and_untrusted_oidc(tmp_path: Path):
    target = tmp_path / ".env.vm"
    with pytest.raises(ValueError, match="distinct"):
        initialize(target, **(config() | {"storage_domain": "app.example.com"}))
    with pytest.raises(ValueError, match="HTTPS"):
        initialize(target, **(config() | {"issuer": "http://identity.example.com"}))
    assert not target.exists()


def test_vm_rejects_an_incomplete_or_public_existing_configuration(tmp_path: Path):
    target = tmp_path / ".env.vm"
    initialize(target, **config())
    original = target.read_text()
    target.write_text(original.replace("RUSTFS_SECRET_KEY=", "MISSING_SECRET="))
    with pytest.raises(ValueError, match="incomplete"):
        initialize(target, **config())
    target.write_text(original)
    target.chmod(0o644)
    with pytest.raises(ValueError, match="permissions"):
        initialize(target, **config())


def test_vm_only_publishes_proxy_and_waits_for_schema():
    root = Path(__file__).resolve().parents[2]
    services = yaml.safe_load((root / "infra/compose.vm.yaml").read_text())["services"]
    assert {name for name, service in services.items() if service.get("ports")} == {"proxy"}
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert (
        services["worker"]["depends_on"]["migrate"]["condition"]
        == "service_completed_successfully"
    )
    assert "DOCTA_WEB_SESSION_SECRET" not in services["api"]["environment"]
    assert "DOCTA_WEB_SESSION_SECRET" not in services["worker"]["environment"]
