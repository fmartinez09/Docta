import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from tests.integration.test_courses import _upgrade_database
from tests.integration.test_documents import _pdf_bytes

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(url, processes):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert all(process.poll() is None for process in processes), "Test server exited"
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise AssertionError("Test server readiness deadline exceeded")


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def test_teacher_to_student_browser_flow(tmp_path):
    _upgrade_database()
    api_port, web_port = free_port(), free_port()
    origin = f"http://127.0.0.1:{web_port}"
    api = f"http://127.0.0.1:{api_port}"
    pdf = tmp_path / "Fisica.pdf"
    pdf.write_bytes(
        _pdf_bytes(
            "La velocidad es desplazamiento dividido por tiempo. "
            "El fallo de medicion puede afectar la velocidad."
        )
    )
    env = os.environ | {
        "DOCTA_OIDC_ISSUER": api + "/test-oidc",
        "DOCTA_OIDC_AUDIENCE": "browser-test",
        "DOCTA_OIDC_JWKS_URL": api + "/test-oidc/jwks",
        "DOCTA_WEB_ORIGIN": origin,
        "DOCTA_WEB_OIDC_CLIENT_ID": "browser-test",
        "DOCTA_WEB_OIDC_SCOPES": "openid profile",
        "DOCTA_WEB_SESSION_SECRET": uuid4().hex + uuid4().hex,
        "DOCTA_API_BASE_URL": api,
        "DOCTA_E2E_CONTROL_KEY": uuid4().hex,
        "DOCTA_E2E_PDF": str(pdf),
        "DOCTA_E2E_OUTPUT": str(tmp_path / "browser"),
        "DOCTA_TUTOR_ENDPOINT_URL": api + "/unused-model",
        "DOCTA_TUTOR_MODEL": "test-only",
        "DOCTA_TUTOR_API_KEY": "test-only",
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    node = shutil.which("node")
    assert node, "Install Node.js and run npm ci before browser tests"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with ExitStack() as stack:
        processes = []
        commands = [
            (
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "tests.e2e.server:create_browser_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                    "--no-access-log",
                ],
                ROOT,
            ),
            ([sys.executable, "-m", "docta_api.worker"], ROOT),
            (
                [
                    node,
                    str(ROOT / "node_modules/next/dist/bin/next"),
                    "start",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(web_port),
                ],
                ROOT / "apps/web",
            ),
        ]
        for index, (command, cwd) in enumerate(commands):
            output = stack.enter_context((tmp_path / f"server-{index}.log").open("w"))
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
            stack.callback(stop, process)
            processes.append(process)
        wait_ready(api + "/api/v1/health/live", processes)
        wait_ready(origin, processes)
        result = subprocess.run(
            [
                node,
                str(ROOT / "node_modules/@playwright/test/cli.js"),
                "test",
                "--config",
                "apps/web/playwright.config.ts",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            creationflags=flags,
        )
        assert result.returncode == 0, result.stdout + result.stderr
