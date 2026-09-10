# Docta repository instructions

## Mission

Build Docta as a trustworthy pedagogical RAG tutor. Phase 0 (the walking skeleton, Increments 0–4 including 2B) is complete. The current objective is Phase 1: measure and improve retrieval, answerability and pedagogical quality while preserving the proven vertical flow. The next implementation slice is Increment 5, an evaluation harness and reviewed dataset v0; later increments are gated plans, not already implemented capabilities.

Optimize for evidence, isolation, failure visibility, and replaceable boundaries. Do not optimize for feature breadth or premature distribution.

## Source of truth and instruction order

1. This file defines repository-wide engineering constraints.
2. `docs/PHASE_1_TUTOR_QUALITY.md` defines the active phase, next slice and acceptance criteria.
3. Existing ADRs define accepted architectural decisions. `docs/DOCTA_ARCHITECTURE.md` describes invariants and target designs; a specific accepted ADR takes precedence over a conceptual example.
4. `docs/CURRENT_STATE.md` maps implemented behavior to code and evidence. `docs/WALKING_SKELETON.md` preserves Phase 0 history, not the active backlog.
5. The issue or user prompt defines the current bounded task. A roadmap entry alone does not authorize implementing every future increment.

If documents disagree, report the contradiction and choose the least expansive implementation until it is resolved.

## Working style

- Inspect the repository before changing it. Preserve user changes and existing conventions.
- Before implementation, state the vertical behavior being added, affected modules, tests, and any assumption.
- Work in small, reviewable increments. Finish one end-to-end behavior before broad scaffolding.
- Prefer simple, explicit code and typed boundaries over frameworks or generic abstractions.
- Do not introduce a service, dependency, datastore, queue, or deployment component unless the current acceptance criteria require it.
- Never hide incomplete production behavior behind mocks. Mocks and fakes are allowed only at explicit ports and in tests.
- Add or update tests with each behavior. Run the narrow tests first, then the complete repository checks.
- Update the active phase checklist and `docs/CURRENT_STATE.md` only with implementation evidence. Preserve Phase 0 checklists and dated verification records; append explicit historical corrections rather than rewriting past results. Architectural changes require an ADR.
- For documentation-only changes, verify links, referenced paths, status consistency and `git diff --check`. Runtime suites are required for behavior changes; documentation must distinguish recorded historical test results from checks run in the current task.
- Quality changes need a versioned baseline, reviewed cases and acceptance gates defined before comparing candidates. Deterministic model fakes prove contracts, not model quality or learning outcomes.
- At handoff, report changed files, commands run, results, known limitations, and the next smallest vertical step.

## Architecture guardrails

Docta is a modular monolith, not a set of microservices.

- Web: Next.js with TypeScript.
- API: FastAPI with Python.
- Persistence: PostgreSQL with full-text search. pgvector and RRF are candidates for Increment 7, subject to an ADR and measured adoption gates; they are not installed in the current Compose stack.
- Object storage: an S3-compatible interface, with MinIO for local development.
- RAG, pedagogy, identity, storage, and job dispatch are module boundaries inside the monolith.
- The deployment unit may contain separate web, API, and worker processes, but they share one codebase and one domain model.

Current layout (preserve established boundaries):

```text
apps/
  web/
  api/src/docta_api/    # API, domain/application boundaries and worker entry point
  api/migrations/
scripts/
tests/
  unit/
  integration/
  e2e/
infra/
docs/
```

Create evaluation files only as Increment 5 needs them. Do not create empty packages or move the worker merely to match a target architecture tree.

## Required invariants

### Tenant and course isolation

- Every conversation belongs to exactly one course.
- Every document, document version, chunk, retrieval request, message, citation, and job is scoped to a course.
- Course scope comes from authorized server-side context, never solely from a client-provided filter.
- Retrieval must fail closed when scope is absent or inconsistent.
- Tests must prove that content from course B cannot appear in course A retrieval or citations.

### Documents and citations

- The supported input remains text-based digital PDFs. Reject unsupported or image-only files explicitly; do not silently add OCR.
- Preserve immutable document-version identity and page/fragment provenance.
- Accepting a new question captures the authorized course's active indexed corpus. All retrieval for that question uses that captured version, even if publication changes during execution.
- Historical citations retain enough metadata to remain auditable if a document is later withdrawn.
- A citation must reference a real retrieved chunk used to form the answer. Never fabricate citations.

### Conversation durability

- Persist the student's question before invoking retrieval or a model.
- Use explicit message state transitions such as `pending -> completed` or `pending -> failed`.
- Persist the final validated answer and citations atomically.
- On any terminal failure, persist a safe error classification without raw secrets or sensitive provider payloads.
- Retrying a client request must not create duplicate logical messages. Use an idempotency key at the write boundary.
- Keep full history for user continuity and audit. Prompt construction uses a bounded window; versioned summarization is later scope.
- Preserve the exact original question and its idempotency semantics. Future standalone queries must be separate, versioned server-derived data bound to the same authorized message and captured corpus; never remove scope checks merely to permit rewriting.

### Answer policy

