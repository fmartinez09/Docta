import subprocess

import pytest
from scripts.init_vm import initialize

pytestmark = pytest.mark.integration


def test_generated_private_vm_profile_resolves_compose(tmp_path):
    path = tmp_path / ".env.vm"
    initialize(path, web_domain="app.example.com", storage_domain="storage.example.com",
               issuer="https://identity.example.com", jwks="https://identity.example.com/keys",
               audience="project", client_id="client")
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(path), "-f", "infra/compose.vm.yaml",
         "config", "--quiet"], capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, "VM Compose rejected the generated profile"
