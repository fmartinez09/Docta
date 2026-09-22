# Docta: architecture, decisions and next work

Reviewed 2026-09-22 against `d65ef8c` (documentation) and `8ee13a5` (latest application changes).
This is a source inspection, not a claim that local services or tests were run today.
Use [README](../README.md) for operation and [AGENTS](../AGENTS.md) for engineering rules.

## Current implementation

Phase 0, Increments 0–4 including 2B, is complete. Docta has a working course-scoped
PDF-to-tutor flow. Phase 1 measures and improves quality; Increment 5 is not implemented yet.

Docta is a modular monolith: Next.js web/BFF, FastAPI application and a separate ingestion
worker share one repository and domain model. PostgreSQL owns durable state, MinIO implements
the S3 storage boundary, and Redis Streams transports ingestion jobs. There is no dedicated
RAG service, conversation queue, pgvector installation or agent framework.

| Boundary | Implementation | Main evidence |
| --- | --- | --- |
| Identity and courses | Verified OIDC JWTs, local memberships, owner-private conversations. | [identity.py](../apps/api/src/docta_api/identity.py), [course tests](../tests/integration/test_courses.py) |
| Documents | Direct presigned upload, immutable object/version checksums, PyMuPDF extraction, page-aware character chunks. | [documents.py](../apps/api/src/docta_api/documents.py), [document tests](../tests/integration/test_documents.py) |
| Ingestion | Transactional outbox, Streams consumer, leases, fencing and bounded retries. | [worker.py](../apps/api/src/docta_api/worker.py), [job tests](../tests/integration/test_jobs.py) |
| Retrieval | Spanish FTS over the original question; AND semantics, `ts_rank_cd`, ordinal tie-break, top five chunks. | [postgres_retriever.py](../apps/api/src/docta_api/postgres_retriever.py) |
| Tutor | Typed request/draft, bounded history, HTTP model adapter, evidence/citation validator. | [rag.py](../apps/api/src/docta_api/rag.py), [tutor_http.py](../apps/api/src/docta_api/tutor_http.py) |
| Conversation | API-owned execution, durable deadline, atomic response/citations, SSE lifecycle events. | [runtime](../apps/api/src/docta_api/conversation_runtime.py), [conversation tests](../tests/integration/test_conversations.py) |
| Browser | OIDC/PKCE, encrypted HttpOnly session, publication, durable chat and citation fragments; assistant-ui/custom CSS with Radix and Lucide. | [workspace](../apps/web/components/workspace.tsx), [browser tests](../apps/web/e2e/workspace.spec.ts) |

### Ingestion and publication

Upload authorization → direct S3 PUT → verified confirmation and job/outbox transaction →
worker extraction/indexing → immutable READY corpus → explicit compare-and-swap activation.

The worker publishes/consumes outside database transactions. A current, unexpired fencing token
is required to commit results; a duplicate delivery does not duplicate publication. ACK/deletion
follows commit. Redis loss is reconciled from PostgreSQL, not by rebuilding completed artifacts.
Only one consumer group owns this queue. Leases are not automatically extended.

Only text-based digital PDFs are supported. Invalid, encrypted or image-only input fails
explicitly. Current corpora contain one document version; multiple uploaded PDFs do not form
an automatically merged corpus. Publication is not a full multiversion authoring/rollback UI.

### Conversation and grounding

```text
authorize + deduplicate + capture active corpus + persist pending question
  → retrieve and persist evidence
  → generate if evidence exists / abstain without a model otherwise
  → validate complete draft
  → atomically commit response and citations
  → deliver committed result through SSE/history
```

- `Message` aggregates the exact question and optional response: `pending → completed|failed`.
  One pending message is allowed per conversation. Same key/text returns the existing message;
  changed text with that key conflicts. Course creation alone permits an optional key for older
  API clients; the browser always provides one. Corpus activation uses compare-and-swap.
- The accepted question fixes its authorized corpus. Retrieval rechecks membership, owner,
  message, corpus, deadline and `Message.question == question`. Future rewritten queries must
  be separately versioned and bound to that message; simply removing the equality check is unsafe.
