# Docta: architecture, decisions and next work

Reviewed 2026-09-29 against the current development orchestration and `8ee13a5` application state.
Verification results below retain their original dates; this review is not a claim that all
historical commands were run again.
Use [README](../README.md) for operation and [AGENTS](../AGENTS.md) for engineering rules.

## Current implementation

Phase 0, Increments 0–4 including 2B, is complete. Docta has a working course-scoped
PDF-to-tutor flow with an implemented Socratic tutor prototype for Spanish humanities, featuring
assistant-ui and OpenUI Lang / Renderer. Phase 1 measures and improves quality; Increment 5 is not
implemented yet. Increment 7 (dense retrieval) is developed in parallel; FTS remains the default.

Docta is a modular monolith: Next.js web/BFF, FastAPI application and a separate ingestion
worker share one repository and domain model. PostgreSQL owns durable state, RustFS implements
the S3 storage boundary, and Redis Streams transports ingestion jobs. There is no dedicated
RAG service, conversation queue or separate agent framework. The local PostgreSQL image includes
pgvector and the `chunk_embeddings` table; dense retrieval is opt-in and FTS is the default.

| Boundary | Implementation | Main evidence |
| --- | --- | --- |
| Identity and courses | Verified OIDC JWTs, local memberships, owner-private conversations. | [identity.py](../apps/api/src/docta_api/identity.py), [course tests](../tests/integration/test_courses.py) |
| Documents | Direct presigned upload, immutable object/version checksums, PyMuPDF extraction, page-aware character chunks. | [documents.py](../apps/api/src/docta_api/documents.py), [document tests](../tests/integration/test_documents.py) |
| Ingestion | Transactional outbox, Streams consumer, leases, fencing and bounded retries. | [worker.py](../apps/api/src/docta_api/worker.py), [job tests](../tests/integration/test_jobs.py) |
| Retrieval | Spanish FTS over the original question or message-bound retrieval query; OR match over its stemmed lexemes with a coverage floor (at least ceil(2n/3) of the n query lexemes must be in the chunk), ordered by coverage, `ts_rank_cd` and ordinal, top five chunks. Authorization is shared in `retrieval_auth.py` and the retriever is selected by `retriever_factory.py` (`fts` only); the retriever reports the version stored per message (D-07-001, D-07-020..022). | [postgres_retriever.py](../apps/api/src/docta_api/postgres_retriever.py), [retrieval_auth.py](../apps/api/src/docta_api/retrieval_auth.py), [retriever_factory.py](../apps/api/src/docta_api/retriever_factory.py), [version test](../tests/integration/test_retrieval_version.py) |
| Tutor | Socratic humanities tutor (`socratic-tutor-v4`, `tutor-intervention-v2`; reads v1), 9 situations, structured blocks (`question`, `hint`, `explanation`, `diagram`, `activity`, `sources`), bounded history, evidence/citation validator, deterministic equivalent text. | [rag.py](../apps/api/src/docta_api/rag.py), [tutor_http.py](../apps/api/src/docta_api/tutor_http.py), [tutor_contract.py](../apps/api/src/docta_api/tutor_contract.py) |
| Conversation | API-owned execution, durable deadline, atomic response/citations/intervention, continuity resolution (questions and actions: `another_hint`, `explain`, `view_source`), SSE lifecycle events. | [runtime](../apps/api/src/docta_api/conversation_runtime.py), [conversations](../apps/api/src/docta_api/conversations.py), [conversation tests](../tests/integration/test_conversations.py) |
| Browser | OIDC/PKCE, encrypted HttpOnly session, publication, durable chat via assistant-ui, OpenUI Lang adapter + Renderer with custom educational components, fallback to accessible equivalent text on render error, and rich-presentation switch. | [workspace](../apps/web/components/workspace.tsx), [tutor-panel](../apps/web/components/tutor-panel.tsx), [rich-response](../apps/web/components/rich-response.tsx), [openui](../apps/web/lib/openui/) |

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
authorize + deduplicate + capture active corpus + persist pending question/action
  → resolve continuity query or detect ambiguous antecedent (clarify immediately without model/retrieval)
  → retrieve and persist evidence for authorized bound query (coalesce(retrieval_query, question))
  → generate structured intervention if evidence exists / abstain without a model otherwise
  → validate complete intervention, mode, groundedness, and exact citations
  → atomically commit response, citations, and intervention metadata
  → deliver committed result through SSE/history
  → frontend adapts intervention to OpenUI Lang and renders via OpenUI Renderer (or fallback)
