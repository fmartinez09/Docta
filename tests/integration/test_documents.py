from collections.abc import AsyncIterator
from hashlib import sha256
from uuid import UUID, uuid4

import httpx
import psycopg
import pymupdf
import pytest
from alembic import command
from alembic.config import Config

from docta_api.config import Settings
from docta_api.identity import AuthenticatedIdentity, DeterministicIdentityProvider
from docta_api.main import create_app
from docta_api.s3_object_storage import S3ObjectStorage

pytestmark = pytest.mark.integration


@pytest.mark.anyio
async def test_direct_pdf_upload_indexes_pages_idempotently_and_activates_with_cas() -> None:
    settings = _settings()
    _upgrade_database()
    suffix = uuid4().hex
    teacher_a = AuthenticatedIdentity("https://integration.test/", f"document-a-{suffix}")
    teacher_b = AuthenticatedIdentity("https://integration.test/", f"document-b-{suffix}")
    provider = DeterministicIdentityProvider({"token-a": teacher_a, "token-b": teacher_b})
    storage = _storage(settings)
    course_ids: list[UUID] = []
    storage_keys: list[str] = []

    try:
        async with _client(settings, provider, storage) as client:
            course_a = await _create_course(client, "token-a", "Ingestion Course A")
            course_b = await _create_course(client, "token-b", "Ingestion Course B")
            course_ids.extend([course_a, course_b])
            content = _pdf_bytes(
                "ALPHAONLY731 is evidence from the first page.",
                "Velocity is displacement divided by elapsed time.",
            )
            upload = await _create_upload(
                client,
                course_a,
                "token-a",
                "upload-positive",
                content,
            )
            repeated_upload = await _create_upload(
                client,
                course_a,
                "token-a",
                "upload-positive",
                content,
            )
            assert repeated_upload["document_id"] == upload["document_id"]
            assert repeated_upload["version_id"] == upload["version_id"]
            await _put_direct(repeated_upload, content)

            document_id = UUID(upload["document_id"])
            version_id = UUID(upload["version_id"])
            source_storage_key = _storage_key(settings, course_a, document_id, version_id)
            storage_keys.append(source_storage_key)
            complete_url = (
                f"/api/v1/courses/{course_a}/documents/{document_id}/"
                f"versions/{version_id}/complete"
            )
            completed = await client.post(
                complete_url,
                headers={
                    "authorization": "Bearer token-a",
                    "idempotency-key": "confirm-positive",
                },
            )
            repeated_completion = await client.post(
                complete_url,
                headers={
                    "authorization": "Bearer token-a",
                    "idempotency-key": "confirm-positive",
                },
            )
            with psycopg.connect(str(settings.database_url)) as connection:
                immutable_storage_version = connection.execute(
                    "SELECT storage_version_id FROM document_versions WHERE id = %s",
                    (version_id,),
                ).fetchone()
            assert immutable_storage_version is not None
            assert immutable_storage_version[0] is not None
            await _put_direct(repeated_upload, _pdf_bytes("Overwritten after confirmation"))
            assert storage.read(
                source_storage_key,
                version_id=immutable_storage_version[0],
                max_bytes=settings.max_document_bytes,
            ) == content
            own_status = await client.get(
                complete_url.removesuffix("/complete"),
                headers={"authorization": "Bearer token-a"},
            )
            cross_course_status = await client.get(
                (
                    f"/api/v1/courses/{course_a}/documents/{document_id}/"
                    f"versions/{version_id}"
                ),
                headers={"authorization": "Bearer token-b"},
            )

            assert completed.status_code == 202
            assert completed.json()["state"] == "INDEXED", completed.json()
            assert completed.json()["job_state"] == "SUCCEEDED"
            assert repeated_completion.status_code == 202
            assert repeated_completion.json() == completed.json()
            assert own_status.status_code == 200
            assert own_status.json()["page_count"] == 2
            assert own_status.json()["chunk_count"] == 2
            assert cross_course_status.status_code == 404
            assert "ALPHAONLY731" not in cross_course_status.text

            corpus_version_id = UUID(completed.json()["corpus_version_id"])
            activated = await client.post(
                f"/api/v1/courses/{course_a}/corpus/activate",
                json={
                    "corpus_version_id": str(corpus_version_id),
                    "expected_course_version": 0,
                },
                headers={"authorization": "Bearer token-a"},
            )
            stale_activation = await client.post(
                f"/api/v1/courses/{course_a}/corpus/activate",
                json={
                    "corpus_version_id": str(corpus_version_id),
                    "expected_course_version": 0,
                },
                headers={"authorization": "Bearer token-a"},
            )

        assert activated.status_code == 200
        assert activated.json()["course_version"] == 1
        assert stale_activation.status_code == 409
        assert stale_activation.json()["code"] == "publication_conflict"

        with psycopg.connect(str(settings.database_url)) as connection:
            chunk_evidence = connection.execute(
                """
                SELECT page_start, content
                FROM chunks
                WHERE course_id = %s
                  AND corpus_version_id = %s
                  AND search_vector @@ plainto_tsquery('spanish', 'ALPHAONLY731')
                """,
                (course_a, corpus_version_id),
            ).fetchall()
            job_count = connection.execute(
                "SELECT count(*) FROM ingestion_jobs WHERE document_version_id = %s",
                (version_id,),
            ).fetchone()
            active = connection.execute(
                "SELECT active_corpus_version_id, version FROM courses WHERE id = %s",
                (course_a,),
            ).fetchone()
        assert chunk_evidence == [(1, "ALPHAONLY731 is evidence from the first page.")]
        assert job_count == (1,)
        assert active == (corpus_version_id, 1)
    finally:
        for storage_key in storage_keys:
            storage.delete(storage_key)
        _delete_test_records(settings, course_ids, [teacher_a, teacher_b])


