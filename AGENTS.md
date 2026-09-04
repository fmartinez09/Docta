# Docta repository instructions

## Mission

Build Docta as a trustworthy pedagogical RAG tutor. The current objective is the walking skeleton: the thinnest production-shaped vertical slice that proves a student can ask a question about one teacher-provided digital PDF and receive a grounded pedagogical response with auditable citations.

Optimize for evidence, isolation, failure visibility, and replaceable boundaries. Do not optimize for feature breadth or premature distribution.

## Source of truth and instruction order

1. This file defines repository-wide engineering constraints.
2. `docs/WALKING_SKELETON.md` defines the current scope and acceptance criteria.
3. Existing ADRs define accepted architectural decisions. Do not silently contradict them.
4. The issue or user prompt defines the current bounded task.

If documents disagree, report the contradiction and choose the least expansive implementation until it is resolved.

## Working style

- Inspect the repository before changing it. Preserve user changes and existing conventions.
- Before implementation, state the vertical behavior being added, affected modules, tests, and any assumption.
- Work in small, reviewable increments. Finish one end-to-end behavior before broad scaffolding.
- Prefer simple, explicit code and typed boundaries over frameworks or generic abstractions.
- Do not introduce a service, dependency, datastore, queue, or deployment component unless the current acceptance criteria require it.
- Never hide incomplete production behavior behind mocks. Mocks and fakes are allowed only at explicit ports and in tests.
- Add or update tests with each behavior. Run the narrow tests first, then the complete repository checks.
- Update `docs/WALKING_SKELETON.md` only when implementation evidence changes a checklist item. Architectural changes require an ADR.
- At handoff, report changed files, commands run, results, known limitations, and the next smallest vertical step.

## Architecture guardrails

The walking skeleton is a modular monolith, not a set of microservices.

- Web: Next.js with TypeScript.
- API: FastAPI with Python.
- Persistence: PostgreSQL with full-text search; keep the retrieval port compatible with pgvector.
- Object storage: an S3-compatible interface, with MinIO for local development.
- RAG, pedagogy, identity, storage, and job dispatch are module boundaries inside the monolith.
- The deployment unit may contain separate web, API, and worker processes, but they share one codebase and one domain model.

Expected top-level shape:

```text
apps/
  web/
  api/
  worker/
packages/
  domain/
  rag/
  safety/
tests/
  unit/
  integration/
  e2e/
infra/
docs/
```

Adapt this shape to established repository conventions if the repo already exists. Do not reorganize working code merely to match the tree.

## Required invariants

### Tenant and course isolation

- Every conversation belongs to exactly one course.
- Every document, document version, chunk, retrieval request, message, citation, and job is scoped to a course.
- Course scope comes from authorized server-side context, never solely from a client-provided filter.
- Retrieval must fail closed when scope is absent or inconsistent.
- Tests must prove that content from course B cannot appear in course A retrieval or citations.

### Documents and citations

- Phase 0 accepts only text-based digital PDFs. Reject unsupported or image-only files explicitly; do not silently add OCR.
- Preserve immutable document-version identity and page/fragment provenance.
- New retrieval uses only the active indexed corpus version.
- Historical citations retain enough metadata to remain auditable if a document is later withdrawn.
- A citation must reference a real retrieved chunk used to form the answer. Never fabricate citations.

### Conversation durability

- Persist the student's question before invoking retrieval or a model.
- Use explicit message state transitions such as `pending -> completed` or `pending -> failed`.
- Persist the final validated answer and citations atomically.
- On any terminal failure, persist a safe error classification without raw secrets or sensitive provider payloads.
- Retrying a client request must not create duplicate logical messages. Use an idempotency key at the write boundary.
- Keep full history for user continuity and audit. Prompt construction uses a bounded window; versioned summarization is later scope.

### Answer policy

- Default to pedagogical guidance, hints, questions, and verification rather than immediately giving a complete solution.
- Base factual claims about course material on retrieved evidence.
- If evidence is insufficient, abstain clearly; do not answer from general model memory as though it came from the course.
- Validate the complete response before exposing it. Phase 0 must not stream unvalidated tokens.

## Integration seams to preserve

These are contracts in phase 0, not mandates to deploy every final component:

- Identity: validate standard OIDC JWT claims through an `IdentityProvider` boundary. Production authorization must not depend on a development-only header or hard-coded user.
- Uploads: expose a presigned-upload contract through an `ObjectStorage` port. Local MinIO is the first adapter.
- Responses: an SSE-compatible response boundary may emit lifecycle events and one validated final answer. Do not emit raw model tokens before validation.
- Jobs: call a `JobDispatcher` port. The initial adapter may run inline or use a database-backed test implementation. Redis Streams is the intended durable asynchronous adapter after the skeleton proves the flow; Redis Pub/Sub is not a job queue.
- Retrieval: start with the smallest real PostgreSQL retrieval implementation that satisfies the acceptance tests. Dense retrieval, RRF, and reranking must be added behind the retrieval port, not wired through handlers.
- Publication: keep versions immutable and activate an indexed corpus through a conditional/atomic pointer update. Phase 0 needs only the single active-version path, not rollback UI or a full publishing workflow.

## HTTP and data conventions

- Version public routes under `/api/v1`.
- Use generated stable identifiers; do not expose storage keys as domain identifiers.
- Use UTC timestamps and explicit enums for state.
- Return machine-readable error codes plus safe human-readable messages.
- Require an idempotency key for state-changing operations that a client may retry.
- Propagate a correlation/request ID from HTTP entry through storage, retrieval, model invocation, and logs.
- Keep secrets out of source, fixtures, logs, exceptions, and client responses.

## Test requirements

The walking skeleton is not complete unless automated tests cover:

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

## Observability baseline

Emit structured logs with correlation ID, course ID, conversation ID, message ID, document version ID, operation, duration, and outcome where applicable. Never log raw PDF contents, prompts, answers, tokens, credentials, presigned URLs, or authorization headers.

The minimum traceable chain is:

```text
upload -> process -> index -> retrieve -> generate -> validate -> persist -> respond
```

## Explicitly out of scope for the walking skeleton

- OCR, scanned PDFs, multimodal document understanding, and advanced layout recovery.
- Moodle/LMS integration, mobile apps, and offline mode.
- Microservices, Kubernetes, Kafka, service mesh, and Elasticsearch.
- GraphRAG, agents, multi-agent orchestration, and elaborate intent taxonomies.
- Redis Streams runtime infrastructure until durable asynchronous execution is the next measured need.
- Permanent cross-encoder reranking, advanced hybrid tuning, or learned query routing.
- Token-by-token output before safety and citation validation.
- Advanced analytics, dashboards, experimentation platforms, and automated pedagogical scoring.
- Full publishing UI, rollback UI, and multi-version authoring workflows.

When asked for an out-of-scope feature, identify it as such and propose the smallest compatible seam or ADR; do not silently build it.

