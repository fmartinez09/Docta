import json
from pathlib import Path

import httpx
import pytest

from docta_api.dev_pdf_upload import DevPDFUploadError, upload_dev_pdf

BASE_URL = "http://127.0.0.1:8000"
UPLOAD_URL = "http://127.0.0.1:9000/docta-documents/signed-object"
COURSE_ID = "0e451fa1-8d2e-4dd2-bd81-e6aa410780c0"
DOCUMENT_ID = "9c21d51b-e6e2-4e2c-a598-5400ab26693f"
VERSION_ID = "fa8f571d-0b00-40a5-a1b2-83c682f87dac"
CORPUS_ID = "e8cb44e0-2788-402c-b283-5cde75df1fa8"


def test_upload_dev_pdf_uses_public_api_and_signed_storage_contract(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("docta_api.dev_pdf_upload.sleep", lambda _: None)
    pdf_path = tmp_path / "lesson.pdf"
    pdf_bytes = b"%PDF-1.7\ntext-based development fixture"
    pdf_path.write_bytes(pdf_bytes)
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            request.headers.get("authorization") == "Bearer signed-access-token"
            or str(request.url) == UPLOAD_URL
        )
        if request.url.path == "/api/v1/courses":
            observed["course_body"] = json.loads(request.content)
            return httpx.Response(
                201,
                json={"id": COURSE_ID, "version": 0},
                request=request,
            )
        if request.url.path.endswith("/documents/uploads"):
            observed["upload_body"] = json.loads(request.content)
            observed["upload_idempotency"] = request.headers["idempotency-key"]
            return httpx.Response(
                201,
                json={
                    "document_id": DOCUMENT_ID,
                    "version_id": VERSION_ID,
                    "upload_url": UPLOAD_URL,
                    "headers": {
                        "content-type": "application/pdf",
                        "x-amz-meta-sha256": "server-signed-hash",
                    },
                },
                request=request,
            )
        if str(request.url) == UPLOAD_URL:
            observed["put_body"] = request.content
            observed["put_content_type"] = request.headers["content-type"]
            observed["put_checksum"] = request.headers["x-amz-meta-sha256"]
            return httpx.Response(200, request=request)
        if request.url.path.endswith("/complete"):
            observed["complete_idempotency"] = request.headers["idempotency-key"]
            return httpx.Response(202, json={"state": "QUEUED"}, request=request)
        if request.method == "GET" and request.url.path.endswith(f"/versions/{VERSION_ID}"):
            observed["status_read"] = True
            return httpx.Response(
                202,
                json={
                    "state": "INDEXED",
                    "failure_code": None,
                    "page_count": 2,
                    "chunk_count": 3,
                    "corpus_version_id": CORPUS_ID,
                },
                request=request,
            )
        if request.url.path.endswith("/corpus/activate"):
            observed["activation_body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"active_corpus_version_id": CORPUS_ID},
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = upload_dev_pdf(
            pdf_path=pdf_path,
            course_title="Curso de prueba",
            base_url=BASE_URL,
            access_token="signed-access-token",
            http_client=client,
        )

    assert observed["course_body"] == {"title": "Curso de prueba"}
    assert observed["upload_body"] == {
        "filename": "lesson.pdf",
        "size_bytes": len(pdf_bytes),
        "sha256": "fd0dc251bd3687462173599f08c5765edfb324d37b6f67afa3b2776e7ae95915",
        "content_type": "application/pdf",
    }
    assert str(observed["upload_idempotency"]).startswith("dev-upload-")
    assert str(observed["complete_idempotency"]).startswith("dev-complete-")
    assert observed["status_read"] is True
    assert observed["put_body"] == pdf_bytes
    assert observed["put_content_type"] == "application/pdf"
    assert observed["put_checksum"] == "server-signed-hash"
    assert observed["activation_body"] == {
        "corpus_version_id": CORPUS_ID,
        "expected_course_version": 0,
    }
    assert result == {
        "course_id": COURSE_ID,
        "document_id": DOCUMENT_ID,
        "version_id": VERSION_ID,
        "state": "INDEXED",
        "failure_code": None,
        "page_count": 2,
        "chunk_count": 3,
        "corpus_version_id": CORPUS_ID,
        "active_corpus_version_id": CORPUS_ID,
    }


def test_upload_dev_pdf_does_not_send_bearer_over_remote_plain_http(tmp_path: Path) -> None:
    pdf_path = tmp_path / "lesson.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nfixture")

    with pytest.raises(DevPDFUploadError, match=r"Plain HTTP.*loopback"):
        upload_dev_pdf(
            pdf_path=pdf_path,
            course_title="Curso de prueba",
            base_url="http://api.example.test:8000",
            access_token="signed-access-token",
        )
