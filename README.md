# Docta

A course-grounded pedagogical RAG tutor built as a modular monolith: Next.js, FastAPI,
PostgreSQL, S3/MinIO and a Redis Streams ingestion worker.

The PDF-to-chat flow is implemented. Tutor quality and pilot readiness are not established.
Next: **Increment 5 — offline evaluation harness and reviewed dataset**, not an agent loop.
See [architecture, decisions and next work](docs/ARCHITECTURE.md) and
[engineering rules](AGENTS.md). These three files are the maintained project documentation.

## Setup

Requirements: Node.js 22/npm 10, Python 3.12 or 3.13, uv, and Docker Compose v2.
Run commands from the repository root. Preserve an existing `.env`:

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
npm ci
uv sync --cache-dir .uv-cache
```

Configure local values using [.env.example](.env.example); never commit secrets or paste the
whole environment into diagnostics. The API reads root `.env`; Next.js loads it server-side.

| Configuration | Requirement |
| --- | --- |
| Database, Redis, S3 | Match published Compose ports with `DOCTA_DATABASE_URL`, `DOCTA_REDIS_URL`, `DOCTA_S3_ENDPOINT_URL` and `DOCTA_MINIO_HEALTH_URL`. Configure S3 access/secret key, bucket and region. |
| API identity | Set `DOCTA_OIDC_ISSUER`, `DOCTA_OIDC_AUDIENCE` and `DOCTA_OIDC_JWKS_URL` together. Authenticated routes fail closed without OIDC; production requires it. |
| Web identity | Public OIDC client with Authorization Code + PKCE and JWT access tokens; set `DOCTA_WEB_OIDC_CLIENT_ID` (or the dev-client fallback) and register the exact `/auth/callback` URI. |
| Web session | Set `DOCTA_WEB_ORIGIN` and `DOCTA_WEB_SESSION_SECRET` with at least 32 random characters. Never expose the secret through `NEXT_PUBLIC_` variables. |
| Tutor | Set `DOCTA_TUTOR_ENDPOINT_URL`, `DOCTA_TUTOR_MODEL` and `DOCTA_TUTOR_API_KEY` together. No default provider/model. Endpoint must accept the emitted Chat Completions strict JSON Schema contract; production requires HTTPS. |

Generate a session secret locally and store it only in `.env`:

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Default web origin/callback: `http://127.0.0.1:3000` and
`http://127.0.0.1:3000/auth/callback`. For port 3100, update origin, callback and MinIO CORS
consistently, then use `npm run dev:web:local`. `localhost` and `127.0.0.1` are different origins.
ZITADEL is the previously tested local provider, not a required production selection; start your
identity provider separately from Docta's Compose services.

## Start and stop

Before upgrading an existing database, stop API and workers and preserve durable data.
Do not downgrade a live database/queue. Migrations currently end at 0008; 0006 introduced
durable ingestion, 0007 conversations and 0008 course-creation idempotency.

```powershell
docker compose --env-file .env -f infra/compose.yaml up -d --wait
uv run --cache-dir .uv-cache alembic -c apps/api/alembic.ini upgrade head
```

Use three terminals:

```powershell
uv run --cache-dir .uv-cache uvicorn docta_api.main:app --app-dir apps/api/src --env-file .env --host 127.0.0.1 --port 8000
```

```powershell
uv run --cache-dir .uv-cache python -m docta_api.worker
```

```powershell
npm run dev:web
```

Alternatively, replace the native worker with a container after applying migrations:

```powershell
docker compose --env-file .env -f infra/compose.yaml --profile worker up -d --build worker
```

Health endpoints: API `/api/v1/health/live` and `/api/v1/health/ready` on port 8000, web
`/api/health` on the configured web port. Readiness checks PostgreSQL/object storage, not worker
progress. MinIO console defaults to port 9001. Stop API/native worker terminals and Compose
without deleting volumes:

```powershell
docker compose --env-file .env -f infra/compose.yaml down
```

### Exercise the flow

Sign in, create a course, upload a digital text PDF, wait for indexing and publish it. Ask a
question supported by the material, inspect citations and reload to verify durable history.
Test an unsupported question separately from a provider failure. A teacher can test their own
course; a student needs an existing server-side membership. There is no enrollment UI.

For direct API testing, register the helper callback `http://127.0.0.1:8765/callback`, configure
`DOCTA_DEV_OIDC_CLIENT_ID` with the actual Client ID, and enable JWT access tokens:

```powershell
uv run python scripts/get_dev_token.py
uv run python scripts/upload_dev_pdf.py ./example.pdf --course-title "Test course"
```

The first helper prints a bearer token: treat terminal output as secret. The upload helper
authenticates, uploads, polls and activates only after indexing; it does not print credentials
or presigned URLs. If polling expires, inspect the existing version, do not upload duplicates.
Route/schema details are available from the running API's `/docs` and `/openapi.json`.

Create a private conversation under `POST /api/v1/courses/{course_id}/conversations`, then send
`{"question":"..."}` to `POST /api/v1/conversations/{conversation_id}/messages`, both with a
bearer token and an `Idempotency-Key`. The latter returns SSE. Use `GET` on the conversation
for paginated history, or `/messages/{message_id}` for durable status. Same key/question observes
the same execution; changed text conflicts. A new generation needs a new question/key and may
incur another charge. Never infer success from a closed SSE connection.

## Tutor configuration and diagnosis

