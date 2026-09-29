import asyncio
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from time import monotonic
from uuid import UUID, uuid4

import pytest
from redis.exceptions import RedisError
from sqlalchemy import func, select, update

from docta_api.config import Settings
from docta_api.document_parser import build_chunks
from docta_api.identity import AuthenticatedIdentity, DeterministicIdentityProvider
from docta_api.ingestion import InvalidJobEnvelope
from docta_api.jobs import OutboxJobDispatcher, OutboxRelay, envelope_for
from docta_api.models import Chunk, DocumentVersion, IngestionJob, OutboxEvent
from docta_api.object_storage import ObjectStorageUnavailable
from docta_api.redis_jobs import RedisJobTransport
from docta_api.worker import WorkerRuntime
from tests.integration.test_documents import (
    _client,
    _create_course,
    _create_upload,
    _delete_test_records,
    _pdf_bytes,
    _put_direct,
    _settings,
    _storage,
    _storage_key,
    _upgrade_database,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def queued_pdf(ingestion_runtime):
    settings = _settings()
    _upgrade_database()
    identity = AuthenticatedIdentity("https://jobs.test", uuid4().hex)
    provider = DeterministicIdentityProvider({"teacher": identity})
    storage = _storage(settings)
    async with _client(settings, provider, storage) as client:
        course = await _create_course(client, "teacher", "Worker course")
        content = _pdf_bytes("Photosynthesis transforms light into chemical energy.")
        upload = await _create_upload(client, course, "teacher", "upload", content)
        await _put_direct(upload, content)
        document, version = UUID(upload["document_id"]), UUID(upload["version_id"])
        url = f"/api/v1/courses/{course}/documents/{document}/versions/{version}"
        headers = {
            "authorization": "Bearer teacher",
            "idempotency-key": "confirmation",
            "x-request-id": "job-test-request",
        }
        response = await client.post(f"{url}/complete", headers=headers)
        assert response.status_code == 202
        assert response.json()["state"] == "QUEUED"
        with ingestion_runtime.database.session() as session:
            job = session.get(IngestionJob, UUID(response.json()["job_id"]))
            event = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job.id))
            envelope = envelope_for(event, job)
            assert job.attempt == 0
        try:
            yield {
                "runtime": ingestion_runtime,
                "client": client,
                "envelope": envelope,
                "headers": headers,
                "url": url,
                "content": content,
                "upload": upload,
            }
        finally:
            storage.delete(_storage_key(settings, course, document, version))
            _delete_test_records(settings, [course], [identity])


def _job(resource):
    with resource["runtime"].database.session() as session:
        return session.get(IngestionJob, resource["envelope"].job_id)


def _expire(resource):
    runtime, envelope = resource["runtime"], resource["envelope"]
    with runtime.database.session() as session:
        past = session.scalar(select(func.clock_timestamp())) - timedelta(minutes=10)
        session.execute(
            update(IngestionJob)
            .where(IngestionJob.id == envelope.job_id)
            .values(lease_expires_at=past, next_attempt_at=past)
        )
        session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.job_id == envelope.job_id)
            .values(publish_lease_until=past, available_at=past)
        )


def _artifact_count(resource):
    with resource["runtime"].database.session() as session:
        return session.scalar(
            select(func.count(Chunk.id)).where(
                Chunk.document_version_id == resource["envelope"].document_version_id
            )
        )


async def test_confirmation_commits_one_job_outbox_and_does_not_need_redis(queued_pdf):
    r = queued_pdf
    repeated = await r["client"].post(f"{r['url']}/complete", headers=r["headers"])
    assert repeated.json()["state"] == "QUEUED"
    with r["runtime"].database.session() as session:
        assert (
            session.scalar(
                select(func.count(OutboxEvent.id)).where(OutboxEvent.job_id == r["envelope"].job_id)
            )
            == 1
        )
    assert _job(r).attempt == 0
    assert _artifact_count(r) == 0
    # Runtime confirmation only records outbox; a real unreachable transport fails afterward.
    unavailable = RedisJobTransport(url="redis://127.0.0.1:1/0", stream="unused", group="unused")
    try:
        with pytest.raises(RedisError):
            OutboxRelay(r["runtime"].database, unavailable).publish_one()
    finally:
        unavailable.close()
    assert _job(r).state == "PENDING"
    _expire(r)
    assert r["runtime"].tick()
    assert _job(r).state == "SUCCEEDED"


