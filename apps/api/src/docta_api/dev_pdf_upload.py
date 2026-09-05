"""Create a development course and upload one PDF through the public API contracts."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from docta_api.dev_token import DevTokenError, DevTokenSettings, obtain_dev_token


class DevPDFUploadError(Exception):
    """A safe, user-facing development upload error."""


def upload_dev_pdf(
    *,
    pdf_path: Path,
    course_title: str,
    base_url: str,
    access_token: str,
    activate: bool = True,
    http_client: httpx.Client | None = None,
) -> dict[str, object]:
    resolved_path = _validated_pdf_path(pdf_path)
    normalized_base_url = _validated_base_url(base_url)
    token = access_token.strip()
    if not token or any(character.isspace() for character in token):
        raise DevPDFUploadError("The access token is empty or malformed.")

    pdf_bytes = resolved_path.read_bytes()
    sha256_hex = sha256(pdf_bytes).hexdigest()
    if http_client is None:
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            return _upload_dev_pdf(
                client=client,
                pdf_path=resolved_path,
                pdf_bytes=pdf_bytes,
                sha256_hex=sha256_hex,
                course_title=course_title,
                base_url=normalized_base_url,
                access_token=token,
                activate=activate,
            )
    return _upload_dev_pdf(
        client=http_client,
        pdf_path=resolved_path,
        pdf_bytes=pdf_bytes,
        sha256_hex=sha256_hex,
        course_title=course_title,
        base_url=normalized_base_url,
        access_token=token,
        activate=activate,
    )


def _upload_dev_pdf(
    *,
    client: httpx.Client,
    pdf_path: Path,
    pdf_bytes: bytes,
    sha256_hex: str,
    course_title: str,
    base_url: str,
    access_token: str,
    activate: bool,
) -> dict[str, object]:
    authorization = {"Authorization": f"Bearer {access_token}"}
    course = _request_json(
        client,
        "create course",
        "POST",
        f"{base_url}/api/v1/courses",
        headers=authorization,
        json={"title": course_title},
    )
    course_id = _required_string(course, "id", "course response")
    course_version = _required_integer(course, "version", "course response")

    operation_id = uuid4().hex
    upload = _request_json(
        client,
        "create upload authorization",
        "POST",
        f"{base_url}/api/v1/courses/{course_id}/documents/uploads",
        headers={
            **authorization,
            "Idempotency-Key": f"dev-upload-{operation_id}",
        },
        json={
            "filename": pdf_path.name,
            "size_bytes": len(pdf_bytes),
            "sha256": sha256_hex,
            "content_type": "application/pdf",
        },
    )
    document_id = _required_string(upload, "document_id", "upload response")
    version_id = _required_string(upload, "version_id", "upload response")
    upload_url = _required_string(upload, "upload_url", "upload response")
    upload_headers = upload.get("headers")
    if not isinstance(upload_headers, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in upload_headers.items()
    ):
        raise DevPDFUploadError("The upload response did not contain valid signed headers.")

    _put_pdf(client, upload_url, dict(upload_headers), pdf_bytes)
    completed = _request_json(
        client,
        "complete upload",
        "POST",
        (
            f"{base_url}/api/v1/courses/{course_id}/documents/{document_id}/"
            f"versions/{version_id}/complete"
        ),
        headers={
            **authorization,
            "Idempotency-Key": f"dev-complete-{operation_id}",
        },
    )

    deadline = monotonic() + 600
    while completed.get("state") in {"QUEUED", "PROCESSING"}:
        if monotonic() >= deadline:
            raise DevPDFUploadError(
                f"Ingestion is still pending. Query course {course_id}, document {document_id}, "
                f"version {version_id}; do not upload again. Check the worker."
            )
        sleep(1)
        completed = _request_json(
            client, "observe ingestion", "GET",
            f"{base_url}/api/v1/courses/{course_id}/documents/{document_id}/versions/{version_id}",
            headers=authorization,
        )
    state = _required_string(completed, "state", "completion response")
    corpus_version_id = completed.get("corpus_version_id")
    activation: Mapping[str, object] | None = None
    if activate and state == "INDEXED" and isinstance(corpus_version_id, str):
        activation = _request_json(
            client,
            "activate corpus",
            "POST",
            f"{base_url}/api/v1/courses/{course_id}/corpus/activate",
            headers=authorization,
            json={
                "corpus_version_id": corpus_version_id,
                "expected_course_version": course_version,
            },
        )

    return {
        "course_id": course_id,
        "document_id": document_id,
        "version_id": version_id,
        "state": state,
        "failure_code": completed.get("failure_code"),
        "page_count": completed.get("page_count"),
        "chunk_count": completed.get("chunk_count"),
        "corpus_version_id": corpus_version_id,
        "active_corpus_version_id": (
            activation.get("active_corpus_version_id") if activation is not None else None
        ),
    }


def _request_json(
    client: httpx.Client,
    operation: str,
    method: str,
    url: str,
    **kwargs: Any,
) -> Mapping[str, object]:
    try:
        response = client.request(method, url, **kwargs)
    except httpx.HTTPError as error:
        raise DevPDFUploadError(f"Could not {operation}: the service is unavailable.") from error
    if not response.is_success:
        error_code = _safe_api_error_code(response)
        suffix = f", code={error_code}" if error_code is not None else ""
        raise DevPDFUploadError(
            f"Could not {operation}: HTTP {response.status_code}{suffix}."
        )
    try:
        document = response.json()
    except ValueError as error:
        raise DevPDFUploadError(f"Could not {operation}: the response was not JSON.") from error
    if not isinstance(document, Mapping):
        raise DevPDFUploadError(f"Could not {operation}: the response was not a JSON object.")
    return document


def _put_pdf(
    client: httpx.Client,
    upload_url: str,
    upload_headers: dict[str, str],
    pdf_bytes: bytes,
) -> None:
    try:
        response = client.put(upload_url, headers=upload_headers, content=pdf_bytes)
    except httpx.HTTPError as error:
        raise DevPDFUploadError("Could not upload the PDF to object storage.") from error
    if not response.is_success:
        raise DevPDFUploadError(
            f"Could not upload the PDF to object storage: HTTP {response.status_code}."
        )


def _validated_pdf_path(pdf_path: Path) -> Path:
    try:
        resolved_path = pdf_path.expanduser().resolve(strict=True)
    except OSError as error:
        raise DevPDFUploadError(f"PDF file not found: {pdf_path}") from error
    if not resolved_path.is_file() or resolved_path.suffix.lower() != ".pdf":
        raise DevPDFUploadError("The input must be an existing .pdf file.")
    if resolved_path.stat().st_size == 0:
        raise DevPDFUploadError("The PDF file is empty.")
    return resolved_path


def _validated_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DevPDFUploadError("The API base URL must be an HTTP or HTTPS URL.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise DevPDFUploadError("Plain HTTP is allowed only for a loopback API base URL.")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise DevPDFUploadError(
            "The API base URL must not contain credentials, query, or fragment."
        )
    return normalized


def _required_string(document: Mapping[str, object], name: str, source: str) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not value:
        raise DevPDFUploadError(f"The {source} did not contain {name}.")
    return value


def _required_integer(document: Mapping[str, object], name: str, source: str) -> int:
    value = document.get(name)
    if not isinstance(value, int):
        raise DevPDFUploadError(f"The {source} did not contain {name}.")
    return value


def _safe_api_error_code(response: httpx.Response) -> str | None:
    try:
        document = response.json()
    except ValueError:
        return None
    if not isinstance(document, Mapping):
        return None
    value = document.get("code")
    if not isinstance(value, str) or re.fullmatch(r"[a-z0-9_-]{1,100}", value) is None:
        return None
    return value


def _access_token() -> str:
    existing = os.environ.get("DOCTA_DEV_ACCESS_TOKEN", "").strip()
    if existing:
        return existing
    return obtain_dev_token(DevTokenSettings())  # type: ignore[call-arg]


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a development course, upload one PDF, index it, and activate it."
    )
    parser.add_argument("pdf_path", type=Path, help="Path to a text-based PDF")
    parser.add_argument("--course-title", default="Curso de prueba")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--no-activate",
        action="store_true",
        help="Index the PDF without activating the resulting corpus",
    )
    return parser


def main() -> int:
    arguments = _argument_parser().parse_args()
    try:
        result = upload_dev_pdf(
            pdf_path=arguments.pdf_path,
            course_title=arguments.course_title,
            base_url=arguments.base_url,
            access_token=_access_token(),
            activate=not arguments.no_activate,
        )
    except (DevTokenError, DevPDFUploadError) as error:
        print(f"Development PDF upload failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0 if result["state"] == "INDEXED" else 1