| Setting | Default / bounds |
| --- | --- |
| `DOCTA_TUTOR_TIMEOUT_SECONDS` | 45; 1–120 seconds, strictly below message timeout. |
| `DOCTA_MESSAGE_TIMEOUT_SECONDS` | 90; 5–180 seconds, bounds the whole operation. |
| `DOCTA_TUTOR_MAX_OUTPUT_TOKENS` | 2,000; 128–8,000, including the provider's completion budget. |
| `DOCTA_TUTOR_SCHEMA_PROFILE` | `standard`; `llama_cpp` omits grammar `maxLength` only, retaining local validation. |
| `DOCTA_TUTOR_PROVIDER` | `chat_completions`; `unsloth` additionally sends thinking/tools/MCP disabled and matching `max_tokens`. |

Restart API after environment changes. For example, tutor=120 and message=150 is valid;
tutor=120/message=90 or equal values fail startup validation. Use numeric seconds, not unit
suffixes. Longer timeouts do not improve retrieval or guarantee completion. Schema truncation,
refusal or invalid output is rejected; provider compatibility must be tested, not assumed.

| Observation / code | Meaning and next check |
| --- | --- |
| `COURSE_CORPUS_NOT_READY` | Publish an indexed corpus, then explicitly submit a new question/key. |
| `MODEL_NOT_CONFIGURED` | Supply endpoint/model/key together and restart API. |
| `MODEL_UNAVAILABLE` | Provider timeout/HTTP failure; inspect availability/configuration, not just retrieval. |
| `MODEL_OUTPUT_INVALID` | Schema, refusal, truncation or invalid citation/quote. Check compatibility and budgets. |
| `RETRIEVAL_SCOPE_INVALID` | Fail closed; inspect membership/message/corpus binding, never loosen filters. |
| `PROCESSING_INTERRUPTED` | Shutdown/deadline/abandoned execution; question remains durable. No automatic model retry. |
| `PERSISTENCE_UNAVAILABLE` / `INTERNAL_ERROR` | Inspect safe correlated diagnostics; never fabricate a committed result. |
| Completed abstention, zero evidence | Retrieval returned empty; no model call. Inspect captured corpus/query and expected evidence. |
| Completed abstention, nonempty evidence | The model declined; inspect actual evidence sufficiency before changing prompts. |

Spanish FTS is lexical AND search, not literal whole-string matching, BM25 or dense retrieval.
Paraphrases/follow-ups may miss evidence. Several retrieved chunks do not prove answerability;
valid quotes do not prove every claim is supported. Inspect private evidence only with authorized
access; never enable raw prompt/answer/provider-body logging. Preserve unknown causes as unknown.

## Ingestion recovery

Use the document-version endpoint for durable state/failure codes. Check worker logs and Redis
group state using configured stream/group names (defaults below):

```powershell
docker compose --env-file .env -f infra/compose.yaml logs --tail 50 worker
docker compose --env-file .env -f infra/compose.yaml exec redis redis-cli XINFO GROUPS docta:jobs:ingestion:v1
docker compose --env-file .env -f infra/compose.yaml exec redis redis-cli XPENDING docta:jobs:ingestion:v1 docta-ingestion-workers-v1
```

For a native worker, inspect its terminal instead. PostgreSQL `ingestion_jobs` and `outbox_events`
hold canonical state; use read-only metadata queries against the intended environment.

- Redis unavailable/lost: restore it and keep the worker running. The worker recreates missing
  transport state and republishes due unfinished database jobs. Completed artifacts remain intact.
- Worker terminated/stuck: restart it; expired leases recover. Never reset fencing tokens.
- Terminal PDF/checksum/format failure: fix the source and create a new upload/version through
  the API. No manual in-place requeue of immutable failed versions is supported.
- Defaults: lease 300 seconds, maximum three attempts, retry base two seconds, reconciliation
  every 30 seconds. See `DOCTA_JOB_*` in [config.py](apps/api/src/docta_api/config.py). No lease
  extension; keep the lease above measured processing duration. Outbox publish leases last 30 seconds.

PostgreSQL and MinIO volumes/backups must survive recovery. Redis AOF is not the canonical job
store or a high-availability guarantee. Do not clear volumes, jobs or stream state to fix a test.

## Verification

Checks without integration services:

```powershell
uv run --cache-dir .uv-cache pytest tests/unit
uv run --cache-dir .uv-cache ruff check apps/api/src scripts tests
npm run lint:web
npm run test:web
npm run build:web
git diff --check
```

Full integration/browser checks additionally require dedicated test services, the worker image
and a browser. After setup and build above:

```powershell
docker compose -f infra/compose.test.yaml up -d --wait
docker compose --env-file .env -f infra/compose.yaml --profile worker build worker
npx playwright install chromium
uv run --cache-dir .uv-cache pytest
docker compose --env-file .env -f infra/compose.yaml config --quiet
docker compose -f infra/compose.test.yaml config --quiet
```

Tests generate isolated databases/buckets/streams on ports 55432, 56379 and 59000. They do not
target development data. Run suites serially: a worker test restarts shared test dependencies.
Browser tests use the production web build, real API/storage/worker and test-only OIDC/model
doubles. They do not assess the real model's pedagogical quality.

Windows options: set `$env:DOCTA_TEST_MINIO_PORT = "19500"` before both Compose and pytest if
59000 is reserved. With installed Edge, set `$env:DOCTA_E2E_BROWSER_CHANNEL = "msedge"` instead
of downloading Chromium. For temporary-directory permission problems:

```powershell
$doctaTestTemp = Join-Path $env:TEMP ('docta-tests-' + [guid]::NewGuid().ToString('N'))
uv run --cache-dir .uv-cache pytest -p no:cacheprovider --basetemp=$doctaTestTemp
```

Historical test results are recorded in [architecture](docs/ARCHITECTURE.md#accepted-decisions-and-history),
not claims that these commands ran during documentation edits. No quality-evaluation command
exists yet; its implementation and usage belong to Increment 5.