- `Retriever` already returns `Evidence`, not another generated answer. Evidence retains course,
  corpus, document version, title, checksum, chunk hash, text and page/fragment provenance.
  Historical citations remain auditable after withdrawal; referenced chunks cannot be deleted freely.
- Prompt `phase0-guidance-v2` receives up to six completed turns / 12,000 history characters.
  Retrieval version is `spanish-fts-and-top5-v1`. History does not resolve the retrieval query.
- Modes are `hint`, `guided_question`, `explanation`, `abstain`. Non-abstentions require evidence,
  a grounded flag and citations containing exact quotes from retrieved chunks. Provider abstentions
  are replaced with safe canonical text. These checks prove provenance, not semantic entailment.
- Strict course grounding is the only supported factual mode. No general-knowledge fallback,
  automatic query rewrite, second search, tool calling or candidate-repair loop is implemented.
- SSE emits `message.accepted`, progress and one committed terminal result while connected.
  Disconnecting stops delivery, not execution. Reads and repeated POSTs reconcile the same message;
  event IDs are for correlation, not a replay log. Unvalidated model tokens are never exposed.
- API loss does not automatically repeat an ambiguous model call. Expired pending messages fail
  through a five-second sweep or authenticated reads; late results cannot overwrite terminal state.
  Database outages delay reconciliation, not authorize fabricated success or provider retries.

The browser token stays in an encrypted HttpOnly cookie, never localStorage or client props.
Sessions expire within one hour or earlier token expiry; no refresh token is stored. Mutations
validate Origin at the BFF; FastAPI remains the authorization authority. Logout clears the Docta
session, not necessarily the identity provider session. PDFs/history/model output are untrusted data.

### Known limits

There is no quality dataset/runner, conversational query resolution, dense/hybrid retrieval,
structured pedagogical state, enrollment UI, teacher access to other users' private conversations,
integrated PDF viewer or feedback workflow. Discovery lists return the newest 100 entries;
message history is paginated. Unaccepted drafts do not survive reload. No pilot model, production
gateway, cost ledger, production deployment or demonstrated backup restoration is established by
the repository. An externally configured gateway may exist; Compose does not manage one.

## Accepted decisions and history

The identifiers below preserve the original ADR references. Their decisions remain in force;
consolidating documentation changes their location, not their technical meaning.

| Decision | Date / status | Rationale and consequence |
| --- | --- | --- |
| ADR 0001 — durable ingestion | 2026-09-05; implemented in 2B/3 | Replace inline ingestion with PostgreSQL outbox + Redis Streams and a worker. At-least-once delivery requires idempotency/fencing; SSE delivers only validated results. LiteLLM was a candidate, not a required deployment. |
| ADR 0002 — durable scoped conversation | 2026-09-07; implemented in 3 | Persist questions before inference, bind FTS/evidence to course and captured corpus, keep generation API-owned with deadlines. Use replaceable HTTP model port; no automatic provider retries. This avoids queue/retry ambiguity while accepting failure after an API crash. |
| ADR 0003 — browser workspace | 2026-09-07; implemented in 4 | BFF/PKCE and encrypted cookie preserve API authorization. Discovery/publication/chat use existing contracts; `llama_cpp`/`unsloth` profiles refine model compatibility without weakening domain validation. |
| ADR 0004 — evaluation first | 2026-09-09; accepted plan, implementation pending | Phase 0 closes as technical proof. Measure 30–50 reviewed cases before optimization; hybrid retrieval is experimental, not mandatory. Retain strict grounding, PyMuPDF and current UI. Agent loops/general-knowledge fallback are not approved. |

Phase 0 merged at `e31d1aa`. Historical records include 110 Python tests on September 7 and a
later September 8 prompt-v2 check reporting 118 Python tests, ten web tests, Ruff and ESLint.
Qwen3.5-9B compatibility checks reported one 5.6-second synthetic response and two prompt-v2
replays at 9.8/8.4 seconds. These are separate small checks, not a benchmark or learning result.
UI commit `8ee13a5` updates presentation, not retrieval/runtime. None of these tests was rerun
by this documentation consolidation.