- Default to pedagogical guidance, hints, questions, and verification rather than immediately giving a complete solution.
- Base factual claims about course material on retrieved evidence.
- If evidence is insufficient, abstain clearly; do not answer from general model memory as though it came from the course.
- Strict course grounding remains the only supported mode. General-knowledge fallback requires a separate product decision and ADR; it is not part of the active plan.
- Validate the complete response before exposing it. Never stream unvalidated tokens.
- Structured clarification and one bounded second retrieval are planned for Increment 10, not current runtime behavior. Do not introduce provider retries, repair loops or autonomous agent loops incidentally.

## Integration seams to preserve

Preserve these implemented contracts while evolving their adapters:

- Identity: validate standard OIDC JWT claims through an `IdentityProvider` boundary. Production authorization must not depend on a development-only header or hard-coded user.
- Uploads: expose a presigned-upload contract through an `ObjectStorage` port. Local MinIO is the first adapter.
- Responses: an SSE-compatible response boundary may emit lifecycle events and one validated final answer. Do not emit raw model tokens before validation.
- Jobs: call the `JobDispatcher` port backed by a transactional PostgreSQL outbox and Redis Streams consumer groups (ADR 0001, implemented in 2B). PostgreSQL owns job state; workers use leases, fencing and idempotent execution. Redis Pub/Sub is not a job queue. Conversation generation remains API-owned with durable deadlines (ADR 0002), separate from ingestion transport.
- Retrieval: preserve the real Spanish FTS baseline. Query resolution, dense retrieval, RRF and reranking belong behind typed application/retrieval boundaries, not in handlers. Changing ADR 0002's query/FTS contract needs an explicit superseding decision for the affected part.
- Publication: keep versions immutable and activate an indexed corpus through a conditional/atomic pointer update. The current single active-version path does not imply a rollback UI or full publishing workflow.
- Browser: preserve the Next.js BFF, PKCE and encrypted HttpOnly session contract in ADR 0003; FastAPI remains the authorization authority. OpenUI is an optional later presentation adapter, never the pedagogical authority.

## HTTP and data conventions

- Version public routes under `/api/v1`.
- Use generated stable identifiers; do not expose storage keys as domain identifiers.
- Use UTC timestamps and explicit enums for state.
- Return machine-readable error codes plus safe human-readable messages.
- Require an idempotency key for state-changing operations that a client may retry.
- Preserve documented compatibility exceptions: course creation accepts an optional key for older API clients (the browser always supplies one); corpus activation uses compare-and-swap and reconciliation (ADR 0003).
- Propagate a correlation/request ID from HTTP entry through storage, retrieval, model invocation, and logs.
- Keep secrets out of source, fixtures, logs, exceptions, and client responses.

## Test requirements

Preserve automated regression coverage for:

- the happy-path vertical flow;
- course isolation during retrieval;
- unsupported or invalid PDF rejection;
- insufficient-evidence abstention;
- citation-to-chunk integrity;
- question persistence before a simulated model failure;
- `pending -> failed` and `pending -> completed` transitions;
- idempotent retry of message creation;
- activation only after indexing succeeds;
- restart/rebuild from migrations and durable object/database state.

Prefer real PostgreSQL and MinIO in integration tests where their semantics matter. Use deterministic fakes for the model provider so tests do not require network access, paid APIs, or nondeterministic generations.

Evaluation runs must use isolated database/object/stream resources. Do not reset development data or automatically export private student histories into datasets. Keep raw educational evaluation artifacts out of operational logs; use reviewed synthetic or explicitly authorized material and record provenance, review status and dataset splits.

## Observability baseline

Emit structured logs with correlation ID, course ID, conversation ID, message ID, document version ID, operation, duration, and outcome where applicable. Never log raw PDF contents, prompts, answers, tokens, credentials, presigned URLs, or authorization headers.

The minimum traceable chain is:

```text
upload -> process -> index -> retrieve -> generate -> validate -> persist -> respond
```

## Scope boundaries for Phase 1

The active slice is defined in `docs/PHASE_1_TUTOR_QUALITY.md`. Offline evaluation and a small pedagogical taxonomy are in the plan; their presence does not authorize a hosted experimentation platform or unrestricted agent execution. The following remain excluded unless a bounded task and applicable decision explicitly promote them:

- OCR, scanned PDFs, multimodal document understanding, and advanced layout recovery.
- Moodle/LMS integration, mobile apps, and offline mode.
- Microservices, Kubernetes, Kafka, service mesh, and Elasticsearch.
- GraphRAG, agents, multi-agent orchestration, and elaborate intent taxonomies.
- Redis infrastructure beyond the Increment 2B ingestion scope accepted in ADR 0001.
- Permanent cross-encoder reranking, advanced hybrid tuning, or learned query routing.
- Token-by-token output before safety and citation validation.
- Advanced analytics, dashboards and online experimentation platforms. Offline rubric-based scoring is planned; it must not be presented as proven educational impact.
- Full publishing UI, rollback UI, and multi-version authoring workflows.

When asked for an out-of-scope feature, identify it as such and propose the smallest compatible seam or ADR; do not silently build it.
