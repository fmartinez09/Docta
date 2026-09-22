# Docta engineering rules

Build a trustworthy pedagogical RAG tutor. Phase 0 is complete; the next slice is Increment 5:
an offline evaluation harness and reviewed dataset. Optimize for evidence, isolation and visible
failures, not framework adoption or feature breadth.

## Documentation and scope

Maintain only three project Markdown documents, in English:

- [README.md](README.md): setup, operation, troubleshooting and checks.
- This file: engineering constraints and working agreements.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): implemented state, accepted decisions, next-slice
  acceptance criteria and open choices. Its decision ledger preserves ADR 0001–0004 identifiers.

The user task defines the bounded work; a roadmap entry does not authorize all future increments.
These rules constrain implementation, accepted decisions constrain contracts, and the architecture's
next-slice criteria define completion. Proposals are not decisions. Report conflicts and choose the
least expansive behavior until resolved. Record material changes as dated decisions in the ledger,
including rationale, affected contracts, gates and reversal; do not create a separate ADR file by default.
Original documents and dated verification records remain recoverable at commit `d65ef8c`.

## Working style

- Inspect code and Git state first; preserve user changes. State the intended vertical behavior,
  affected modules, tests and assumptions before implementation. Keep increments reviewable.
- Prefer explicit code and typed boundaries. Do not add services, dependencies, stores, queues,
  empty packages or speculative abstractions without a current acceptance requirement.
- Preserve the modular monolith: Next.js/TypeScript web, FastAPI/Python application, PostgreSQL,
  S3-compatible storage and separate ingestion worker. Keep retrieval/pedagogy out of HTTP handlers.
- Never hide incomplete production behavior with mocks. Fakes belong at explicit ports in tests.
- Add/update tests for behavior changes, run narrow tests first, then full checks from README.
  For docs-only work, check local links/paths, status consistency and `git diff --check` instead.
- Update implementation claims only with evidence. Keep historical results dated; do not imply
  they ran again. Report files, commands/results, limitations and the next smallest step at handoff.
- Keep documentation concise: one authoritative location per topic. Git holds lengthy historical
  proposals and transcripts; do not copy them back as active specifications.

## Non-negotiable guarantees

1. **Authorization:** every document/version/chunk, conversation/message, retrieval, citation and
   job belongs to a course. Scope comes from authorized server context, not a client/model filter.
   Fail closed on absent/inconsistent scope. Verify OIDC claims and current membership; conversations
   remain owner-private. A teacher role does not grant access to another user's conversation.
2. **Documents:** support digital text PDFs only; explicitly reject invalid/unsupported/image-only
   input. Keep versions immutable and page/fragment/hash provenance. Activate only indexed corpora
   using conditional publication. Capture the authorized active corpus when accepting each question.
   Every search in that turn uses that snapshot; publication changes must not alter it.
3. **Durability:** persist the exact original question before retrieval/model calls. Preserve
   idempotency and `pending → completed|failed` transitions; commit validated response/citations
   atomically. Fence expired/terminal completions. Failures retain safe classifications, not raw
   provider data. Keep full history while bounding prompt context; never silently repeat an ambiguous
   provider call. Future derived queries need separate, versioned message/scope binding.
4. **Grounding:** factual guidance requires course evidence. Abstain when insufficient; no general
   knowledge fallback. Cite only actual retrieved evidence used for the answer. Validate the full
   response before exposure; never stream unvalidated tokens. Prefer appropriate hints/guidance
   over premature solutions. Literal citations do not prove semantic support or learning.
5. **Boundaries:** preserve IdentityProvider, ObjectStorage, JobDispatcher, Retriever, TutorModel
   and ResponseValidator seams. PostgreSQL owns ingestion jobs/outbox; Redis Streams transports them
   with leases/fencing/idempotency, not Pub/Sub. Conversation generation remains API-owned and
   separate from ingestion. Preserve BFF/PKCE/encrypted HttpOnly sessions; API owns authorization.
6. **API and privacy:** use `/api/v1`, stable generated IDs, UTC, explicit states, safe error codes
   and correlation IDs. Require idempotency for retryable writes; preserve optional course-creation
   keys for older clients and compare-and-swap activation. Do not expose storage keys as domain IDs.
   Never log PDF text, prompts, answers, private reasoning, credentials, presigned URLs or raw provider
   payloads. Trace operations, duration/outcome and relevant request/course/conversation/message/version IDs.

## Verification and evaluation

Preserve tests for the end-to-end path, cross-course denial, invalid PDF rejection, abstention,
citation integrity, question persistence before model failure, message transitions, idempotent
retries, activation only after indexing, and restart/rebuild from durable state.

Use real PostgreSQL/MinIO where semantics matter and deterministic provider fakes for normal tests.
Run integration suites serially: some restart shared test services. Evaluation uses isolated
database/bucket/stream resources and reviewed synthetic or explicitly authorized material. Never
reset development data or export private histories by default. Record provenance, review state,
splits and retention; keep educational artifacts outside operational logs.

Before comparing quality changes, fix a versioned baseline, reviewed cases and acceptance gates.
Separate retrieval, answerability, grounding, pedagogy and operational metrics. Fakes prove contracts,
not model quality; simulated students do not prove human learning. Report missing measures explicitly.

## Not authorized by the current plan

The evaluation harness is not a production agent runtime. Query resolution, experimental hybrid
retrieval and typed pedagogy are later gated work. pgvector/RRF are not installed or mandatory.
The later second retrieval is bounded, not permission for provider retries or autonomous loops.

Tool/repair loops, general-knowledge fallback, new framework/runtime adoption, graph or multiagent
execution require an explicit decision. Also out of scope: OCR/multimodal processing, LMS/mobile,
microservices/Kubernetes/Kafka/Elasticsearch, extra Redis infrastructure, permanent rerankers or
advanced routing, dashboards/online experiments, unvalidated streaming, full publishing/rollback
UI and multi-version authoring. OpenUI and verifiers are optional proposals, not prerequisites.

For such a request, identify the scope change and propose the smallest compatible contract/decision;
do not implement it incidentally while working on an unrelated slice.