async def test_duplicate_publish_after_relay_crash_does_not_repeat_indexing(queued_pdf):
    r = queued_pdf
    runtime = r["runtime"]

    class CrashAfterPublish:
        def publish(self, kind, fields):
            # No database transaction may span the network publication.
            with runtime.database.session() as session:
                session.scalar(
                    select(OutboxEvent)
                    .where(OutboxEvent.id == UUID(fields["event_id"]))
                    .with_for_update(nowait=True)
                )
            runtime.transport.publish(kind, fields)
            raise RedisError("simulated uncertain publish")

    with pytest.raises(RedisError):
        OutboxRelay(runtime.database, CrashAfterPublish()).publish_one()
    _expire(r)
    assert runtime.relay.publish_one()
    for _ in range(4):
        runtime.transport.consume_one(runtime.worker, reclaim_idle_ms=0)
    assert _job(r).state == "SUCCEEDED"
    assert _job(r).attempt == 1
    assert _artifact_count(r) == 1


async def test_commit_before_ack_survives_consumer_restart(queued_pdf, monkeypatch):
    r = queued_pdf
    runtime = r["runtime"]
    runtime.relay.publish_one()

    def lost_ack(message_id):
        raise RedisError("connection lost before ACK")

    monkeypatch.setattr(runtime.transport, "acknowledge", lost_ack)
    with pytest.raises(RedisError):
        runtime.transport.consume_one(runtime.worker, reclaim_idle_ms=0)
    assert _job(r).state == "SUCCEEDED"
    with_new = WorkerRuntime(Settings())
    try:
        assert with_new.transport.consume_one(with_new.worker, reclaim_idle_ms=0)
        assert (
            with_new.transport.client.xpending(with_new.transport.stream, with_new.transport.group)[
                "pending"
            ]
            == 0
        )
    finally:
        with_new.close()
    assert _job(r).attempt == 1
    assert _artifact_count(r) == 1


async def test_expired_worker_cannot_publish_after_new_claim(queued_pdf):
    r = queued_pdf
    worker = r["runtime"].worker
    first = worker._claim(r["envelope"])
    assert first is not None
    assert worker.process(r["envelope"]) is False
    _expire(r)
    second = worker._claim(r["envelope"])
    assert second.fencing_token > first.fencing_token
    parsed = worker._parser.parse(r["content"])
    chunks = build_chunks(parsed, size_characters=1600, overlap_characters=160)
    with pytest.raises(RuntimeError, match="stale"):
        worker._persist_success(
            first,
            first.expected_sha256,
            len(r["content"]),
            parsed.pages,
            chunks,
            parsed.parser_version,
        )
    worker._persist_success(
        second,
        second.expected_sha256,
        len(r["content"]),
        parsed.pages,
        chunks,
        parsed.parser_version,
    )
    assert _job(r).state == "SUCCEEDED"
    assert _artifact_count(r) == 1


async def test_retry_backoff_and_terminal_dead_letter_are_durable(queued_pdf, monkeypatch):
    r = queued_pdf
    runtime = r["runtime"]

    def unavailable(*args, **kwargs):
        raise ObjectStorageUnavailable("sensitive provider payload must not be recorded")

    monkeypatch.setattr(runtime.worker._object_storage, "read", unavailable)
    for attempt in range(1, 4):
        assert runtime.worker.process(r["envelope"])
        job = _job(r)
        assert job.attempt == attempt
        if attempt < 3:
            assert job.state == "PENDING"
            assert job.next_attempt_at > job.updated_at
            # Even duplicate deliveries cannot bypass the stored backoff.
            runtime.worker.process(r["envelope"])
            assert _job(r).attempt == attempt
            _expire(r)
    assert _job(r).state == "FAILED"
    assert _job(r).failure_code == "OBJECT_STORAGE_UNAVAILABLE"
    with runtime.database.session() as session:
        assert (
            session.scalar(
                select(func.count(OutboxEvent.id)).where(
                    OutboxEvent.job_id == r["envelope"].job_id, OutboxEvent.kind == "dead"
                )
            )
            == 1
        )
    for _ in range(4):
        runtime.relay.publish_one()
    letters = runtime.transport.client.xrange(f"{runtime.transport.stream}:dead")
    matching = [fields for _, fields in letters if fields["job_id"] == str(r["envelope"].job_id)]
    assert len(matching) == 1
    assert matching[0]["failure_code"] == "OBJECT_STORAGE_UNAVAILABLE"
    assert "sensitive" not in str(matching)
    assert _artifact_count(r) == 0


