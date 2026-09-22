# Conversation and RAG operations

Conversation execution, introduced in Increment 3, runs inside the FastAPI process.
PostgreSQL owns questions, outcomes and evidence;
Redis remains responsible only for document ingestion. See [ADR 0002](../adr/0002-durable-conversation-and-scoped-rag.md).

This remains the current operational contract after Phase 0 closure. The browser workspace is
implemented in Increment 4; see its [runbook](browser-workspace.md). Quality work is planned in
[Phase 1](../PHASE_1_TUTOR_QUALITY.md); future resolution/planner modes are not runtime capabilities yet.

**Documentation review:** 2026-09-22. Procedures below describe the inspected code. Dated live
diagnostics remain historical; this review did not invoke a model or inspect the active `.env`.
The proposed [pedagogical runtime](../DOCTA_HARNESS_ARCHITECTURE.md) is not the current tool API.

## Start and configure

Apply migrations before starting the updated API:

```powershell
uv run --cache-dir .uv-cache alembic -c apps/api/alembic.ini upgrade head
uv run --cache-dir .uv-cache uvicorn docta_api.main:app --app-dir apps/api/src --env-file .env --host 127.0.0.1 --port 8000
```

Set these values in the local environment or secret manager before starting the API:

| Variable | Purpose/default |
| --- | --- |
| `DOCTA_TUTOR_ENDPOINT_URL` | Full `/v1/chat/completions` endpoint supporting strict JSON Schema. No default provider. |
| `DOCTA_TUTOR_MODEL` | Provider model identifier or configured gateway alias. No default model. |
| `DOCTA_TUTOR_API_KEY` | Bearer credential for that endpoint. Never commit or print it. |
| `DOCTA_TUTOR_TIMEOUT_SECONDS` | Default 45; allowed 1–120 seconds, strictly shorter than message timeout. |
| `DOCTA_TUTOR_MAX_OUTPUT_TOKENS` | 2,000, including the provider's completion budget. |
| `DOCTA_TUTOR_SCHEMA_PROFILE` | `standard`; use `llama_cpp` for servers rejecting large `maxLength` grammar repetitions. |
| `DOCTA_TUTOR_PROVIDER` | `chat_completions`; `unsloth` disables Studio thinking/tools/MCP and also supplies `max_tokens`. |
| `DOCTA_MESSAGE_TIMEOUT_SECONDS` | Default 90; allowed 5–180 seconds. Bounds the complete operation; deadline is stored in PostgreSQL. |

Endpoint/model/key must be supplied together. HTTPS is required in production. Unconfigured
generation records `MODEL_NOT_CONFIGURED`; it never substitutes a deterministic runtime model.
No paid request is needed for the tests. A configured model must support the emitted schema and
`max_completion_tokens`. Inspect failures through safe codes; do not enable raw HTTP body logging.

For Unsloth serving GGUF through llama-server, use the exact model ID from `/v1/models` (the
quantization suffix may not be part of the served ID). A confirmed compatibility issue in the
local server returned HTTP 400 with `failed to parse grammar` when compiling `maxLength`.
The explicit `llama_cpp` profile omits only that keyword from the sampling schema. Docta still
enforces all original lengths, types, citation integrity and output-byte limits locally. It
does not retry automatically or fall back to unvalidated free text.

For Studio, select `DOCTA_TUTOR_PROVIDER=unsloth` and initially use
`DOCTA_TUTOR_MAX_OUTPUT_TOKENS=512` for short pedagogical hints. This explicit adapter profile
sends `enable_thinking=false`, `enable_tools=false`, `mcp_enabled=false`, and matching
`max_tokens`/`max_completion_tokens`. The standard provider does not receive these extensions.
Restart the API after changing `.env`; an existing process retains its initial configuration.
If a response exceeds its token budget it fails validation; no truncated answer is exposed.

### Startup failure after increasing a timeout

`Tutor timeout must be shorter than message timeout` is configuration validation during startup,
not a retrieval or provider error. Both settings use numeric seconds, without a unit suffix.
An illustrative valid pair is:

```dotenv
DOCTA_TUTOR_TIMEOUT_SECONDS=120
DOCTA_MESSAGE_TIMEOUT_SECONDS=150
```

This is not a recommended latency target or a claim about the active environment. It leaves
additional time for retrieval/validation/persistence but does not guarantee completion. Do not
exceed the bounds in [config.py](../../apps/api/src/docta_api/config.py); restart the API after
changes. Equal timeouts fail validation. Raising a timeout neither improves retrieval nor proves
model reliability. Never paste the full `.env` or validation payload containing credentials into logs.

### Retrieval is separate from provider connectivity

The current retriever uses strict all-term full-text search. It does not repair missing spaces:
`que esRetrieval-augmented Generation` searches for `esretrieval`, whereas
`¿Qué es Retrieval-augmented Generation?` searches the intended concept. A greeting such as
`Hola` normally retrieves no course evidence and returns the deterministic abstention without
calling the model. Confirm retrieval separately from provider connectivity when diagnosing it.

