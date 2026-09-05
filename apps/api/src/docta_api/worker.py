"""Run the ingestion relay and consumer: python -m docta_api.worker."""

import logging
import signal
from threading import Event
from time import monotonic

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from docta_api.config import Settings, get_settings
from docta_api.database import Database
from docta_api.ingestion import IngestionWorker
from docta_api.jobs import OutboxRelay, log_job
from docta_api.pymupdf_parser import PyMuPDFDocumentParser
from docta_api.redis_jobs import RedisJobTransport
from docta_api.s3_object_storage import S3ObjectStorage


class WorkerRuntime:
    def __init__(self, settings: Settings) -> None:
        if not settings.object_storage_configured:
            raise ValueError("worker requires S3-compatible object storage")
        self.settings = settings
        self.database = Database(str(settings.database_url))
        self.transport = RedisJobTransport(
            url=settings.redis_url.get_secret_value(),
            stream=settings.redis_stream,
            group=settings.redis_group,
            timeout_seconds=settings.dependency_timeout_seconds,
        )
        self.relay = OutboxRelay(self.database, self.transport)
        storage = S3ObjectStorage(
            endpoint_url=str(settings.s3_endpoint_url),
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key.get_secret_value(),
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            upload_ttl_seconds=settings.upload_ttl_seconds,
            timeout_seconds=settings.dependency_timeout_seconds,
        )
        self.worker = IngestionWorker(
            database=self.database,
            object_storage=storage,
            parser=PyMuPDFDocumentParser(max_pages=settings.max_document_pages),
            max_document_bytes=settings.max_document_bytes,
            chunk_size_characters=settings.chunk_size_characters,
            chunk_overlap_characters=settings.chunk_overlap_characters,
            lease_seconds=settings.job_lease_seconds,
            max_attempts=settings.job_max_attempts,
            retry_base_seconds=settings.job_retry_base_seconds,
        )
        self._last_reconcile = float("-inf")

    def tick(self) -> bool:
        if monotonic() - self._last_reconcile >= self.settings.job_reconcile_seconds:
            self.relay.reconcile(self.settings.job_reconcile_seconds)
            self._last_reconcile = monotonic()
        published = self.relay.publish_one()
        consumed = self.transport.consume_one(
            self.worker,
            reclaim_idle_ms=self.settings.job_lease_seconds * 1000,
        )
        return published or consumed

    def close(self) -> None:
        self.transport.close()
        self.database.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stopped = Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    runtime = WorkerRuntime(get_settings())
    log_job("worker.lifecycle", "started")
    try:
        while not stopped.is_set():
            try:
                busy = runtime.tick()
            except (RedisError, SQLAlchemyError):
                log_job("worker.tick", "unavailable", error_code="DEPENDENCY_UNAVAILABLE")
                stopped.wait(2)
            except Exception:
                # Do not leak parser, storage, SQL or transport exception payloads.
                log_job("worker.tick", "failed", error_code="WORKER_OPERATION_FAILED")
                stopped.wait(2)
            else:
                if not busy:
                    stopped.wait(0.5)
    finally:
        runtime.close()
        log_job("worker.lifecycle", "stopped")


if __name__ == "__main__":
    main()