async def test_exhausted_crash_attempts_become_failed(queued_pdf):
    r = queued_pdf
    worker = r["runtime"].worker
    for _ in range(3):
        assert worker._claim(r["envelope"]) is not None
        _expire(r)
    assert worker.process(r["envelope"])
    assert _job(r).state == "FAILED"
    assert _job(r).failure_code == "INGESTION_ATTEMPTS_EXHAUSTED"
    assert _artifact_count(r) == 0


async def test_stream_loss_reconciles_from_database_and_immutable_object(queued_pdf):
    r = queued_pdf
    runtime = r["runtime"]
    runtime.relay.publish_one()
    runtime.transport.ensure_group()
    # Delete only this test run's queue. The job is already recorded as dispatched.
    runtime.transport.client.delete(runtime.transport.stream)
    await _put_direct(r["upload"], _pdf_bytes("Later overwrite must not be indexed"))
    with runtime.database.session() as session:
        session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == r["envelope"].event_id)
            .values(published_at=func.now() - timedelta(minutes=10))
        )
    restarted = WorkerRuntime(Settings())
    try:
        for _ in range(4):
            restarted.tick()
    finally:
        restarted.close()
    assert _job(r).state == "SUCCEEDED"
    with runtime.database.session() as session:
        contents = session.scalars(
            select(Chunk.content).where(
                Chunk.document_version_id == r["envelope"].document_version_id
            )
        ).all()
    assert "Photosynthesis" in " ".join(contents)
    assert "Later overwrite" not in " ".join(contents)


@pytest.mark.parametrize("field", ["course_id", "document_version_id", "event_id", "job_id"])
async def test_transport_scope_mismatch_fails_closed(queued_pdf, field):
    r = queued_pdf
    forged = replace(r["envelope"], **{field: uuid4()})
    with pytest.raises(InvalidJobEnvelope):
        r["runtime"].worker.process(forged)
    assert _job(r).attempt == 0
    assert _artifact_count(r) == 0


@pytest.mark.parametrize("worker_mode", ["local", "docker"])
async def test_real_worker_process_indexes_committed_api_upload(queued_pdf, worker_mode):
    r = queued_pdf
    # Inherits the fixture's isolated DB/object/Redis environment. No token crosses this boundary.
    container_name = f"docta-test-worker-{uuid4().hex}"
    command = [sys.executable, "-m", "docta_api.worker"]
    if worker_mode == "docker":
        r["runtime"].relay.publish_one()
        # Exercise durable database, AOF transport and immutable objects across actual restarts.
        await _docker("compose", "-f", "infra/compose.test.yaml", "restart")
        await _docker("compose", "-f", "infra/compose.test.yaml", "up", "-d", "--wait")
        assert r["runtime"].transport.client.xlen(r["runtime"].transport.stream) == 1
        settings = Settings()
        database_name = settings.database_url.path.removeprefix("/")
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "docta-test_default",
            "-e",
            "DOCTA_ENVIRONMENT=test",
            "-e",
            f"DOCTA_DATABASE_URL=postgresql://docta_test:docta_test_only@postgres:5432/{database_name}",
            "-e",
            "DOCTA_MINIO_HEALTH_URL=http://minio:9000/minio/health/ready",
            "-e",
            "DOCTA_S3_ENDPOINT_URL=http://minio:9000",
            "-e",
            "DOCTA_S3_ACCESS_KEY=docta-test",
            "-e",
            "DOCTA_S3_SECRET_KEY=docta-test-secret",
            "-e",
            f"DOCTA_S3_BUCKET={settings.s3_bucket}",
            "-e",
            "DOCTA_REDIS_URL=redis://redis:6379/0",
            "-e",
            f"DOCTA_REDIS_STREAM={settings.redis_stream}",
            "docta-worker:latest",
        ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = monotonic() + 15
        while monotonic() < deadline and _job(r).state != "SUCCEEDED":
            assert process.returncode is None, "worker process exited unexpectedly"
            await asyncio.sleep(0.1)
        assert _job(r).state == "SUCCEEDED"
        status = await r["client"].get(r["url"], headers=r["headers"])
        assert status.json()["state"] == "INDEXED"
    finally:
        if worker_mode == "docker":
            await _docker("stop", "--time", "2", container_name)
        else:
            process.terminate()
        await asyncio.wait_for(process.wait(), timeout=10)


