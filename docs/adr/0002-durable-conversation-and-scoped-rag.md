# ADR 0002 — Durable conversation and scoped RAG

Date: 2026-09-07  
Status: Implemented for Increment 3; concrete provider/model selection remains open

## Context

Increment 2B supplies durable ingestion and immutable indexed corpora. ADR 0001 requires
SSE lifecycle events, a validated persisted final payload, and execution independent of
the browser socket. The tutor provider, model and gateway deployment are still undecided.

## Decision

1. Add course-scoped, owner-private conversations. Any current course member can create a
   conversation, including teachers testing their own material. Membership never grants access
   to another member's history. Student provisioning continues to use existing membership data;
   enrollment management and teacher access to individual student conversations are not added.
2. Represent one logical message as the durable student question plus a nullable tutor response.
   `pending -> completed|failed` applies to this aggregate. This is the smallest representation
   of the recommended turn concept, without separate turn and assistant-message tables.
   A conversation lock orders messages and permits one pending question per conversation.
   Unique keys deduplicate conversation creation and question submission; changed question text
   under the same key produces a conflict. Comparison uses the exact submitted text.
3. Capture the authorized course's READY active corpus when accepting the question. Commit the
   question before retrieval. A missing active corpus leaves a durable failed question.
   Subsequent publication does not change an already accepted request's captured version.
4. Retrieve at most five chunks using existing Spanish PostgreSQL FTS, AND semantics and
   deterministic rank/ordinal order. The retriever rechecks durable scope and membership.
   Persist the exact evidence and provenance before calling the model; validate snapshots
   against stored chunks. Composite foreign keys bind citations to evidence retrieved for that
   same question/course/corpus. Historical evidence retains text, title, page/fragment, document
   checksum and chunk hash after withdrawal. Physical deletion of referenced chunks is restricted.
5. Keep `Retriever`, `TutorModel` and `ResponseValidator` ports independent of handlers and
   vendor payloads. Use existing HTTPX for a configurable Chat Completions endpoint supporting
   strict JSON Schema, including compatible gateways. No LiteLLM service, dependency, fallback
   or default paid model is introduced. Configuration must include endpoint/model/key together;
   absent configuration fails explicitly with `MODEL_NOT_CONFIGURED` when evidence needs a model.
   Requests disable streaming/storage, bound output tokens/bytes and enforce timeouts. No automatic
   model retries are made. Concrete model quality, privacy and spending approval remain pending.
6. Send a bounded history window (up to six completed messages, 12,000 characters) alongside
   current evidence. Full history stays in PostgreSQL and is available through pagination.
   Treat PDFs and history as untrusted data; factual guidance must use current evidence.
   Validate schema, mode, nonempty answer, grounded/citation consistency, exact quotations and
   scope. Empty retrieval abstains without a model; provider abstentions use safe canonical text.
   These deterministic checks establish provenance, not semantic entailment of every generated
   sentence. Model/pedagogical quality requires evaluation before a student pilot.
7. Commit answer and citation rows together using a conditional pending-state lock; terminal
   failures contain only an allowlisted classification. API-owned asyncio tasks run independently
   of SSE, with a fixed durable deadline. Shutdown, timeout or abandoned work becomes failed;
   never automatically repeat an ambiguous provider call. A five-second recovery sweep and
   authenticated reads/retries reconcile expired pending messages. Late completion is fenced.
   If PostgreSQL is unavailable, recovery waits for it rather than claiming an uncommitted outcome.
8. POST messages returns SSE: accepted, bounded retrieval/generation/validation progress, and
   one completed or failed payload read from committed history. Disconnecting cancels only
   delivery. Repeating the same POST observes the same message and never schedules it again.
   GET history and GET message status remain JSON; event IDs are correlatable, not a replay log.

## Alternatives and consequences

A Redis conversation queue would expand the ingestion-only decision and add provider retry
ambiguity. Request-owned execution would lose work on disconnect. The selected API task plus
durable deadline preserves questions and gives an explicit terminal result after process loss,
but intentionally does not promise automatic successful generation after an API crash.

Strict all-term FTS favors conservative abstention over recall. Vector search, query rewriting,
semantic judges, full-solution policy, gateway routing and conversation summaries remain deferred.
Separate user/assistant message tables can be introduced when editing/branching history warrants
them; the public question/response aggregate already exposes each logical interaction explicitly.

## Evidence and reversal

`tests/integration/test_conversations.py` exercises real PDF ingestion through the worker,
student membership, scoped retrieval, persistence before model invocation, citation integrity,
transaction rollback, concurrent retries, socket disconnection, corpus capture, history after
withdrawal/restart, bounded prompts and expired-work fencing. Model fakes exist only in tests.
`tests/unit/test_rag.py` and `tests/unit/test_tutor_http.py` cover invalid outputs and the HTTP port.

Protocol reference: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
The adapter checks refusal and incomplete output rather than exposing provider text.

To reverse this implementation, stop API executions and preserve/export conversation tables and
evidence first. Migration downgrade removes conversation data and is only exercised in the
isolated integration database. No production downgrade or data deletion is an operational retry.

## Subsequent status note — 2026-09-22

This remains the runtime contract. ADR 0003 refined HTTP-provider compatibility; ADR 0004
planned quality evaluation without adopting query rewriting, tools or automatic provider retries.
The existing `Retriever` already returns evidence independently of generation. Its exact-question
binding must be explicitly refined before accepting derived queries, not removed incidentally.

The [harness proposal](../DOCTA_HARNESS_ARCHITECTURE.md) considers activity/run state, tool calls,
new events and recovery between steps. Those are not implemented by this ADR. Preserve current
atomic completion and idempotency through any migration; do not rebuild them in parallel tables
or defer them until after a loop is introduced. See [open decisions](../DECISIONS.md).
No runtime suites or live-provider checks were rerun by this documentation review.
