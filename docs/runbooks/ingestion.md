# Ingestion operations

Current ingestion contract, introduced in Increment 2B and retained after Phase 0 closure.
See [ADR 0001](../adr/0001-ingestion-durability-and-response-delivery.md) and the
[current-state inventory](../CURRENT_STATE.md). The recovery procedures below remain active.

Documentation reviewed 2026-09-22 against the versioned configuration and architecture. No worker,
migration or recovery exercise was run by this documentation task. For installation and the full
test sequence, use [local development](local-development.md). The pedagogical harness proposal
does not move conversational execution onto the ingestion queue.

## Process and transaction boundaries

The API checks the immutable object version and commits the document's `QUEUED` state,
ingestion job and outbox event in one PostgreSQL transaction. `POST .../complete` returns
202 without executing the parser. A repeated confirmation returns the same logical job.

Run `python -m docta_api.worker` from the installed environment, or use the Compose
`worker` profile described in README. The worker alternates outbox publication and consuming
one job. This is deliberately a single-job-at-a-time process within the monolith.
No database transaction spans a Redis, storage or parser invocation.

An outbox publisher claims an event for 30 seconds, commits, publishes, then conditionally
marks it dispatched. A crash during that sequence can duplicate an event. The consumer
revalidates event/job/course/document/pipeline/correlation against PostgreSQL, claims a job
lease, and increments its attempt and fencing token. Only the current unexpired owner may
commit artifacts. Result, version state and job completion are atomic. Redis ACK and deletion
follow that commit. One configured consumer group owns this queue; do not add another group
expecting the same events to remain after ACK.

## Configuration and limits

- `DOCTA_REDIS_URL`: Redis connection, kept as a secret setting. Local Compose binds Redis
  to loopback and uses AOF with `appendfsync everysec` and `noeviction`.
- `DOCTA_REDIS_STREAM` / `DOCTA_REDIS_GROUP`: ingestion queue/group; dead letters are mirrored
  at `<stream>:dead`. Keep stream/group consistent across worker restarts.
- `DOCTA_JOB_LEASE_SECONDS` (300): an attempt must finish before this lease expires.
  There is no automatic lease extension. Configure above the measured PDF processing duration;
  an expired attempt cannot commit. A stuck native parser requires terminating/restarting its
  worker process; another worker or the restarted process recovers the expired job.
- `DOCTA_JOB_MAX_ATTEMPTS` (3): includes interrupted attempts. Exhaustion records
  `INGESTION_ATTEMPTS_EXHAUSTED` when recovery encounters repeated crashed attempts.
- `DOCTA_JOB_RETRY_BASE_SECONDS` (2): transient storage failures use exponential delay,
  capped at 60 seconds with jitter. The next eligible time is durable in PostgreSQL.
- `DOCTA_JOB_RECONCILE_SECONDS` (30): due unfinished jobs with old dispatch timestamps
  are republished. This recovers even a missing Redis stream or consumer group.

PDF/checksum/size/format failures are terminal and are not retried. Database failures cannot
be acknowledged and recover through the lease. An unknown parser failure is terminal, with
a safe classification. The dead-letter outbox is committed with terminal failure; its Redis
mirror retains approximately 10,000 entries. PostgreSQL remains the durable failure record.

No PDF text, prompts, authorization headers, storage keys, presigned URLs or provider error
payloads are placed in Redis or application logs. Correlation IDs accepted over HTTP are
bounded safe identifiers; malformed values are replaced with generated IDs.

## Inspect safely

Query the authenticated document-version endpoint for queued/processing/terminal state,
job ID and safe failure code. For operator diagnosis, use a database connection with access
to the intended environment and read only metadata:

```sql
SELECT id, course_id, document_version_id, state, attempt, fencing_token,
       next_attempt_at, lease_expires_at, failure_code, correlation_id
FROM ingestion_jobs
ORDER BY created_at DESC LIMIT 20;

SELECT id, job_id, kind, available_at, published_at, publish_lease_until
FROM outbox_events
ORDER BY created_at DESC LIMIT 20;
```

```powershell
docker compose --env-file .env -f infra/compose.yaml logs --tail 50 worker
docker compose --env-file .env -f infra/compose.yaml exec redis redis-cli XINFO GROUPS docta:jobs:ingestion:v1
docker compose --env-file .env -f infra/compose.yaml exec redis redis-cli XPENDING docta:jobs:ingestion:v1 docta-ingestion-workers-v1
```

Use the configured names if overridden. Expected log operations include `job.dispatch`,
`document.ingest`, and `worker.tick`; failures contain safe codes. API readiness deliberately
does not imply queue progress. Pending outbox growth with no worker logs means to inspect
the worker process and Redis connectivity.

## Recover

1. Redis unavailable: restore/start Redis and keep the worker running. Confirmed jobs remain
   in PostgreSQL. Publication leases expire after 30 seconds; the relay retries automatically.
2. Worker terminated: restart it. Pending Redis deliveries can be reclaimed after the idle
   threshold, and the database reconciler republishes expired jobs. Never reset fencing tokens.
3. Redis state lost: restore/start Redis. The worker creates the stream/group if absent and
   republishes due unfinished jobs from PostgreSQL. Completed artifacts are not rebuilt;
   old dead-letter mirror entries may be absent, but their database records remain auditable.
4. Terminal failure: inspect the safe code. Correct the source or infrastructure and create
   a new upload/version through the API. Manual in-place requeue of a failed immutable version
   is not provided in 2B; do not change job state by hand.

AOF everysec can lose recent transport writes on a host crash; database reconciliation is
therefore required even with Redis persistence. These local services are not a high-availability
deployment. Preserve PostgreSQL and MinIO volumes and backups; Redis is not the canonical store.

## Migrations and verification

Stop the old inline API and all workers before upgrading to `20260905_0006`. The forward
migration gives pending jobs an outbox event, recovers old RUNNING jobs with a new fencing
token and records existing failures as dead-letter events. Indexed/active corpora remain intact.
Do not downgrade a live queue: stop consumers and preserve database/outbox/object state first.

Run the complete [local-development sequence](local-development.md#clean-checkout-to-green-checks).
Integration fixtures use only the dedicated test Compose
services and generated databases, buckets and Redis keys. They cover the HTTP-to-worker path,
transaction rollback, duplicate publish, commit-before-ACK recovery, expired-worker fencing,
bounded retries, dead letters, missing stream reconciliation, immutable objects and scope denial.
The Docker worker test restarts the isolated PostgreSQL, Redis and MinIO containers after
committing and dispatching an upload, verifies the persisted stream, and finishes indexing
through the built worker image. Run suites serially because that test restarts shared test
services. Restarting/rebuilding the API/worker retains status through PostgreSQL and MinIO.