async def _docker(*arguments):
    process = await asyncio.create_subprocess_exec(
        "docker",
        *arguments,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert await asyncio.wait_for(process.wait(), timeout=60) == 0, "Docker operation failed"


async def test_outbox_failure_rolls_back_confirmation_job_and_state(queued_pdf, monkeypatch):
    r = queued_pdf
    upload = await _create_upload(
        r["client"],
        r["envelope"].course_id,
        "teacher",
        "atomic-upload",
        r["content"],
    )
    await _put_direct(upload, r["content"])
    version_id = UUID(upload["version_id"])
    original_dispatch = OutboxJobDispatcher.dispatch

    def fail_after_insert(self, session, job):
        original_dispatch(self, session, job)
        raise RuntimeError("simulated transaction abort")

    monkeypatch.setattr(OutboxJobDispatcher, "dispatch", fail_after_insert)
    with pytest.raises(RuntimeError, match="transaction abort"):
        await r["client"].post(
            f"/api/v1/courses/{r['envelope'].course_id}/documents/{upload['document_id']}"
            f"/versions/{version_id}/complete",
            headers=r["headers"],
        )
    with r["runtime"].database.session() as session:
        version = session.get(DocumentVersion, version_id)
        assert version.state == "AWAITING_UPLOAD"
        assert version.confirmation_idempotency_key is None
        assert (
            session.scalar(
                select(func.count(IngestionJob.id)).where(
                    IngestionJob.document_version_id == version_id
                )
            )
            == 0
        )


async def test_transient_retry_recovers_and_clears_failure_code(queued_pdf, monkeypatch):
    r = queued_pdf
    runtime = r["runtime"]
    real_read = runtime.worker._object_storage.read

    def unavailable(*args, **kwargs):
        raise ObjectStorageUnavailable

    monkeypatch.setattr(runtime.worker._object_storage, "read", unavailable)
    runtime.worker.process(r["envelope"])
    assert _job(r).state == "PENDING"
    monkeypatch.setattr(runtime.worker._object_storage, "read", real_read)
    _expire(r)
    runtime.tick()
    assert _job(r).state == "SUCCEEDED"
    assert _job(r).attempt == 2
    assert _job(r).failure_code is None


async def test_late_relay_commit_cannot_overwrite_retry_dispatch(queued_pdf, monkeypatch):
    r = queued_pdf
    runtime = r["runtime"]

    def unavailable(*args, **kwargs):
        raise ObjectStorageUnavailable

    monkeypatch.setattr(runtime.worker._object_storage, "read", unavailable)

    class FastConsumer:
        def publish(self, kind, fields):
            runtime.transport.publish(kind, fields)
            runtime.transport.consume_one(runtime.worker, reclaim_idle_ms=0)

    assert OutboxRelay(runtime.database, FastConsumer()).publish_one()
    assert _job(r).state == "PENDING"
    with runtime.database.session() as session:
        event = session.get(OutboxEvent, r["envelope"].event_id)
        assert event.published_at is None
        assert event.publish_token is None


async def test_invalid_delivery_is_discarded_without_logging_payload(queued_pdf, caplog):
    r = queued_pdf
    runtime = r["runtime"]
    runtime.transport.publish("ingestion", {"prompt": "private-untrusted-material"})
    with caplog.at_level("INFO", logger="docta.jobs"):
        assert runtime.transport.consume_one(runtime.worker, reclaim_idle_ms=0)
    assert "private-untrusted-material" not in caplog.text
    assert "INVALID_JOB_ENVELOPE" in caplog.text
    assert _job(r).attempt == 0