On 2026-09-22 the owner requested three English documents instead of duplicated architecture,
state, roadmap, ADR and runbook files. This replaces their document-location requirements only.
Full original decisions, checklists, imported proposals, bibliographies and dated test records
remain in Git at `d65ef8c32196a0b4e5ab824072305aa9f68c356b`. Read without changing the checkout:

```powershell
git ls-tree -r --name-only d65ef8c -- docs
git show d65ef8c:docs/adr/0002-durable-conversation-and-scoped-rag.md
git show d65ef8c:docs/WALKING_SKELETON.md
```

Record future material decisions here with date, status, rationale, affected contract and
acceptance/reversal conditions. Do not silently overwrite an accepted decision or label a
proposal implemented. Stop executors and preserve database/object state before a rollback;
destructive migration downgrades belong only in isolated tests.

## Next: Increment 5 — evaluation harness

Build an offline runner and reviewed dataset without changing the production prompt, retrieval,
model or policy. Reuse real ingestion and retrieval ports with authorized synthetic messages on
isolated PostgreSQL/MinIO/Redis resources. Do not bypass scope checks with copied SQL or reset
development state. A small `evals/` implementation is enough; no hosted platform or new service.

**Dataset:** 30–50 Spanish cases covering concepts, exercises, incorrect attempts, follow-ups,
ellipsis, topic changes, ambiguity, absent evidence, inactive corpora and cross-course distractors.
Each case needs an ID/schema version, category/difficulty/split, permitted corpus manifest with
checksums/pipeline version, exact question and minimal authorized history, expected answerability,
relevant/prohibited evidence and acceptable/prohibited pedagogical help. Include expected standalone
interpretation where relevant, author/reviewer, review status, rubric version and usage provenance.

Use portable document/page/fragment/hash labels, not generated database UUIDs. Resolve labels to
the actual run and reject missing/ambiguous references. Generated drafts require human review to
count as gold. Reserve held-out families from v0; never tune against them or derive gold from the
retriever's own output. Private student histories are not automatically evaluation material.

**Execution:** without a provider, measure real retrieval and deterministic contracts only.
An explicit model mode adds request/time/spending limits and reviewable generation artifacts;
never call a paid provider implicitly from CI. Store commit, dataset/corpus/pipeline/prompt/model
versions, configuration, per-case results, category aggregates and limitations. Keep educational
artifacts outside operational logs, with access/retention rules. Missing metrics are `not_measured`.

| Measurement | Interpretation |
| --- | --- |
| Recall@K, hit rate, MRR@K, nDCG@K; include K=5 | Known relevant evidence/ranking; explicit grading, denominators and exclusions. |
| False abstention / false answer | Abstentions among answerable cases / factual answers among unanswerable cases. Technical failures are separate. |
| Grounding and pedagogy | Automated citation integrity plus human review of claim support, correctness, help level and premature solutions. |
| Operation | Per-stage/turn duration, failures and available token/cost data. Fake latency is not model latency. |

Classify failures as query resolution, retrieval miss, false abstention/answer, invalid citation,
unsupported grounding, pedagogy mismatch or technical failure; allow multiple causes and
`UNDETERMINED`. Empty retrieval alone does not measure complete tutor answerability. Freeze
comparison gates before experiments; a weak baseline is acceptable if measured honestly.

Acceptance criteria, all pending:

- [ ] Schema rejects incomplete/duplicate cases and invalid/ambiguous evidence references.
- [ ] 30–50 reviewed cases include reproducible manifests, provenance, rubric and protected splits.
- [ ] Clean-checkout runner uses isolated real resources; rebuilt IDs do not invalidate labels.
- [ ] FTS baseline is reproducible; metrics pass known-ranking tests and results remain per case.
- [ ] Isolation tests exclude course-B and inactive-corpus evidence from retrieval/citations.
- [ ] Reports separate fake contracts, model/human quality, unmeasured data and technical failures.
- [ ] Conversational failures are diagnosed without inventing review scores or claiming smoke-test quality.
- [ ] README documents the implemented command, versions, isolation/cleanup and opt-in model mode.
- [ ] Narrow tests and full repository checks pass; implementation status and results are recorded here.

