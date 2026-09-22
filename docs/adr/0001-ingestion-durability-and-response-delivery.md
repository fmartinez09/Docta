# ADR 0001 — Durable ingestion before conversational RAG

Date: 2026-09-05  
Status: Accepted by project owner

## Context

Increments 0–2 implement direct PDF uploads, immutable storage versions, parsing,
PostgreSQL FTS and conditional corpus activation. Ingestion currently executes inline.
AGENTS.md and WALKING_SKELETON.md deferred Redis, while DOCTA_ARCHITECTURE.md required
Streams during ingestion. The project owner chooses to prove durable asynchronous
ingestion in Increment 2B before starting Increment 3.

The conversation contract also differed: the skeleton allowed JSON while the architecture
required SSE. Model and gateway selection remain independent of ingestion.

## Decision

1. Implement Increment 2B with Redis Streams behind JobDispatcher. PostgreSQL stores
   canonical job state and a transactional outbox; Redis transports identifiers only.
   An outbox relay and an ingestion consumer run in the worker process, in this monolith.
2. Commit job creation and its outbox event together. Publish outside database transactions.
   Assume duplicate deliveries. Use leases, fencing, bounded transient retries, abandoned
   delivery recovery and durable terminal failure/dead-letter records. Acknowledge only
   after committing the result. Reconcile unfinished database jobs after transport loss.
3. Preserve immutable document versions, course isolation and activation only after indexing.
   Test with real PostgreSQL, Redis and MinIO in isolated test state, including restart,
   duplicate delivery, stale workers, Redis outages and exhausted retries.
4. Increment 3 will deliver lifecycle events and one validated, persisted final response
   over SSE. Ordinary queries remain JSON. PostgreSQL history reconciles disconnects;
   neither SSE nor Redis Pub/Sub owns conversation durability. No unvalidated model tokens.
5. LiteLLM is the preferred gateway candidate for Increment 3. Deployment, concrete
   guardrail integrations, provider, model and spending limits remain pending evaluation.
   No gateway/model service is required for Increment 2B. Docta retains its own scope,
   citation and response validation behind domain ports.

## Alternatives and consequences

Keeping inline ingestion would be smaller but leaves crash recovery unproven. A database
queue alone avoids Redis but differs from the selected execution architecture. Streams
adds an operational dependency and requires explicit at-least-once handling; it does not
make execution exactly once. A separate relay microservice is unnecessary for this scope.

SSE provides visible progress while the complete response is validated. It adds connection
handling and must not tie the persisted operation's lifetime to the browser socket.

## Scope, evidence and reversal

Deliver decision documentation directly to develop, implement on feature/redis-2b, run
checks, review, push and merge before evaluating Increment 3. Do not implement chat,
model routing, Pub/Sub, OCR or advanced worker distribution in 2B.

At acceptance, only the inline baseline exists; this ADR does not claim Redis or SSE is
implemented. The 2B checklist must be updated only with passing implementation evidence.
Any rollback must stop consumers and preserve database jobs/outbox and object versions;
never silently fall back to inline processing or discard pending work. Changes to these
decisions require a superseding ADR.

## Subsequent status note — 2026-09-22

The context above describes the pre-2B system, not today's execution. Ingestion/outbox/Streams
were implemented in 2B; validated SSE was implemented in 3. ADR 0002 chose the configurable
HTTP model boundary without making a gateway mandatory. The historical LiteLLM preference
is not a claim that it is installed or a current instruction to deploy it.
See the [implementation inventory](../CURRENT_STATE.md) and [decision history](../DECISIONS.md).
This documentation review did not rerun ingestion or acceptance tests and changes no decision.
