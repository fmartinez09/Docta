# Docta walking skeleton

Status: implementation specification  
Scope: Phase 0  
Primary risk under test: Docta can produce a course-isolated, grounded and auditable pedagogical answer from one digital PDF.

## 1. Outcome

One teacher can create a course, upload one text-based PDF, make its indexed version active, and let one student ask a question. Docta persists the question before invoking the RAG path and returns either:

- a validated pedagogical answer with citations to real PDF pages/fragments; or
- a clear abstention or recorded failure.

This is a production-shaped proof, not a feature-complete MVP.

## 2. Demonstrable path

```text
authorized teacher
  -> creates course
  -> requests presigned upload
  -> uploads digital PDF to MinIO
  -> confirms upload
  -> processes and indexes document version
  -> atomically activates indexed corpus version

authorized student
  -> creates or opens course conversation
  -> submits question with idempotency key
  -> question is persisted as pending
  -> scoped retrieval selects evidence
  -> pedagogical answer is generated and validated
  -> answer plus citations is persisted as completed
  -> client receives the validated result
```

## 3. Minimum domain model

Names may follow repository conventions, but these concepts and relationships must remain explicit.

| Entity | Minimum responsibility |
| --- | --- |
| `User` | External OIDC subject and role/membership identity. |
| `Course` | Authorization and retrieval isolation boundary; points to at most one active corpus version. |
| `CourseMembership` | Connects a user to a course with teacher or student capability. |
| `Document` | Stable logical document belonging to one course. |
| `DocumentVersion` | Immutable uploaded PDF identity, storage key, checksum, parse/index state and provenance. |
| `CorpusVersion` | Immutable set of successfully indexed document versions. Phase 0 may contain one document version. |
| `Chunk` | Course-, corpus-, document-version-, page- and fragment-scoped retrievable text. |
| `Conversation` | Belongs to one course and one student; owns ordered messages. |
| `Message` | Student question or assistant response with explicit processing state and idempotency identity. |
| `Citation` | Links a completed assistant message to a retrieved chunk and stores displayable provenance. |

Recommended state shapes:

```text
DocumentVersion: uploaded -> processing -> indexed | failed
Message: pending -> completed | failed
```

Use conditional updates or equivalent constraints so invalid transitions and double completion fail deterministically.

## 4. Minimum ports and first adapters

| Port | Phase 0 adapter | Later adapter or extension |
| --- | --- | --- |
| `IdentityProvider` | OIDC/JWT verifier plus deterministic test identity | Chosen hosted or self-managed OIDC provider |
| `ObjectStorage` | MinIO via S3-compatible API and presigned PUT | Any compatible production object store |
| `DocumentParser` | Digital-PDF text extraction with page provenance | Layout-aware parsing; OCR only by new decision |
| `Retriever` | PostgreSQL FTS, course and active-corpus scoped | pgvector dense retrieval, RRF, then measured reranking |
| `TutorModel` | Deterministic fake in tests; one configured provider in runtime | Gateway, fallback and routing if justified |
| `ResponseValidator` | Schema, citation, scope and abstention checks | Additional safety/pedagogical evaluators |
| `JobDispatcher` | Transactional PostgreSQL outbox and Redis Streams ingestion worker (Increment 2B, ADR 0001) | Measured worker scaling |
| `EventSink` | Structured logs | Distributed traces and product analytics |

The adapters are replaceable because the ports express domain needs, not vendor APIs.

## 5. API contract to prove

Exact payload fields may evolve, but the behavioral contract must be testable.

| Operation | Purpose | Critical guarantee |
| --- | --- | --- |
| `POST /api/v1/courses` | Create a course | Creator becomes authorized teacher. |
| `POST /api/v1/courses/{course_id}/documents/uploads` | Create document version and presigned PUT | Storage key is server-generated and course-scoped. |
| `POST /api/v1/courses/{course_id}/documents/{document_id}/versions/{version_id}/complete` | Confirm upload and dispatch processing | Checksum/type/size validated; repeat is idempotent. |
| `GET /api/v1/courses/{course_id}/documents/{document_id}/versions/{version_id}` | Observe processing state | Returns explicit indexed or failed outcome. |
| `POST /api/v1/courses/{course_id}/corpus/activate` | Activate indexed corpus | Conditional atomic pointer update; cannot activate failed/unindexed content. |
| `POST /api/v1/courses/{course_id}/conversations` | Create student conversation | Conversation is course-scoped and membership-checked. |
| `POST /api/v1/conversations/{conversation_id}/messages` | Ask a question | Persists question before RAG; requires idempotency key. |
| `GET /api/v1/conversations/{conversation_id}` | Read auditable history | Returns ordered statuses, answer and citation metadata. |

Per ADR 0001, Increment 3 uses SSE for bounded lifecycle events and a single validated, persisted final payload. Queries and history remain JSON. Reconnection reconciles with durable history; no unvalidated model tokens are streamed.

## 6. Tutor response contract

Use a typed internal structure similar to:

```json
{
  "mode": "hint|guided_question|explanation|abstain",
  "answer": "string",
  "citations": [
    {
      "chunk_id": "stable-id",
      "document_version_id": "stable-id",
      "page": 12,
      "fragment": "optional display label"
    }
  ],
  "grounded": true
}
```

Validation rules:

- every citation maps to a retrieved chunk from the same course and active corpus version;
- `grounded=true` requires at least one valid citation;
- unsupported claims or insufficient evidence produce `mode=abstain`;
- invalid provider output never becomes a completed assistant message;
- the client sees only the validated, persisted terminal result.

## 7. Implementation increments

Each increment must end with runnable tests and a visible vertical improvement.

### Increment 0 — repository and executable baseline

- [x] Establish monorepo layout without speculative packages.
- [x] Start web and API locally.
- [x] Start PostgreSQL and MinIO from reproducible local infrastructure.
- [x] Add configuration validation, health/readiness endpoints, migrations, linting and test commands.
- [x] Document one command sequence from clean checkout to green checks.

### Increment 1 — identity, course and isolation

- [x] Implement the identity port and test verifier.
- [x] Create course and membership schema/use cases.
- [x] Enforce server-derived course authorization.
- [x] Add positive and cross-course denial integration tests.

### Increment 2 — upload, process and activate

- [x] Generate a server-scoped presigned PUT.
- [x] Confirm object checksum, content type and configured size limit.
- [x] Parse a digital PDF with page provenance.
- [x] Persist chunks and a searchable PostgreSQL FTS representation.
- [x] Activate only an indexed immutable corpus version by conditional update.
- [x] Expose processing failure without losing diagnostic state.

### Increment 2B — durable asynchronous ingestion (ADR 0001)

- [ ] Commit ingestion jobs and outbox events atomically.
- [ ] Dispatch through Redis Streams and a separate worker process.
- [ ] Recover abandoned work with leases, fencing and idempotent results.
- [ ] Bound transient retries and persist terminal failures/dead letters.
- [ ] Reconcile unfinished jobs after transport loss; retain active corpus invariants.
- [ ] Prove outages, duplicates, scope validation and restart with isolated real dependencies.
- [ ] Document reproducible infrastructure and recovery operations.

### Increment 3 — durable conversation and RAG

- [ ] Create course-scoped conversations.
- [ ] Persist question as `pending` before retrieval/model invocation.
- [ ] Retrieve only from the active corpus and same course.
- [ ] Generate a typed pedagogical result through the model port.
- [ ] Validate answer, evidence and citations.
- [ ] Atomically persist completed answer/citations or mark failure.
- [ ] Make retries idempotent.
- [ ] Deliver SSE lifecycle events and only a validated, persisted final response.

### Increment 4 — minimal student/teacher UI and end-to-end proof

- [ ] Teacher can create a course, upload a PDF and observe indexing state.
- [ ] Student can ask and see pending/completed/failed state.
- [ ] Student sees citations with document, page and fragment provenance.
- [ ] Student sees a clear abstention when evidence is insufficient.
- [ ] Run the browser-level happy path against real API, PostgreSQL and MinIO.

## 8. Acceptance tests

The walking skeleton is done only when all statements are reproducibly true.

1. From a clean checkout, documented commands start dependencies, migrate the database, start the apps and run tests.
2. A teacher uploads a valid digital PDF without proxying its bytes through the API application.
3. Processing produces page-aware chunks and an indexed immutable version.
4. Only successfully indexed content can become the active course corpus.
5. A student's question exists in the database before the model adapter is called.
6. A supported question returns pedagogical guidance with at least one citation that resolves to an actually retrieved chunk.
7. An unsupported question abstains instead of presenting model memory as course evidence.
8. A forced model failure leaves the question and an explicit failed state available in history.
9. Repeating the same message request with the same idempotency key does not duplicate the question, model call, answer, or citations.
10. A user from course A cannot retrieve, cite or read course B content.
11. No raw unvalidated tokens are exposed to the client.
12. After process restart, database and object state remain usable and the test flow can continue.

## 9. Definition of done for every increment

- Behavior is reachable through the real application boundary, not only a unit test.
- Database changes have forward migrations and constraints.
- Failure paths use typed, safe errors and leave durable diagnosable state.
- Unit and integration tests are deterministic and green.
- Logs contain correlation and entity identifiers but no secrets or raw educational content.
- README/runbook commands work from a clean checkout.
- No item from the explicit out-of-scope list was introduced incidentally.

## 10. Deferred decisions, with seams only

- Worker scaling beyond the Redis Streams ingestion scope of Increment 2B.
- Dense embeddings, pgvector indexing, hybrid fusion/RRF and cross-encoder reranking.
- Minimal KITE-style intent routing and richer pedagogical state.
- Conversation-window summaries and summary versioning.
- OIDC provider selection and production deployment topology.
- Full immutable publication workflow, rollback and authoring UI.
- RAG evaluation datasets, RAGAS-like metrics and pedagogical pilot design.
- Model gateway, provider fallback, rate limits and cost controls.

Promote a deferred item only when the current skeleton is green and the next experiment or product risk requires it.