FTS processes Spanish lexemes and stopwords; it is not an exact-string comparison of the whole
question, nor BM25 or dense semantic retrieval. Its current AND condition and original-query
contract can miss relevant evidence. A trace with several chunks does not prove those chunks
contain enough information to answer.

## Exercise the API

First authenticate with the existing OIDC flow and publish a PDF using `scripts/upload_dev_pdf.py`.
The actor must be a current course member. A teacher can exercise their own conversation; student
membership is provisioned in the database as in Increment 1. There is no enrollment UI yet.

Create the conversation, using an opaque client-generated key retained across retries:

```http
POST /api/v1/courses/{course_id}/conversations
Authorization: Bearer <access-token>
Idempotency-Key: <creation-key>
```

The JSON response contains `id`, `course_id` and `created_at`. Use that ID to ask:

```http
POST /api/v1/conversations/{conversation_id}/messages
Authorization: Bearer <access-token>
Idempotency-Key: <question-key>
Content-Type: application/json

{"question":"¿Qué es la velocidad?"}
```

This returns `text/event-stream`. Consume `message.accepted`, optional stage progress and exactly
one terminal `message.completed` or `message.failed` while connected. Intermediate events expose
identifiers only. The terminal payload contains the question, state and either a validated
`response` with citations or `failure_code`/`failure_message`. Treat answer/quote text as untrusted
plain text when rendering. Do not infer completion from connection closure or missing progress.

Reconnect through either JSON endpoint, using the same bearer identity:

```http
GET /api/v1/conversations/{conversation_id}?after_sequence=0&limit=50
GET /api/v1/conversations/{conversation_id}/messages/{message_id}
```

History returns ordered `messages` and `next_after_sequence`; use that cursor until it is null.
It retains all questions, including failed ones, and original citation metadata after withdrawal.
Reposting exactly the same question/key observes the existing message without another model call.
A changed question with the same key returns `409 idempotency_key_reused`. A different question
while one is pending returns `409 conversation_busy` and is not accepted.

## Failure and recovery

### Diagnose the stage before changing a component

| Observation | What it establishes | Next safe check |
| --- | --- | --- |
| API fails configuration validation | No accepted conversation execution yet. | Numeric values, bounds and tutor timeout shorter than message timeout. |
| `failed` / `MODEL_UNAVAILABLE` | Provider call failed or timed out. | Endpoint availability, compatible configuration, safe duration/outcome; not evidence absence. |
| `completed` abstention, zero evidence | No generation was needed; retrieval returned empty. | Captured corpus, authorized source and actual query; annotate expected evidence before judging a miss. |
| `completed` abstention, nonempty evidence | Provider abstained and validation canonicalized the response. | Whether retrieved content really supports the question, then model/prompt behavior. |
| Answer with valid citations | Scope/provenance/literal quotation checks passed. | Human review of claim support, correctness and pedagogical help. |

Inspect persisted evidence only through authorized access and keep educational content out of
operational logs. Do not infer a recall failure solely from the final UI message. If the answer
is in an authorized source but not retrieved, retain it as a reviewed evaluation candidate, not
an automatically exported private conversation. Unknown causes remain `UNDETERMINED`.

### Public classifications

| Code | Behavior/action |
| --- | --- |
| `COURSE_CORPUS_NOT_READY` | Publish an indexed course corpus, then explicitly submit a new question/key. |
| `MODEL_NOT_CONFIGURED` | Configure the three tutor values and restart the API. |
| `MODEL_UNAVAILABLE` | Upstream timeout/HTTP failure. Check endpoint availability and credential configuration. |
| `MODEL_OUTPUT_INVALID` | Invalid JSON/schema, refusal, truncated output, citation or quotation mismatch. Evaluate provider compatibility. |
| `RETRIEVAL_SCOPE_INVALID` | Fail closed. Investigate IDs, membership and captured corpus; never broaden a filter. |
| `PROCESSING_INTERRUPTED` | Execution shutdown, deadline or abandoned pending message. The question remains durable. |
| `PERSISTENCE_UNAVAILABLE` | Commit failed. No partial answer/citations are exposed. |
| `INTERNAL_ERROR` | Unexpected failure classified without sensitive exception text. Investigate using request/message IDs. |

No-evidence abstention is a completed response, not a technical error. Spanish FTS currently
requires all non-stopword query terms; paraphrases can abstain even when a related passage exists.

A completed abstention can also come from the model after successful retrieval. Check the
message's persisted `retrieved_evidence` and correlated retrieval/generation outcomes before
changing the endpoint: zero evidence skips generation; nonempty evidence followed by a completed
abstention means the provider declined to answer. Provider errors instead leave a failed message.

Prompt `phase0-guidance-v2` explicitly permits short conceptual explanations in Spanish from
sources in another language, while exercises still receive hints. It asks for short literal
quotations in the source language, preserving PDF characters. The validator continues to reject
translated, reformatted or fabricated quotations; this prompt change does not relax that gate.
An earlier abstention in the history is not evidence that the current sources are insufficient.

After a prompt change, restart the API process and submit a new question. Reusing the old
idempotency key only returns its original persisted result. No migration, re-upload or web
restart is required for this prompt update.

### Historical prompt diagnostic — 2026-09-08

