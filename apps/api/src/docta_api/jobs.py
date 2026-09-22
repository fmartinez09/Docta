"""Durable job dispatch. PostgreSQL is authoritative; transport is replaceable."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from docta_api.database import Database
from docta_api.models import IngestionJob, OutboxEvent

logger = logging.getLogger("docta.jobs")


class JobDispatcher(Protocol):
    def dispatch(self, session: Session, job: IngestionJob) -> None:
        """Enlist dispatch in the caller's database transaction; no network effects."""
        ...


class OutboxJobDispatcher:
    def dispatch(self, session: Session, job: IngestionJob) -> None:
        session.flush()
        session.execute(
            insert(OutboxEvent)
            .values(
                id=uuid4(),
                course_id=job.course_id,
                job_id=job.id,
                kind="ingestion",
            )
            .on_conflict_do_nothing(constraint="uq_outbox_job_kind")
        )


def schedule_retry(session: Session, job: IngestionJob, available_at: datetime) -> None:
    session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.job_id == job.id,
            OutboxEvent.kind == "ingestion",
        )
        .values(
            available_at=available_at,
            published_at=None,
            publish_token=None,
            publish_lease_until=None,
        )
    )


def record_dead_letter(session: Session, job: IngestionJob) -> None:
    session.execute(
        insert(OutboxEvent)
        .values(
            id=uuid4(),
            course_id=job.course_id,
            job_id=job.id,
            kind="dead",
        )
        .on_conflict_do_nothing(constraint="uq_outbox_job_kind")
    )


@dataclass(frozen=True)
class JobEnvelope:
    event_id: UUID
    job_id: UUID
    course_id: UUID
    document_version_id: UUID
    pipeline_version: str
    correlation_id: str
    created_at: str

    def fields(self) -> dict[str, str]:
        return {
            "schema_version": "1",
            "event_id": str(self.event_id),
            "job_id": str(self.job_id),
            "course_id": str(self.course_id),
            "document_version_id": str(self.document_version_id),
            "pipeline_version": self.pipeline_version,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
        }

    @classmethod
    def parse(cls, fields: dict[str, str]) -> "JobEnvelope":
        if (
            set(fields)
            != {
                "schema_version",
                "event_id",
                "job_id",
                "course_id",
                "document_version_id",
                "pipeline_version",
                "correlation_id",
                "created_at",
            }
            or fields["schema_version"] != "1"
        ):
            raise ValueError("invalid job envelope")
        if any(len(value) > 100 for value in fields.values()):
            raise ValueError("oversized job envelope")
        return cls(
            event_id=UUID(fields["event_id"]),
            job_id=UUID(fields["job_id"]),
            course_id=UUID(fields["course_id"]),
            document_version_id=UUID(fields["document_version_id"]),
            pipeline_version=fields["pipeline_version"],
            correlation_id=fields["correlation_id"],
            created_at=fields["created_at"],
        )


def envelope_for(event: OutboxEvent, job: IngestionJob) -> JobEnvelope:
    return JobEnvelope(
        event_id=event.id,
        job_id=job.id,
        course_id=job.course_id,
        document_version_id=job.document_version_id,
        pipeline_version=job.pipeline_version,
        correlation_id=job.correlation_id,
        created_at=event.created_at.isoformat(),
    )


class JobPublisher(Protocol):
    def publish(self, kind: str, fields: dict[str, str]) -> None: ...


class OutboxRelay:
    def __init__(self, database: Database, publisher: JobPublisher) -> None:
        self._database = database
        self._publisher = publisher

    def publish_one(self) -> bool:
        token = uuid4()
        with self._database.session() as session:
            now = session.scalar(select(func.clock_timestamp()))
            row = session.execute(
                select(OutboxEvent, IngestionJob)
                .join(
                    IngestionJob,
                    IngestionJob.id == OutboxEvent.job_id,
                )
                .where(
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.available_at <= now,
                    or_(
                        OutboxEvent.publish_lease_until.is_(None),
                        OutboxEvent.publish_lease_until <= now,
                    ),
                )
                .order_by(OutboxEvent.available_at, OutboxEvent.id)
                .limit(1)
                .with_for_update(of=OutboxEvent, skip_locked=True)
            ).first()
            if row is None:
                return False
            event, job = row
            event.publish_token = token
            event.publish_lease_until = now + timedelta(seconds=30)
            kind = event.kind
            envelope = envelope_for(event, job)
            fields = envelope.fields()
            if kind == "dead":
                fields["failure_code"] = job.failure_code or "DOCUMENT_PROCESSING_FAILED"
                fields["attempt"] = str(job.attempt)

        # A crash after XADD but before this commit may duplicate delivery, intentionally.
        self._publisher.publish(kind, fields)
        with self._database.session() as session:
            session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.id == envelope.event_id,
                    OutboxEvent.publish_token == token,
                )
                .values(published_at=func.now(), publish_token=None, publish_lease_until=None)
            )
        log_job("job.dispatch", "published", envelope)
        return True

    def reconcile(self, stale_seconds: int) -> int:
        """Republish due unfinished work, even if Redis lost its stream or group.

        Stable event identity and the job's lease/fence protect against the extra deliveries.
        Dead letters remain in PostgreSQL even if their bounded Redis mirror is lost.
        """
        with self._database.session() as session:
            now = session.scalar(select(func.clock_timestamp()))
            due_jobs = select(IngestionJob.id).where(
                or_(
                    and_(IngestionJob.state == "PENDING", IngestionJob.next_attempt_at <= now),
                    and_(IngestionJob.state == "RUNNING", IngestionJob.lease_expires_at <= now),
                )
            )
            result = session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.kind == "ingestion",
                    OutboxEvent.job_id.in_(due_jobs),
                    OutboxEvent.published_at <= now - timedelta(seconds=stale_seconds),
                )
                .values(
                    published_at=None,
                    available_at=now,
                    publish_token=None,
                    publish_lease_until=None,
                )
            )
            return result.rowcount


def log_job(
    operation: str, outcome: str, envelope: JobEnvelope | None = None, error_code: str | None = None
) -> None:
    values = {"operation": operation, "outcome": outcome, "error_code": error_code}
    if envelope is not None:
        values.update(
            {
                key: value
                for key, value in envelope.fields().items()
                if key in {"job_id", "course_id", "document_version_id", "correlation_id"}
            }
        )
    logger.info(json.dumps(values, separators=(",", ":")))
