import subprocess
import time
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_proxy_errors_do_not_log_request_secrets():
    name = "docta-proxy-test-" + uuid4().hex
    marker = "synthetic-private-" + uuid4().hex
    subprocess.run(
        ["docker", "run", "--rm", "-d", "--name", name, "-p", "127.0.0.1::443",
         "-e", "DOCTA_WEB_DOMAIN=app.localhost", "-e", "DOCTA_STORAGE_DOMAIN=storage.localhost",
         "docta-proxy:vm"], check=True, capture_output=True, text=True, timeout=30,
    )
    try:
        binding = subprocess.run(
            ["docker", "port", name, "443/tcp"], check=True, capture_output=True,
            text=True, timeout=10,
        ).stdout.strip()
        port = int(binding.rsplit(":", 1)[1])
        status = ""
        for _ in range(60):
            result = subprocess.run(
                ["curl", "--noproxy", "storage.localhost", "--insecure", "--silent",
                 "--output", "/dev/null", "--write-out", "%{http_code}", "--resolve",
                 f"storage.localhost:{port}:127.0.0.1", "--header", f"X-Test: {marker}",
                 f"https://storage.localhost:{port}/synthetic?private={marker}"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            status = result.stdout
            if status == "502":
                break
            time.sleep(0.25)
        assert status == "502", "Expected an upstream failure from the isolated proxy"
        logs = subprocess.run(
            ["docker", "logs", name], check=True, capture_output=True, text=True, timeout=10,
        )
        combined = logs.stdout + logs.stderr
        assert '"logger":"http.log.error"' in combined
        assert marker not in combined
        assert "/synthetic" not in combined
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True, timeout=30, check=False)
