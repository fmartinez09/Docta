"""Redis Streams adapter for the single ingestion consumer group."""

from uuid import uuid4

from redis import Redis
from redis.exceptions import ResponseError

from docta_api.ingestion import IngestionWorker, InvalidJobEnvelope
from docta_api.jobs import JobEnvelope, log_job


class RedisJobTransport:
    def __init__(
        self,
        *,
        url: str,
        stream: str,
        group: str,
        timeout_seconds: int = 2,
        consumer: str | None = None,
    ) -> None:
        self.client = Redis.from_url(
            url,
            decode_responses=True,
            socket_timeout=timeout_seconds,
            socket_connect_timeout=timeout_seconds,
        )
        self.stream = stream
        self.group = group
        self.consumer = consumer or f"worker-{uuid4().hex}"
        self._claim_cursor = "0-0"

    def close(self) -> None:
        self.client.close()

    def ensure_group(self) -> None:
        try:
            self.client.xgroup_create(self.stream, self.group, id="0-0", mkstream=True)
        except ResponseError as error:
            if not str(error).startswith("BUSYGROUP"):
                raise

    def publish(self, kind: str, fields: dict[str, str]) -> None:
        if kind == "dead":
            self.client.xadd(f"{self.stream}:dead", fields, maxlen=10000, approximate=True)
        else:
            # Do not trim pending deliveries. ACK + deletion below bounds completed entries.
            self.client.xadd(self.stream, fields)

    def consume_one(self, worker: IngestionWorker, reclaim_idle_ms: int) -> bool:
        self.ensure_group()
        claimed = self.client.xautoclaim(
            self.stream,
            self.group,
            self.consumer,
            min_idle_time=reclaim_idle_ms,
            start_id=self._claim_cursor,
            count=1,
        )
        self._claim_cursor = claimed[0]
        messages = claimed[1]
        if not messages:
            received = self.client.xreadgroup(
                self.group,
                self.consumer,
                {self.stream: ">"},
                count=1,
            )
            messages = received[0][1] if received else []
        if not messages:
            return False
        message_id, fields = messages[0]
        try:
            envelope = JobEnvelope.parse(fields)
        except (ValueError, TypeError):
            log_job("job.consume", "rejected", error_code="INVALID_JOB_ENVELOPE")
            self.acknowledge(message_id)
            return True
        try:
            can_acknowledge = worker.process(envelope)
        except InvalidJobEnvelope:
            # Never log untrusted envelope contents, even its alleged correlation ID.
            log_job("job.consume", "rejected", error_code="JOB_SCOPE_MISMATCH")
            can_acknowledge = True
        if can_acknowledge:
            self.acknowledge(message_id)
        return True

    def acknowledge(self, message_id: str) -> None:
        # One configured consumer group owns this queue; canonical history lives in PostgreSQL.
        with self.client.pipeline(transaction=True) as transaction:
            transaction.xack(self.stream, self.group, message_id)
            transaction.xdel(self.stream, message_id)
            transaction.execute()