Material permissions and a reviewer must be agreed before closing the dataset criterion. Runner
and draft fixtures can progress meanwhile. No live model call is required by normal CI, but
generation quality, model selection and pilot readiness remain open until evaluated.

## Later work and open choices

These are gated plans, not authorization to implement everything. Preserve increment numbers
for history; change order explicitly when evidence warrants it. A rejected candidate can close
an experiment without becoming a dependency.

| Increment | Planned result and decision gate |
| --- | --- |
| 6 — query resolution | After 5, version a derived standalone query bound to the original message/scope. Refine ADR 0002; test history manipulation and corpus changes without rewriting idempotency. |
| 7 — retrieval comparison | After 5–6, compare FTS, dense and FTS+dense+RRF with other factors fixed. pgvector needs an explicit decision, migrations/rebuild and immutable index versions. Start with exact search; retain FTS if gates fail. |
| 8 — pedagogy | After 5–6 and a fixed retrieval baseline from 7, define learner observations → pedagogical decision → retrieval intent. Separate observed errors from hypotheses; agree a small activity/policy and reviewed rubric. |
| 9 — conditioned retrieval | After 8, expand to 80–120 reviewed cases; compare question vs history vs attempt/pedagogical purpose using the same retriever and query convention. Require better evidence/help, not just fluency. |
| 10 — answerability | After 8–9, version answer/clarify/retrieve-again/abstain separately from durable message states. At most one additional search under the same scope/deadline; no autonomous loop or ambiguous provider retry. |
| 11 — model benchmark | With stable evidence/contracts, compare generation/planning quality, schema failures, latency, cost and privacy; evaluate held-out cases. No model or gateway is selected by a smoke test. |
| 12–13 — learning and feedback | After 10–11, review next-attempt help and distinguish simulation from human learning. Obtain consent/privacy rules before feedback; review/deidentify promoted cases and protect splits. |
| 14 — optional presentation | Consider OpenUI only if stable pedagogical contracts expose a measured UI need; allowlisted components, safe fallback, no generated executable code. |
| 15 — pilot | Reviewed material/model, real identity/model E2E, privacy/retention, support, quotas/cost limits, TLS, threat review and proven backup restore with recovery objectives. Does not require vectors or OpenUI. |

### Evaluation harness versus tutor runtime

The evaluation harness measures executions offline. The production runtime conducts a turn.
The existing `ConversationRuntime`, `Retriever`, `TutorModel` and validator already separate
retrieval from generation. A custom harness means owning these contracts and policies, not
avoiding every library. Pydantic validation is not adoption of Pydantic AI.

The imported proposal suggests activity/policy state and a bounded model → tool → observation
loop, with code controlling authorization, budgets and publication. That is a real alternative
to the current flow, but conflicts with ADR 0004's no-loop decision and is not accepted.
LangGraph, LlamaIndex, Pydantic AI and Agents SDK remain options, not selected dependencies.

If proposed for implementation, first decide permitted tools, derived-query binding, mandatory
evidence, budgets across retries, provider tool-call compatibility, durable steps/cancellation,
and a comparison against the fixed flow. Preserve existing atomic commits throughout migration.
Checkpoints alone do not schedule recovery or share a transaction with domain state. Tool-result
types must distinguish empty, unavailable and forbidden without weakening scope/provenance.

No new search can mean reused authorized evidence or a nonfactual coordination turn; it must not
silently mean an unsupported factual answer. Both require a response contract beyond today's
mandatory citations for non-abstentions. General-knowledge fallback needs a separate product decision.

Improve measured retrieval without waiting for an agent loop. A linear flow can retrieve well;
a tool loop can retrieve badly. Keep search quality, action policy and pedagogy as separate
experiments. Store activity facts/attempts/help independently from inferred learning; do not
treat an assertion of understanding as mastery. Verifiers, rerankers, parent-child chunking,
durable graph execution and practice banks remain deferred until a concrete need is demonstrated.

The papers discussed motivate experiments, not a framework mandate: KITE's simulated students
do not establish human learning, and adding a search tool does not reproduce Self-RAG training.
Human transfer/retention claims need an approved study. The full prior bibliography is in Git;
this consolidation does not claim to have revalidated its external sources.