Live diagnostic evidence (2026-09-08, explicitly authorized): a saved RAG definition question had
five retrieved fragments and no prior history. The original prompt abstained in both replays.
Earlier candidate instructions elicited explanations but sometimes changed quoted text, which
the validator rejected. The final v2 prompt produced validated Spanish explanations in two
repetitions (9.8 and 8.4 seconds, respectively two and one exact citations). An unsupported
geography question abstained. These replays used the configured Unsloth Qwen3.5-9B adapter and
the saved PDF evidence, without writing conversation data. This small diagnostic establishes
neither general model reliability nor semantic support for every generated sentence. The
automated HTTP-port regressions use synthetic English evidence and deterministic model output.

Verification after the prompt change: 24 targeted tutor tests and the full 118-test Python suite
(including the browser flow) passed. The ten web unit tests, Ruff, ESLint and `git diff --check`
also passed. The full suite used dedicated test services with `DOCTA_TEST_MINIO_PORT=19500`,
`DOCTA_E2E_BROWSER_CHANNEL=msedge` and a fresh pytest temporary directory.

### Current recovery semantics

An API crash does not cause automatic provider re-execution. On restart, the recovery sweep marks
expired pending messages failed within five seconds of their stored deadline (authenticated reads
also reconcile them). API shutdown attempts to record failure immediately. Late model results cannot
complete an expired/terminal message. If the database is down, reconciliation resumes when it returns;
SSE closes rather than inventing a durable terminal event. Reuse the original key to inspect the
outcome; a new generation is an explicit new question/key, potentially with a new provider charge.

Logs include request/course/conversation/message IDs and operation/outcome/duration. Retrieval also
records document-version IDs. They omit educational text, provider bodies, authorization and keys.

## Verification and remaining gates

```powershell
uv run --cache-dir .uv-cache pytest tests/unit/test_rag.py tests/unit/test_tutor_http.py
docker compose -f infra/compose.test.yaml up -d --wait
uv run --cache-dir .uv-cache pytest tests/integration/test_conversations.py
```

Integration resources are generated and isolated by the existing test fixture. They never target
the development database. The full suite additionally rebuilds migrations and restarts dependencies.

Before a student pilot, select the model through a reviewed benchmark and verify the complete
browser deployment with its real identity/model configuration. The local smoke tests and prompt
diagnostics recorded above establish compatibility on a small sample, not that acceptance gate.
Exact citation checks cannot prove semantic entailment of every sentence. The Increment 4 UI is
implemented; the next slice is the evaluation harness and reviewed dataset in
[Phase 1](../PHASE_1_TUTOR_QUALITY.md).

## Implementation verification — 2026-09-07

Historical Increment 3 handoff. Statements about that session's migrations and lack of live
requests apply to 2026-09-07; subsequent 2026-09-08 checks are recorded above and in Phase 0.

Verified commands/results in this workspace:

| Command | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=$doctaTestTemp` | 110 passed: 70 unit and 40 integration tests, including 16 conversation cases. |
| `.venv/Scripts/ruff.exe check apps/api/src scripts tests apps/api/migrations/env.py apps/api/migrations/versions/20260907_0007_conversations.py` | Passed. |
| `npm run lint:web` / `npm run test:web` / `npm run build:web` | Passed; four tests and production build. |
| `docker compose --env-file .env -f infra/compose.yaml --profile worker build worker` | Local worker image built; native/Docker restart tests passed. |
| `docker compose --env-file .env -f infra/compose.yaml config --quiet` | Passed. |
| `docker compose -f infra/compose.test.yaml config --quiet` | Passed. |
| `git diff --check` | Passed. |

The existing virtual environment was used because sandboxed `uv run` could not reach PyPI.
An initial full run passed 107 tests and hit two Windows temporary-directory permission errors.
The final complete run passed using a fresh system temporary directory:

```powershell
$doctaTestTemp = Join-Path ([System.IO.Path]::GetTempPath()) ('docta-inc3-' + [guid]::NewGuid().ToString('N'))
```

Migration 0007 was exercised against generated test databases, including downgrade/rebuild.
The development database was not migrated by this implementation session. Apply the migration
before using the new routes. No live/paid tutor request was executed.

Changed files, grouped by responsibility:

- Persistence: `apps/api/src/docta_api/conversation_models.py`, `models.py`,
  `apps/api/migrations/env.py`, and `apps/api/migrations/versions/20260907_0007_conversations.py`.
- Conversation API/execution: `apps/api/src/docta_api/conversations.py`,
  `conversation_routes.py`, `conversation_runtime.py`, and `main.py`.
- RAG/configuration: `apps/api/src/docta_api/rag.py`, `postgres_retriever.py`, `tutor_http.py`,
  `config.py`, and `.env.example`.
- Tests: `tests/integration/test_conversations.py`, `test_local_dependencies.py`,
  `tests/unit/test_rag.py`, `test_tutor_http.py`, and `test_config.py`.
- Documentation: `README.md`, `docs/WALKING_SKELETON.md`,
  `docs/adr/0002-durable-conversation-and-scoped-rag.md`, and this runbook.