@pytest.mark.anyio
async def test_textless_pdf_records_ocr_required_and_survives_app_restart() -> None:
    settings = _settings()
    _upgrade_database()
    identity = AuthenticatedIdentity(
        "https://integration.test/",
        f"document-failure-{uuid4().hex}",
    )
    provider = DeterministicIdentityProvider({"teacher-token": identity})
    storage = _storage(settings)
    course_ids: list[UUID] = []
    storage_keys: list[str] = []
    document_id: UUID | None = None
    version_id: UUID | None = None

    try:
        async with _client(settings, provider, storage) as client:
            course_id = await _create_course(client, "teacher-token", "Failure Course")
            course_ids.append(course_id)
            oversized = await client.post(
                f"/api/v1/courses/{course_id}/documents/uploads",
                json={
                    "filename": "too-large.pdf",
                    "size_bytes": settings.max_document_bytes + 1,
                    "sha256": "0" * 64,
                    "content_type": "application/pdf",
                },
                headers={
                    "authorization": "Bearer teacher-token",
                    "idempotency-key": "oversized-upload",
                },
            )
            assert oversized.status_code == 413
            assert oversized.json()["code"] == "document_too_large"

            content = _pdf_bytes("")
            upload = await _create_upload(
                client,
                course_id,
                "teacher-token",
                "textless-upload",
                content,
            )
            await _put_direct(upload, content)
            document_id = UUID(upload["document_id"])
            version_id = UUID(upload["version_id"])
            storage_keys.append(_storage_key(settings, course_id, document_id, version_id))
            version_url = (
                f"/api/v1/courses/{course_id}/documents/{document_id}/versions/{version_id}"
            )
            completed = await client.post(
                f"{version_url}/complete",
                headers={
                    "authorization": "Bearer teacher-token",
                    "idempotency-key": "confirm-textless",
                },
            )
            assert completed.status_code == 202
            assert completed.json()["state"] == "OCR_REQUIRED"
            assert completed.json()["failure_code"] == "DOCUMENT_OCR_REQUIRED"
            assert completed.json()["job_state"] == "FAILED"
            assert completed.json()["corpus_version_id"] is None

            declared_content = _pdf_bytes("Declared checksum A")
            tampered_content = _pdf_bytes("Declared checksum B")
            assert len(declared_content) == len(tampered_content)
            tampered_upload = await _create_upload(
                client,
                course_id,
                "teacher-token",
                "tampered-upload",
                declared_content,
            )
            await _put_direct(tampered_upload, tampered_content)
            tampered_document_id = UUID(tampered_upload["document_id"])
            tampered_version_id = UUID(tampered_upload["version_id"])
            storage_keys.append(
                _storage_key(
                    settings,
                    course_id,
                    tampered_document_id,
                    tampered_version_id,
                )
            )
            tampered = await client.post(
                (
                    f"/api/v1/courses/{course_id}/documents/{tampered_document_id}/"
                    f"versions/{tampered_version_id}/complete"
                ),
                headers={
                    "authorization": "Bearer teacher-token",
                    "idempotency-key": "confirm-tampered",
                },
            )
            assert tampered.status_code == 202
            assert tampered.json()["state"] == "REJECTED"
            assert tampered.json()["failure_code"] == "DOCUMENT_CHECKSUM_MISMATCH"

        async with _client(settings, provider, storage) as restarted_client:
            durable_status = await restarted_client.get(
                version_url,
                headers={"authorization": "Bearer teacher-token"},
            )
        assert durable_status.status_code == 200
        assert durable_status.json() == completed.json()

        with psycopg.connect(str(settings.database_url)) as connection:
            artifact_counts = connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM corpus_version_documents
                     WHERE document_version_id = %s),
                    (SELECT count(*) FROM chunks WHERE document_version_id = %s)
                """,
                (version_id, version_id),
            ).fetchone()
        assert artifact_counts == (0, 0)
    finally:
        for storage_key in storage_keys:
            storage.delete(storage_key)
        _delete_test_records(settings, course_ids, [identity])


@pytest.mark.anyio
async def test_activation_rejects_building_corpus() -> None:
    settings = _settings()
    _upgrade_database()
    identity = AuthenticatedIdentity(
        "https://integration.test/",
        f"publication-{uuid4().hex}",
    )
    provider = DeterministicIdentityProvider({"teacher-token": identity})
    storage = _storage(settings)
    course_ids: list[UUID] = []

    try:
        async with _client(settings, provider, storage) as client:
            course_id = await _create_course(client, "teacher-token", "Publication Course")
            course_ids.append(course_id)
            corpus_version_id = uuid4()
            with psycopg.connect(str(settings.database_url)) as connection:
                connection.execute(
                    """
                    INSERT INTO corpus_versions
                        (id, course_id, state, pipeline_version, page_count, chunk_count)
                    VALUES (%s, %s, 'BUILDING', %s, 0, 0)
                    """,
                    (corpus_version_id, course_id, settings.pipeline_version),
                )

            response = await client.post(
                f"/api/v1/courses/{course_id}/corpus/activate",
                json={
                    "corpus_version_id": str(corpus_version_id),
                    "expected_course_version": 0,
                },
                headers={"authorization": "Bearer teacher-token"},
            )

        assert response.status_code == 409
        assert response.json()["code"] == "corpus_not_ready"
    finally:
        _delete_test_records(settings, course_ids, [identity])


def _settings() -> Settings:
    return Settings(
        s3_endpoint_url="http://127.0.0.1:9000",
        s3_access_key="docta-local",
        s3_secret_key="docta-local-secret",
        s3_bucket="docta-integration-documents",
        upload_ttl_seconds=60,
        max_document_bytes=1024 * 1024,
        max_document_pages=10,
        chunk_size_characters=400,
        chunk_overlap_characters=40,
    )


def _storage(settings: Settings) -> S3ObjectStorage:
    assert settings.s3_endpoint_url is not None
    assert settings.s3_access_key is not None
    assert settings.s3_secret_key is not None
    return S3ObjectStorage(
        endpoint_url=str(settings.s3_endpoint_url),
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key.get_secret_value(),
        bucket=settings.s3_bucket,
        region=settings.s3_region,
        upload_ttl_seconds=settings.upload_ttl_seconds,
        timeout_seconds=settings.dependency_timeout_seconds,
    )


async def _create_course(client: httpx.AsyncClient, token: str, title: str) -> UUID:
    response = await client.post(
        "/api/v1/courses",
        json={"title": title},
        headers={"authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])


async def _create_upload(
    client: httpx.AsyncClient,
    course_id: UUID,
    token: str,
    idempotency_key: str,
    content: bytes,
) -> dict:
    response = await client.post(
        f"/api/v1/courses/{course_id}/documents/uploads",
        json={
            "filename": "lesson.pdf",
            "size_bytes": len(content),
            "sha256": sha256(content).hexdigest(),
            "content_type": "application/pdf",
        },
        headers={
            "authorization": f"Bearer {token}",
            "idempotency-key": idempotency_key,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _put_direct(upload: dict, content: bytes) -> None:
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        response = await client.put(
            upload["upload_url"],
            content=content,
            headers=upload["headers"],
        )
    assert response.status_code in (200, 204), response.text


def _pdf_bytes(*pages: str) -> bytes:
    document = pymupdf.open()
    try:
        for text in pages:
            page = document.new_page()
            if text:
                page.insert_text((72, 72), text)
        return document.tobytes()
    finally:
        document.close()


def _storage_key(
    settings: Settings,
    course_id: UUID,
    document_id: UUID,
    version_id: UUID,
) -> str:
    return (
        f"{settings.environment}/courses/{course_id}/documents/{document_id}/"
        f"versions/{version_id}/source.pdf"
    )


def _upgrade_database() -> None:
    command.upgrade(Config("apps/api/alembic.ini"), "head")


def _client(
    settings: Settings,
    provider: DeterministicIdentityProvider,
    storage: S3ObjectStorage,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        settings=settings,
        identity_provider=provider,
        object_storage=storage,
    )
    return _LifespanClient(app)


class _LifespanClient:
    def __init__(self, app) -> None:
        self._app = app
        self._lifespan = app.router.lifespan_context(app)
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def __aenter__(self) -> httpx.AsyncClient:
        await self._lifespan.__aenter__()
        return await self._client.__aenter__()

    async def __aexit__(self, *args: object) -> None:
        await self._client.__aexit__(*args)
        await self._lifespan.__aexit__(*args)


def _delete_test_records(
    settings: Settings,
    course_ids: list[UUID],
    identities: list[AuthenticatedIdentity],
) -> None:
    with psycopg.connect(str(settings.database_url)) as connection:
        for course_id in course_ids:
            connection.execute("DELETE FROM courses WHERE id = %s", (course_id,))
        for identity in identities:
            connection.execute(
                "DELETE FROM users WHERE oidc_issuer = %s AND oidc_subject = %s",
                (identity.issuer, identity.subject),
            )