```

- `Message` aggregates the exact question and optional response: `pending → completed|failed`.
  One pending message is allowed per conversation. Same key/text returns the existing message;
  changed text with that key conflicts. Course creation alone permits an optional key for older
  API clients; the browser always provides one. Corpus activation uses compare-and-swap.
- The accepted question/action fixes its authorized corpus. Retrieval rechecks membership, owner,
  message, corpus, deadline, and binds the query via
  `func.coalesce(Message.retrieval_query, Message.question) == query`. Bounded follow-ups
  (\"no sé\", \"otra pista\", \"explícame eso\") and action turns (`another_hint`, `explain`, `view_source`)
  safely resolve their antecedent from stored history at acceptance without a second model call.
  Ambiguous antecedents immediately trigger an uncited clarification turn without retrieval or model calls.
- `Retriever` returns `Evidence`, not another generated answer. Evidence retains course,
  corpus, document version, title, checksum, chunk hash, text and page/fragment provenance.
  Historical citations remain auditable after withdrawal; referenced chunks cannot be deleted freely.
- Prompt `socratic-tutor-v4` implements 9 observable pedagogical situations for Spanish humanities,
  receiving up to six completed turns / 12,000 history characters with situation and help level.
  Retrieval version is `spanish-fts-coverage-top5-v2`. Single primary question per turn.
- Contract `tutor-intervention-v2` structures the response into blocks (`QuestionBlock`, `HintBlock`,
  `ExplanationBlock`, `DiagramBlock`, `ActivityBlock`, `SourcesBlock`), while reading stored v1.
  Non-abstentions require evidence,
  a grounded flag and citations containing exact quotes from retrieved chunks. Provider abstentions
  are replaced with safe canonical text.
- Frontend renders the intervention with `@assistant-ui/react` and `@openuidev/react-lang` Renderer
  using a custom component library (`TutorResponse`, `GuidedQuestion`, `Hint`, `Explanation`,
  `Diagram`, `Activity`, `Sources`, `SourceCard`). If rendering encounters an error, it falls back to the
  deterministic equivalent text and citations. A rich presentation switch allows toggling between
  rich OpenUI rendering and accessible text-only presentation without losing history or citations.
- Strict course grounding is the only supported factual mode. No general-knowledge fallback,
  automatic model query rewrite, second search, tool calling or candidate-repair loop is implemented.
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

There is no quality dataset/runner, conversational query resolution, active dense or hybrid retrieval,
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
| Interactive tutor presentation | 2026-10-29; implemented | The owner prioritized direct explanations and optional visual sequences and single-choice practice ahead of the evaluation plan. `socratic-tutor-v2` asks for evidence-based guidance; `tutor-intervention-v2` adds a bounded activity while retaining v1 reads. The browser checks answers and reveals hints locally; selections are ephemeral, and the correct answer is delivered to the browser, so this is practice, not secure assessment. Structured and literal citation validation do not prove semantic support or teaching quality. Gates: contract, citation and browser interaction tests and existing full checks; real-provider quality remains unmeasured. Reversal: stop emitting v2 and retain v2 read support for already stored responses. |
| Provider block schema | 2026-10-29; implemented | A real malformed response exposed that the flattened sampling schema required only a block type. Standard and llama.cpp profiles now request per-block required fields using `anyOf`; Gemini uses separate typed lists to avoid previously rejected unions and nested array bounds. The API derives tags and version from those lists, converts them in a fixed order and validates the full intervention and citations. Incomplete activities are rejected rather than fabricated or retried. Adapter tests cover malformed output and both schema profiles. On 2026-10-29 one synthetic Gemini request returned an explanation, diagram, activity and sources that passed local validation; this does not establish model quality or provider reliability. Reversal: restore the previous sampling schema if an endpoint rejects its replacement, while retaining fail-closed validation. |
| Conversation presentation refinement | 2026-10-29; implemented | The owner requested a more natural, progressive tutor experience. Prompt v4 requests direct explanations and reserves practice activities for a learner request or a relevant attempt. The browser displays validated new blocks with a brief staggered animation and provides expandable per-answer citations; reduced-motion preferences disable animation. This is presentation after validation, not streaming generation, and does not change the evidence contract. Gate: prompt, renderer, browser and full checks; visual and pedagogical quality with real learners remains unmeasured. Reversal: restore the prior prompt and presentation while retaining stored interventions. |
| Gemini 3.8 trial | 2026-10-29; not adopted | The owner authorized a model change and test. With the existing gateway and synthetic evidence, `gemini-3.8-flash` timed out at 45 and 119 seconds; a separate minimal request also timed out after 30 seconds. `gemini-3.1-flash-lite` produced one valid response and a later unavailable result. These observations do not identify whether the gateway or upstream provider was responsible. The existing profile was retained. Recheck gateway routing and availability before switching; no model quality comparison was established. |
| Socratic tutor prototype with OpenUI Lang and assistant-ui | 2026-10-29; implemented | The owner prioritized a simple Socratic tutor for Spanish-language humanities with assistant-ui plus OpenUI Lang/Renderer, postponing pedagogical review and the Increment 5 harness. This is a bounded exception to the "typed pedagogy" and "query resolution" limits in `AGENTS.md`: the tutor is the existing runtime's educational behavior, not a new agent runtime; follow-ups such as "no sé" and actions ("Otra pista", "Explicar", "Ver fuente") are resolved from bounded stored context without a second model call, and derived queries are bound to the originating message and authorized scope (`coalesce(Message.retrieval_query, Message.question) == query`); retriever and corpus capture are unchanged. Schema & storage: migration `20261029_0010_tutor_interventions` adds `kind`, `intent`, `parent_message_id`, `retrieval_query`, `query_version`, `intervention`, `contract_version`, `situation`, and `help_level`, with constraints `ck_messages_kind`, `ck_messages_intent`, `ck_messages_parent`, and updated `ck_messages_mode`. API: `POST /conversations/{id}/actions` supports action turns; `conversations.py` `accept()` resolves antecedents; `conversation_runtime.py` emits immediate uncited clarification if antecedent is ambiguous; `tutor_http.py` prompt implements the 9-situation table with single question per turn. Contract `tutor-intervention-v1` (Python and TypeScript): 9 situations, blocks `question`/`hint`/`explanation`/`diagram`/`sources`, one question per turn, deterministic equivalent text and allowed actions. Frontend: assistant-ui runtime integrated with OpenUI Lang and Renderer (`@openuidev/react-lang`); custom library (`TutorResponse`, `GuidedQuestion`, `Hint`, `Explanation`, `Diagram`, `Sources`, `SourceCard`); `RenderBoundary` cleanly falls back to equivalent text and citations on renderer failure; rich presentation switch allows toggling between rich OpenUI and text-only presentation; double-click protection via disabled state. Evidence (2026-10-29): full check passed cleanly with 394 pytest tests (293 unit, 101 integration/browser including conversations, actions, and follow-ups), 41 Vitest tests, Next.js build, Ruff, ESLint, Docker worker/proxy builds, and git diff check. Reversal: revert migration `20261029_0010` and the tutor/OpenUI modules; stored legacy messages retain text answer and mode. |
| Managed embedding setup and IAM port diagnostics | 2026-10-29; implemented | The development wizard configures embedding endpoint, model, secret and embedding parameters independently of the tutor, without provider calls or implicit root `.env` import. FTS remains the default; dense mode still requires a calibrated threshold and compatible deadlines. A Docker port-bind failure produces a safe actionable diagnostic, and the default IAM port is excluded from VS Code auto-forwarding on container rebuild. Evidence (2026-10-29): managed setup succeeded after removing an existing VS Code port forward; the full check passed 369 tests, Ruff and web checks with an isolated Docker CLI configuration. No real embedding provider was called. Reverse the wizard and VS Code setting without changing storage or identity; embeddings already stored remain available. |
| Explicit development retrieval selection | 2026-10-29; implemented | The owner requested using dense retrieval in the managed profile. The wizard now offers explicit FTS/dense selection and requests a compatible tutor timeout when the embedding plus tutor timeouts exceed the message deadline. FTS remains the default for new profiles; existing corpora without matching embeddings still fail closed and must be re-imported. Evidence (2026-10-29): the full check completed successfully with 371 tests collected, Ruff and web checks; the managed profile reports dense after explicit selection. No real embedding provider was called; model-quality and threshold-calibration gates remain open. Reverse by choosing FTS in the wizard. |
| RustFS local storage migration | 2026-10-28; implemented locally; VM deployment pending | The owner requested replacing MinIO and explicitly waived migration of existing documents. Development and isolated tests use RustFS 1.0.4 pinned by digest and a new volume. S3 version IDs, metadata, scoped documents, and immutable reads remain required. Managed profiles accept legacy storage credentials and ports without rewriting them; old volumes remain untouched. `DOCTA_STORAGE_HEALTH_URL` replaces the MinIO-specific setting, and optional `DOCTA_S3_PUBLIC_ENDPOINT_URL` signs browser uploads for a separate HTTPS endpoint in production. Setup can generate a new private development profile without prompts. The browser test now waits for committed acceptance before reloading: its optimistic pending indicator could appear before the request was accepted. A fresh database still applies the existing Alembic chain; the empty initial revision remains a chain anchor. Evidence: on 2026-10-28, 354 serial pytest cases passed against isolated PostgreSQL/Redis/RustFS, including browser flow and restart. On 2026-10-29, the full check collected 363 tests and completed successfully, including browser, Ruff and web checks; managed setup also succeeded after a Docker restart. The worker build now uses a restricted temporary context because Docker could not inspect an unrelated Windows-mounted `.pytest_cache`. Reversal: restore the previous Compose and configuration with matching original volumes; RustFS data is not automatically imported back. Remote VM deployment, TLS and backup/restore remain unverified. |
| Single-VM deployment path | 2026-10-28; implemented, external validation pending | The owner requested repeatable deployment on a VM. A separate Compose project builds API, worker, web and PostgreSQL, runs Alembic before application services, and uses RustFS plus an HTTPS Caddy 2.11.7 proxy; only the proxy publishes ports. The external HTTPS OIDC issuer/client and two public DNS names remain operator inputs. `scripts/init_vm.py` generates an owner-only profile once and rejects mismatched or incomplete existing profiles. An internal S3 endpoint serves the API/worker, and a separate public endpoint signs browser uploads. Proxy header capitalization requires case-insensitive S3 metadata names; conflicting duplicates fail closed. Percent-encoded database credentials are escaped for Alembic's configuration parser. Proxy errors omit request and error fields to prevent logging signed URLs or callback data. Evidence: isolated local VM composition started and reran successfully with synthetic identity values; API/web health passed; local self-signed HTTPS proxy preserved a presigned upload, hash metadata and versioned read; generated Compose config, permissions and percent-encoded database credentials were tested. On 2026-10-29 a real proxy error test confirmed request URI and header markers were absent from logs; a first-image build and startup succeeded from isolated source contexts. Public DNS/ACME, real OIDC login, EC2 and backup/restore are not verified; do not treat the smoke test as production acceptance. Reverse by stopping the VM Compose project without deleting volumes and returning to the previous deployment configuration. |
| Web dependency security update | 2026-10-28; implemented | The requested public VM path exposed critical advisories in the existing Next.js dependency. Upgrade Next from 16.3.4 to 16.4.3 and refresh compatible lockfile dependencies; build the VM image with development dependencies pruned. `npm audit --omit=dev` reported zero known production dependency vulnerabilities on 2026-10-28. Development-only Vitest dependencies still have audit findings; do not expose their tooling as a service. Gates: web lint/tests/build and browser integration. Reversal: restore the previous dependency lock only with an equivalent mitigation before public exposure. |
| Tutor timeout envelope | 2026-09-26; implemented | Permit provider timeouts up to 179 seconds for the current slow local model while retaining a strictly larger message deadline. Configuration tests gate `175/180` and reject equality; reverse by restoring the 120-second tutor bound if the local model is replaced or latency is reduced. |
| Case-only citation repair | 2026-09-26; implemented | A local model returned a valid supporting quote with only its initial letter's case changed, causing a technical failure. The validator now replaces a case-only quote with the unique exact span from the cited retrieved chunk before persistence; absent or ambiguous spans still fail. Unit tests gate exact output and rejection. Revert the repair if reviewed evaluation shows that even this narrow normalization masks unsupported citations. |
| Repeated citation collapse | 2026-09-26; implemented | The local model cited the same retrieved chunk twice for distinct valid phrases, conflicting with the one-citation-per-chunk persistence contract. Validate every quote against its bound chunk, then retain the first exact quote per chunk in model order. An invalid duplicate still fails the turn. Unit and isolated persistence tests gate the contract; revert if reviewed evaluation shows that retaining only the first quote hides material support. |
| WSL2 development bootstrap | 2026-09-29; implemented | The owner requested a ready development container, independently of Increment 5. Provide Linux Python/uv/Node/npm/Docker tooling and isolated local ZITADEL/Login V2 plus the existing Docta services. Host networking keeps browser/server loopback issuer, PKCE callbacks and signed S3 URLs consistent. Generated credentials/IDs live in ignored devcontainer files; preserve root environments and durable volumes. Version-pinned Management v1 provisions a public PKCE/JWT client, with saved ambiguous-create outcomes and no automatic mutation retry. Gates: preservation/rerun/failure tests, real IAM/JWT/BFF checks and repository checks. Reverse by stopping the dev Compose projects without deleting volumes and using native setup; no production identity or tutor contract changes. |
| Development state migration and diagnostics | 2026-09-29; implemented | Reopening a Windows checkout from WSL reused the same Docker volumes with newly generated credentials. Keep one managed environment per Docker host and transfer its private environment/identity state when moving the checkout. Classify IAM database authentication failures without exposing service logs. Checks supply dedicated test endpoints for import-time settings even without a root environment. Gates: safe diagnostic tests and serial checks from the WSL checkout; recovery preserves volumes and identity. Reverse the diagnostic/check changes independently; existing volumes still require their original credentials. |
| Development OIDC Web application | 2026-09-29; implemented | The Next.js BFF handles the code exchange on the server, so register the managed public PKCE application as Web instead of User Agent. Reconcile existing applications in place, retaining resource/Client IDs, exact callbacks, no client secret, JWT access tokens and local Development Mode. Gates: creation/migration/idempotency tests and real Management API confirmation. Reverse only the app type if compatibility requires it; no BFF/API authorization changes. |
| Missing development configuration guard | 2026-09-29; implemented | Rebuilding after a lost WSL mount retained durable Docker volumes while a missing ignored environment generated incompatible secrets. Before CLI environment creation, check the five known development data volumes; absent configuration with existing data fails before generating secrets or changing services. Unavailable Docker also fails closed; dependency caches alone permit first setup. Recovery keeps current tutor settings and restores original infrastructure credentials/identity. Gates: fresh/existing/unavailable volume tests and real bootstrap recovery. Reverse only this guard if environment ownership changes; do not reset data as a recovery step. |
| Developer-owned configuration | 2026-09-29; implemented | Replace implicit root-environment copying and personal template values with an explicit terminal assistant. Fresh profiles allow developer credentials or unique generated accounts, coordinated loopback ports, and an explicitly selected endpoint/model/key with validated provider/schema/budgets. Hooks install tools first and reconcile only existing profiles. Existing profiles preserve infrastructure credentials/identity, with tutor-only reconfiguration and private backups; recovery imports original managed files without overwriting. Managed API/token/web processes ignore root environments. README owns the account/secret-location inventory; secrets remain ignored private files. Gates: fresh/reuse/recovery/hidden-input/isolation tests and serial repository checks. Reverse by restoring the private profile backup and native setup; no production authorization or tutor request/validation changes. |
| Shared development lifecycle | 2026-09-29; implemented | Treat ZITADEL as the local IAM provider and separate first-time setup from daily execution. `dev:setup` is the only path that provisions/reconciles IAM, starts infrastructure and applies migrations; `dev:all` starts existing containers after public OIDC and Alembic-head checks and never uses the admin PAT or performs migrations. A named `docta-dev-state` volume stores the managed environment and identity state across linked worktrees; an inherited file lock permits one active application checkout. Runtime processes receive only their component configuration. Reverse by stopping managed projects and restoring the previous private profile; no application authorization or OIDC contract changes. |
| Development identity after volume deletion | 2026-09-29; implemented | Deleting IAM and Docta data volumes retained private files with obsolete project/application IDs, stopping bootstrap before Docta infra. Bind saved IDs to the authenticated IAM Instance ID; a confirmed instance change may archive/reconcile local identity only when all Docta data volumes are absent. Legacy state requires explicit recovery with a confirmed project 404 and no pending creation. Preserve infrastructure credentials/tutor settings and privately archive identity inputs before replacing IDs; retained Docta data requires its matching IAM backup because subjects change. When the IAM database is absent, setup or explicit identity recovery asks whether to reuse or change the initial human administrator before creating that instance, after rejecting retained Docta data. Existing IAM databases never accept an `.env` password edit as rotation. Same-instance deletions, permission/network failures and ambiguous creates fail closed. Gates: legacy/new-instance/admin-choice/preservation/refusal tests, real recovery and idempotent bootstrap, serial repository checks. Reverse the administrator choice independently by restoring the matching private profile and IAM data; no production authorization or tutor contract changes. |
| Gemini Chat Completions compatibility | 2026-09-29; updated 2026-10-29; implemented | Repeated saved `MODEL_UNAVAILABLE` messages were provider HTTP failures: (1) Google's Gemini API and gateways routing to it (such as Bifrost) reject `store: false` with HTTP 400 `INVALID_ARGUMENT`, so omit `store` when the endpoint or model ID refers to Gemini; (2) Gemini rejected `oneOf`/`anyOf` polymorphism and nested array `minItems`/`maxItems` inside structured output JSON Schemas with HTTP 400 `Request contains an invalid argument.`; the compatibility path supplies a unified Block schema for sampling without unions or nested bounds, and `generate()` normalizes decoded block properties and citation indexes before Pydantic contract validation. Earlier scoped adapter and live tests confirmed HTTP 200 with structured interventions and valid citations; the current activity contract has not been verified against a live provider. |
| Any-term FTS recall with lexeme coverage floor | 2026-10-03; implemented in `cb96da8` | Retrieval used `plainto_tsquery('spanish', question)`, an AND of all lexemes (`postgres_retriever.py`), so one term absent from the target chunk yielded zero rows and the turn abstained although the fragment was indexed. Diagnosis (local, isolated `docta-test` stack, 2026-10-03): on a synthetic 4-page case AND returned zero rows for 8 of 11 questions and the OR/coverage retriever for 1 of 11 (the off-topic one, as intended), with the target at rank 1 in every other zero-row case except one chunk-boundary case. Layer lost: tsquery, not extraction or tsvector. Change: query-side only in `PostgresRetriever`; matched chunks by OR over the stemmed lexemes of `plainto_tsquery('spanish', question)` and required coverage >= ceil(2n/3) (integer arithmetic, `tsvector_to_array`), where n is the number of query lexemes and matched lexemes are those present in the chunk `search_vector`. Ordered by coverage, `ts_rank_cd`, ordinal, `LIMIT 5`; all course/corpus/INDEXED/membership/scope filters unchanged. Question and lexemes are bound as parameters, each lexeme quoted/escaped. Standard SQL, no extension, no migration, no schema change. `RETRIEVAL_VERSION` is now `spanish-fts-coverage-top5-v2` (stored per message); `pipeline_version` and indexed content are unchanged, so no rebuild. The 2/3 value is an initial value; changing it requires updating this row. Evidence: controls C0–C7 and a cap test live in `tests/integration/test_retrieval_recall.py` (10 tests: C0 absent word retrieves target in top five; C1 partial vocabulary; C2 off-topic returns nothing; C3 distractor does not outrank; C4 isolation across courses and an inactive corpus plus positive recall; C5 exact question keeps rank one and provenance; C6 misspelled entity below the floor returns nothing; C7 special characters neither break nor widen the query; top-five cap with unique chunks); `npm run check:dev -- --full` passed on 2026-10-03: 160 unit tests, 214 serial Python tests including integration/browser suites, web lint/tests/build and whitespace checks. A local aggregate probe (no document content recorded) on a private 33-page Spanish PDF (75 chunks, isolated test resources): probes with one content word absent from the text returned zero rows under AND in 8 of 8 cases and in 0 of 8 under the new retriever; of 9 natural-language questions about facts stated in the PDF, 6 retrieved their expected chunk in the top five (4 of which returned zero rows under AND), 1 negative question returned nothing, and 8 off-topic questions returned nothing. Observed limits and regressions: questions that previously abstained without a model call can now retrieve weak evidence and reach the provider (validator and tutor, not retrieval, then decide); name-dominated queries are not fixed because many chunks match equally and `ts_rank_cd` has no signal for the intended chunk (in the probe, 34 of 75 chunks matched both entity tokens and the expected chunk ranked about 18th to 26th); accented interrogatives such as `cuándo`/`quién` are not Spanish stopwords and count as a query lexeme that never matches, removing tolerance for questions with one interrogative and three or four content words; excluding a fixed list of interrogatives was measured on 15 questions (+3 hits, no regression, no new false positive, expected chunk still not in the top five for name-dominated questions) and NOT adopted: it is a quality tuning to be measured with the reviewed dataset of Increment 5; vocabulary gap between question and text; a typo in a rare entity token below the floor returns nothing (control C6); accent folding of query words needs the `unaccent` extension (not installed); facts split across chunks or pages and overlap starting mid-word would require a `pipeline_version` bump; coverage subquery cost not measured on large corpora and `LIMIT 5` exercised only on small corpora; the top-five evidence cap is a validated contract; fakes and aggregates do not establish tutor quality. Gates: the tests above, existing citation integrity, cross-course, snapshot-corpus and provenance tests and `npm run check:dev -- --full` gate the change. Reversal: restore the AND `plainto_tsquery` query and the previous `RETRIEVAL_VERSION` value (`spanish-fts-and-top5-v1`); no data repair is needed because messages keep their stored version. |
| Increment 7 in parallel with Increments 5 and 6 (D-07-001, D-07-020..022) | 2026-10-23; accepted | The owner accepted developing dense retrieval (Increment 7) in parallel with Increments 5 and 6 on branch `feature/vector-search`, with FTS kept live and selectable and `fts` as the default until a measured switch. This supersedes, for Increment 7 only, the sequencing stated in `AGENTS.md` ("the next slice is Increment 5", pgvector/RRF not installed or mandatory) and in "Later work" below; those texts are corrected in the change that installs pgvector. The first increment adds no pgvector, dependency, migration or provider call. Reversal: pause the branch; FTS is unchanged. |
| Retrieval interface separation (D-07-001, D-07-020..022) | 2026-10-23; implemented in `098ddb0` | Behavior-preserving first increment of Increment 7. Contracts changed: (1) the scope check and the durable authorization query (membership, owner-private conversation, `pending` message with the same question, unexpired deadline, `READY` corpus) moved unchanged from `PostgresRetriever` to `retrieval_auth.py` (`validate_scope`, `authorize_retrieval`), called in the same place and order, so any future retriever reuses one audited authorization; (2) `Retriever` exposes `version`, like `TutorModel.version`; `rag.RETRIEVAL_VERSION` is removed and `PostgresRetriever.version` keeps `spanish-fts-coverage-top5-v2`; `ConversationService` receives `retrieval_version` and still persists it in `messages.retrieval_config_version` when accepting the question, before retrieval; (3) `Settings.retrieval_mode` (`DOCTA_RETRIEVAL_MODE`) accepts only `fts`, and `retriever_factory.build_retriever` selects the retriever, raising on any other mode without I/O; `create_app` resolves the retriever before building `ConversationService`, and an injected `retriever` still wins. A `conversation_service` injected together with a different retriever is not checked for version agreement. Persisted values are unchanged, so no migration or data repair. Evidence (devcontainer, isolated `docta-test` stack, 2026-10-23): `test_retrieval_recall.py` (10 passed) and `test_conversations.py` (19 passed) are unmodified relative to `b52d58d` and green; new unit tests for `retrieval_mode`, `validate_scope` and the factory (unit suite 175 passed); new `test_retrieval_version.py` (2 passed) proves the default app persists `spanish-fts-coverage-top5-v2` and an injected retriever's version is persisted (the turns fail with `COURSE_CORPUS_NOT_READY` before retrieval and the model, as the scenario has no READY corpus; the version is persisted at acceptance); Ruff and web checks (12 web tests) green; `npm run check:dev -- --full` passed on 2026-10-23 with 231 Python tests passed, including the browser test `tests/e2e/test_browser.py`. Reversal: revert the increment's commits; no data to repair. |
| Text embedder port and configuration | 2026-10-23; implemented in the embedder branch, not yet wired into ingestion or retrieval | Adds a synchronous `Embedder` port (`version`, `embed_documents`, `embed_query`) with `EmbeddingUnavailable` (transient) and `EmbeddingInvalid` (permanent), one httpx adapter for OpenAI-compatible `/v1/embeddings` endpoints (batching, timeout, count/dimension/finiteness validation; failures never carry payloads, texts, vectors or the key) and a deterministic test fake. `embedding_version` is `model\|dims\|sha8(templates+extras)`, so changing the model, dimensions, query/document templates, `input_type` mapping or provider routing yields a new version and requires re-importing documents; `embed_model` is capped so the value fits `varchar(200)`. `Settings` gains `retrieval_mode` `dense`, the `DOCTA_EMBED_*` variables (endpoint, model, `SecretStr` key, dimensions, timeout 10 s, batch 32, templates with `{text}`, `input_type` `off`\|`nvidia` mapping questions to `query` and documents to `passage`, JSON provider routing that must disable fallbacks) and `DOCTA_DENSE_MAX_DISTANCE`. Validation: the embedder is configured all-or-nothing; `dense` requires it and embed plus tutor timeouts below the message timeout; the endpoint needs HTTPS in production and no credentials, query or fragment; `dimensions` is never sent, only checked against the response. The selected model is `nvidia/nemotron-3.5-embed-8b:free` (text only, 4096 dimensions, full size) for development with synthetic or authorized material only; a model id ending in `:free` (lowercased, whitespace removed) aborts startup in production because free endpoints may log or retain data and students may be minors, and moving to the paid variant changes the version and requires re-import and threshold recalibration. A synthetic probe must still record commercial-use licensing, Spanish quality, `input_type` forwarding, free-tier rate limits, endpoint data policy and real dimensions before any pilot. The retriever factory still rejects `dense`; the worker passes the embedder variables through without defaults. Evidence: new unit tests with `httpx.MockTransport` (no network, no real key). Reversal: revert the commits; no data to repair. |
| pgvector in the local PostgreSQL image and `chunk_embeddings` | 2026-10-23; implemented in the infrastructure branch, not yet wired into ingestion or retrieval | The local PostgreSQL image is now built from `postgres:16.10-alpine` with pgvector v0.8.7 (source tarball sha256 `cac0b10c360f05b2d521200105ba3697e773d4cd3731f5a915a7e37ebe0bea85`), `dev:setup` and the test stack build it with `--build`, the `pgvector` Python dependency is added, and a migration creates `chunk_embeddings`: an unsized `vector` column, a primary key per embedding version, a composite foreign key to the chunk (course, corpus version, chunk) and a scope index; the migration has upgrade and downgrade. Reasons: the same Alpine/musl ABI as the official image keeps the collation of the existing volume unchanged, and exact search without HNSW keeps results deterministic and avoids index tuning before there is evidence. Evidence: 251 unit tests, 63 integration tests (including the new schema test), Ruff and `uv lock --check` passed. Reversal: run the downgrade only in an isolated environment, then return to the official `postgres` image; FTS does not depend on the table. |
| Embeddings during ingestion | 2026-10-23; implemented in `e18107c` | Embeddings are computed after chunking and outside the database transaction, in batches with an internal deadline inside the job lease. Chunks, embeddings and `READY` are committed in one transaction; `READY` requires exactly one embedding per chunk of the configured embedding version, and any discrepancy rolls the whole transaction back. A provider failure is transient and uses the existing bounded retry (`EMBEDDING_UNAVAILABLE`); an invalid response is not transient (`EMBEDDING_INVALID`). Without an embedder, ingestion is unchanged. Reversal: unset the embedder configuration; stored embeddings do not affect FTS. |
| Dense retrieval | 2026-10-23; implemented in `7007839` | `DenseRetriever` authorizes before and after embedding the question outside the session, makes one embedder call without retry, and runs exact cosine-distance search under the configured threshold with a limit of 5, scoped to the course and the captured corpus, ignoring other embedding versions and dimensions. Zero rows is abstention without calling the model; it never falls back to FTS. `DOCTA_RETRIEVAL_MODE` selects the retriever (default `fts`). The retriever version includes the threshold and `embedding_version`. Safe failures and the web report `RETRIEVAL_UNAVAILABLE` and `RETRIEVAL_INDEX_MISSING`. Evidence (2026-10-23): `full` passed with 345 tests, `integration_all` with 89 tests, and Ruff passed. Limitations: not run against a real provider; the threshold is uncalibrated and retrieval quality is not claimed; FTS remains the default; no test ingests with the worker and retrieves with dense end to end. Reversal: `DOCTA_RETRIEVAL_MODE=fts`; stored embeddings do not affect FTS. |

Phase 0 merged at `e31d1aa`. Historical records include 110 Python tests on September 7 and a
later September 8 prompt-v2 check reporting 118 Python tests, ten web tests, Ruff and ESLint.
Qwen3.5-9B compatibility checks reported one 5.6-second synthetic response and two prompt-v2
replays at 9.8/8.4 seconds. These are separate small checks, not a benchmark or learning result.
UI commit `8ee13a5` updates presentation, not retrieval/runtime. None of these tests was rerun
by this documentation consolidation.

On 2026-09-26, the case-only and repeated-citation repairs passed 124 Python tests, including
the isolated browser flow and persistence regressions. The earlier case-only repair also passed
10 web tests, Ruff, ESLint and the web build; Ruff was rerun after the repeated-citation repair.
The test PostgreSQL used port 25432 because Windows reserved the default 55432. These checks
do not establish typo-tolerant retrieval or real-model answer quality.

On 2026-09-29, Dev Containers CLI 0.89.0 built and started the development image on Docker
Desktop Engine 28.3.0: Python 3.12.11, uv 0.8.13, Node 22.17.0/npm 10.9.2 and Compose 2.40.3.
Creation/startup hooks installed locked dependencies and Chromium, provisioned local ZITADEL,
applied migrations and preserved IAM IDs on rerun. Real Login V2 passed both PKCE callbacks:
the helper validated JWT signature/issuer/audience/expiry; the web established an HttpOnly
session and completed an authenticated BFF/API request. Callback codes were absent from dev logs.
`npm run check:dev -- --full` completed in a Linux-filesystem verification copy: 91 unit tests,
135 total Python tests (serial integration/browser), 10 web tests, Ruff, ESLint, web/worker builds
and whitespace checks. Compose configurations and local documentation links also passed.
The Windows-mounted checkout's existing cache ACLs blocked a worker build-context read; no
cache/data was deleted. Use a Linux checkout as documented. Ubuntu's WSL Docker integration
was not enabled on this host, so direct VS Code reopening from that distro was not exercised.
These are local provisioning/contracts checks, not a real-tutor or learning-quality evaluation.

Later on 2026-09-29, VS Code opened the Ubuntu-26.04 checkout after Docker WSL integration was
enabled. Its newly generated environment conflicted with existing IAM database credentials.
Restoring the original managed environment and identity state completed bootstrap and migrations
without resetting volumes. The safe failure classifier and clean-checkout test settings passed
`npm run check:dev -- --full` in that WSL checkout: 94 unit tests, 138 total Python tests,
10 web tests, Ruff, ESLint, web/worker builds and whitespace checks. Integration services remained
isolated; this rerun did not measure real-model quality.

The subsequent 2026-09-29 OIDC correction reconciled the existing application to Web with the
same resource/Client IDs, PKCE and JWT access tokens. Management API confirmation returned no
compliance problems; protobuf JSON omits the default Web enum, which bootstrap now normalizes.
The 15 setup tests, 95 unit tests, 10 web tests, Ruff, ESLint and web/worker builds passed.
The full serial Python run passed 138 tests and timed out in the browser's Create course dialog;
the isolated browser rerun passed in 17.91 seconds. The intermittent browser failure remains
visible; this was not a clean full-suite run. Google configuration is documented from its
official compatibility guide, without a live Gemini request or a model-quality claim.

After the WSL container rebuild on 2026-09-29, new credentials again conflicted with retained
IAM volumes. Recovery restored only original infrastructure credentials/identity, preserving
the current tutor settings and a private backup of the new environment. Bootstrap and migrations
completed with healthy development services. The missing-environment guard rejected real existing
Docker data without creating files. Its 21 focused tests passed; `npm run check:dev -- --full`
then passed 101 unit tests, 145 total Python tests (including the browser flow), 10 web tests,
Ruff, ESLint, web/worker builds and whitespace checks. Local documentation links also passed.

The developer-owned configuration change on 2026-09-29 passed 31 setup tests and the full
WSL/Linux checks: 112 unit tests, 156 total Python tests (including the browser flow), 12 web
tests, Ruff, ESLint, web/worker builds and whitespace checks. A fresh isolated ZITADEL instance
accepted custom account/database names, reserved characters and literal dollar expressions in
passwords, and custom ports; its public Web JWT/PKCE callbacks and idempotent reconciliation
passed. Only those temporary IAM resources were removed. Existing development bootstrap retained
credentials, tutor settings and identity IDs. The first full run exposed a filtered test-port
override; propagating it to test Compose fixed the mismatch and the serial rerun passed.
Documentation links passed. No live LLM request or model-quality evaluation was performed.

The volume-deletion recovery on 2026-09-29 passed 52 setup tests and full checks through
Dev Containers CLI `exec` in the WSL container: 133 unit tests, 177 total serial Python tests
(including the browser flow), 12 web tests, Ruff, ESLint, web/worker builds and whitespace checks.
Real legacy-state recovery refreshed Project/Application/Client IDs, preserved every other
managed setting, and confirmed Web PKCE/JWT callbacks with no OIDC compliance problems.
A bootstrap rerun kept both environment files and identity state unchanged; the real retained-data
guard refused replacement. The first full run stopped at a disconnected VS Code Docker credential
helper; the successful rerun used temporary public-registry Docker and neutral Git configuration
without changing user authentication settings. Deleted development data was not restored;
no live LLM request or model-quality evaluation was performed.

The 2026-09-29 Gemini compatibility repair passed 14 focused adapter tests and the full WSL
devcontainer checks: 134 unit tests, 178 serial Python tests (including browser/integration),
12 web tests, Ruff, ESLint, web/worker builds and whitespace checks. A synthetic live request
confirmed HTTP 400 when `store: false` was sent; removing only that field eliminated the 400.
The resulting HTTP 503 still prevented completion for the selected Gemini model at the time of
verification. No course material was sent in the probe, no saved question was retried, and these
checks do not establish model quality or provider availability.

On 2026-10-29, the Socratic tutor prototype with assistant-ui and OpenUI Lang/Renderer passed
the full devcontainer checks: 293 unit tests, 394 total serial Python tests (including browser
and integration suites with conversations, action turns, and follow-up continuity), 41 Vitest
tests, Next.js 16.4.3 production build, Ruff, ESLint, Docker worker/proxy builds, and git diff
checks. No live LLM request or human learning evaluation was performed.

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
isolated PostgreSQL/RustFS/Redis resources. Do not bypass scope checks with copied SQL or reset
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
| 7 — retrieval comparison | Developed in parallel with 5–6 on `feature/vector-search`: pgvector is in the local PostgreSQL image with the `chunk_embeddings` migration and immutable embedding versions. Compare FTS and dense with other factors fixed, using exact search; FTS stays the default until measured evidence justifies a switch. RRF and hybrid retrieval are not authorized. |
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
