# Docta

A course-grounded pedagogical RAG tutor built as a modular monolith: Next.js, FastAPI,
PostgreSQL, S3/MinIO and a Redis Streams ingestion worker.

The PDF-to-chat flow is implemented. Tutor quality and pilot readiness are not established.
Next: **Increment 5 — offline evaluation harness and reviewed dataset**, not an agent loop.
See [architecture, decisions and next work](docs/ARCHITECTURE.md) and
[engineering rules](AGENTS.md). These three files are the maintained project documentation.

## Setup

### Dev container (WSL2 / Linux)

Clone into the WSL Linux filesystem, open the folder in VS Code and select **Dev Containers:
Reopen in Container**. The host needs Docker Engine and Compose 2.24.4+ with working `docker info`.
With Docker Desktop, enable the distro under **Resources → WSL Integration** and enable
**Resources → Network → Enable host networking** (Desktop 4.34+).
[Host networking](https://docs.docker.com/engine/network/drivers/host/) keeps browser callbacks,
issuer validation and signed uploads on consistent loopback URLs. One managed development
environment is supported per Docker host; native `docta` / `zitadel` stacks remain separate.

[devcontainer.json](.devcontainer/devcontainer.json) installs Python 3.12, uv 0.8.13,
Node.js 22.17/npm 10, Git, Docker CLI/Compose and Chromium. Linux volumes isolate dependencies
and build output from Windows; use a Linux checkout to avoid Windows cache ACLs in Docker builds.
A first opening installs tools and prints the configuration command. Hooks never ask for secrets.

Run **inside the container**:

```bash
npm run dev:configure   # First setup, or change the existing tutor configuration
npm run dev:all         # Reconcile infra/identity, migrate, then start API + worker + web
npm run dev:status      # Account names, URLs, IDs and secret locations; no secret values
```

The assistant starts from a neutral template; it never copies the root `.env`. First setup offers:

- Your local ZITADEL administrator login/email and a hidden password; Enter generates a password.
- Generated infrastructure credentials or your own PostgreSQL roles/databases/passwords,
  MinIO access/secret key and bucket, IAM database/master key, session secret, organization,
  project/application names and service-account names.
- Default or custom loopback ports, with matching URLs, callbacks, CORS and encoded database DSNs.
- Google, llama.cpp, Unsloth Studio, a compatible custom endpoint, or no tutor yet. Enter your
  own model ID and hidden API key; no model/key is preselected. Custom services also expose
  provider/schema choices. Tutor timeout, message deadline and completion budget are validated.

Saving configuration starts no services and makes no LLM request. `dev:all` starts isolated
ZITADEL/Login V2, creates/reuses a public **Web / Authorization Code + PKCE / JWT** application,
records the actual Project and Client IDs, starts PostgreSQL/MinIO/Redis and applies migrations.
The BFF remains public PKCE with no client secret. A missing tutor produces `MODEL_NOT_CONFIGURED`
when generation is required. Local services without authentication still need an explicit
nonempty key value for the current adapter contract; consult the selected server's configuration.

With an existing managed environment, reopening automatically reconciles infrastructure.
`dev:configure` defaults to **keep** and only changes tutor settings; account credentials, identity
state and volumes are preserved. It writes a private environment backup before reconfiguration.
Changing a password in an environment file does not rotate an initialized database or IAM
account. Use the service's administration flow and update matching client credentials afterward;
the assistant does not pretend to perform this rotation. Old managed profiles retain their
original account names and IDs. Unrelated existing stacks are never adopted automatically.

```bash
npm run dev:setup           # Reconcile infra/identity and migrations without starting app servers
npm run dev:stop            # After Ctrl+C: stop managed services, retain volumes and local state
npm run check:dev           # Unit tests, Ruff, web lint/tests/build, diff whitespace
npm run check:dev -- --full # Also worker build and serial integration/browser tests
```

| Endpoint | Initial default; configurable before first provisioning |
| --- | --- |
| Web / callback | `http://127.0.0.1:13000` / `/auth/callback` |
| API / docs | `http://127.0.0.1:18000` / `/docs` |
| ZITADEL console / issuer | `http://localhost:18080/ui/console` / `http://localhost:18080` |
| Helper callback | `http://127.0.0.1:18765/callback` |
| MinIO API / console | `http://127.0.0.1:19000` / `http://127.0.0.1:19001` |
| PostgreSQL / Redis | Loopback ports `15432` / `16379` |

#### Accounts and private state

Actual values belong only in ignored `.devcontainer/.env` and `.devcontainer/state/`, with
private file permissions on Linux. `dev:status` reports the selected names and secret locations.
Documentation and logs never contain passwords, PATs or API keys.

| Account / credential | Purpose and private location |
| --- | --- |
| Human administrator | `DOCTA_DEV_LOGIN_USERNAME` / `DOCTA_DEV_LOGIN_EMAIL`, password `DOCTA_DEV_LOGIN_PASSWORD`. Created only on first IAM initialization, email marked verified locally, no forced password change. IAM owner administration account; no application course membership is invented. |
| Provisioning service account | `DOCTA_DEV_BOOTSTRAP_USERNAME`, privileged local IAM provisioning. PAT in IAM bootstrap volume `/bootstrap/admin.pat`, copied to `.devcontainer/state/admin.pat`. |
| Login V2 service account | `DOCTA_DEV_LOGIN_SERVICE_USERNAME`; PAT stays in bootstrap volume `/bootstrap/login.pat`, consumed by Login V2. |
| Docta PostgreSQL role | `POSTGRES_USER` / `POSTGRES_DB`, password `POSTGRES_PASSWORD`; matching encoded `DOCTA_DATABASE_URL`. Local initial role is a database superuser. |
| IAM PostgreSQL role | `DOCTA_DEV_IAM_DB_USER` / `DOCTA_DEV_IAM_DB_NAME`, password `DOCTA_DEV_IAM_DB_PASSWORD`; internal encoded DSN. Local initial role is a database superuser. |
| MinIO administrator | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`; matching `DOCTA_S3_ACCESS_KEY` / `DOCTA_S3_SECRET_KEY`. Development API/worker share these storage credentials. |
| IAM encryption / browser sessions | `DOCTA_DEV_IAM_MASTERKEY` / `DOCTA_WEB_SESSION_SECRET`; preserve the IAM key with its database. |
| Tutor service | `DOCTA_TUTOR_API_KEY`; belongs to the selected developer/provider and is entered privately. |
| Identity / backups | `state/identity.json` records IAM Instance/Project/Application IDs and pending mutations. `state/env-before-configure-*` contains prior private environments; `state/identity-before-recovery-*` and `state/env-before-identity-recovery-*` preserve identity recovery inputs. Initial service PAT expiry is `DOCTA_DEV_PAT_EXPIRATION` (one year); changing that field alone does not renew an issued PAT. |

The Project ID is the audience; the actual OIDC Client ID is separate from the Application ID.
The Docker socket grants local daemon control. These privileged local accounts are development
resources; preserve private backups with volumes and restrict access to this host.

When recloning or moving from Windows to WSL, privately transfer the **original** managed
environment and its matching `state/` before starting. Missing configuration with known
development data volumes stops first setup before generating secrets or changing services.
If restoring from a private environment backup, use:

```bash
npm run dev:configure -- --from-env /private/path/original-managed.env
# Restore the matching .devcontainer/state/ privately, then:
npm run dev:setup
```

Import accepts only an original managed environment, refuses to overwrite one, and does not
generate credentials. Root/native environments require their own setup and are never silently
imported. Dependency-cache volumes alone do not block first setup. Rebuilding fixes a stale
workspace mount but cannot recover lost ignored files or rotate existing database passwords.

If you intentionally deleted **both IAM and Docta data volumes** while keeping the private
files, their saved IDs refer to the previous ZITADEL database. For legacy state that has no
Instance ID, run this once inside the devcontainer:

```bash
npm run dev:recover-identity
```

Recovery requires a confirmed missing saved project, no pending creation and no Docta data
volumes. It privately archives the environment/identity state, preserves credentials and tutor
settings, provisions the new identity IDs, starts Docta infrastructure and applies migrations.
It deletes no Docker resources and cannot restore data you already deleted. Reopening or
`dev:setup` then reuses the new IDs; `dev:all` starts the application servers.

New state binds IDs to the authenticated [ZITADEL instance](https://zitadel.com/docs/reference/api/admin/zitadel.admin.v1.AdminService.GetMyInstance).
A confirmed instance change automatically recovers only when all three Docta data volumes are
absent. Retained application data requires restoring its matching IAM database/bootstrap backup:
new users with the same login have different identity subjects. A missing project in the same
instance, permission failures and ambiguous creations never trigger automatic recreation.
Removing containers alone or rebuilding the devcontainer preserves data volumes and identity IDs.

Helper commands also use the managed environment:

```bash
uv run --frozen python scripts/dev.py run -- uv run python scripts/get_dev_token.py
uv run --frozen python scripts/dev.py run -- uv run python scripts/upload_dev_pdf.py ./example.pdf
```

Open the helper's authorization URL in the host browser if needed; keep its token output private.
Development web logs omit callback authorization codes. On a failed stage, fix Docker/network/port
availability and rerun `dev:setup`. IAM password rejection requires restoring its original
credentials/master key, not regenerating them. Unknown creation outcomes remain in
`state/identity.json`; inspect the named resource before resolving a pending entry. Provisioning
never automatically repeats an ambiguous mutation. Expired PATs require local IAM administration.
No volume reset command is supplied. Changing initialized IAM ports requires an explicit service
migration; the reconfiguration assistant preserves the existing port contract.

### Native setup

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

To inspect requests in Apidog, import [the OpenAPI collection](docs/docta-api.openapi.json), set
its server URL to `http://127.0.0.1:8000`, and set its Bearer Token authorization from a fresh token
printed by `uv run python scripts/get_dev_token.py`. Keep the token local and do not commit it.
The collection covers Docta API routes; the document upload endpoint returns a short-lived MinIO
`upload_url`, which needs a separate `PUT` of the PDF using the returned headers. Use
`scripts/upload_dev_pdf.py` for that complete flow.

For local ZITADEL, the `.env.example` values configure the API issuer, project audience and JWKS
URL. In ZITADEL, use a public OIDC application with Authorization Code + PKCE (S256), enable JWT
access tokens, and register the helper callback above. Set `DOCTA_DEV_OIDC_CLIENT_ID` to the
application's OIDC Client ID (not its Application ID). The helper requests the configured
project's audience and validates signature, issuer, audience and expiry before printing the
short-lived access token. If ZITADEL uses another issuer or project, align the API and
`DOCTA_DEV_OIDC_*` settings; never put a client secret or long-lived token in the collection.

Create a private conversation under `POST /api/v1/courses/{course_id}/conversations`, then send
`{"question":"..."}` to `POST /api/v1/conversations/{conversation_id}/messages`, both with a
bearer token and an `Idempotency-Key`. The latter returns SSE. Use `GET` on the conversation
for paginated history, or `/messages/{message_id}` for durable status. Same key/question observes
the same execution; changed text conflicts. A new generation needs a new question/key and may
incur another charge. Never infer success from a closed SSE connection.

## Tutor configuration and diagnosis

Select a tutor service, model and private API key together. In the devcontainer run
`npm run dev:configure` and select `google`; enter your model ID and hidden key, then restart
`dev:all`. Native setup uses root `.env`. The HTTP adapter supports Google's
[Chat Completions compatibility endpoint](https://ai.google.dev/gemini-api/docs/openai):

```dotenv
DOCTA_TUTOR_ENDPOINT_URL=https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
DOCTA_TUTOR_MODEL=<your-selected-google-model-id>
DOCTA_TUTOR_API_KEY=<your-private-key>
DOCTA_TUTOR_PROVIDER=chat_completions
DOCTA_TUTOR_SCHEMA_PROFILE=standard
```

When switching from Unsloth/llama.cpp, change both provider and schema profile as shown; do not
retain Studio-only generation parameters. The model ID depends on the selected service/account.
Google documents structured outputs; configuration alone does not verify a live request or model
quality. Keep the existing local response/citation validation and do not send course material
to a remote service implicitly. Locked tests use deterministic providers and dedicated resources.
Gemini's compatibility endpoint rejects the optional `store` request field; the adapter omits it
only for that endpoint. An [HTTP 503](https://ai.google.dev/gemini-api/docs/troubleshooting)
can still mean Gemini is temporarily unavailable. Docta keeps
the original question and does not automatically repeat an ambiguous model call. Browser-extension
console errors do not diagnose the tutor; use the saved message's `failure_code`.

| Setting | Default / bounds |
| --- | --- |
| `DOCTA_TUTOR_TIMEOUT_SECONDS` | 45; 1–179 seconds, strictly below message timeout. |
| `DOCTA_MESSAGE_TIMEOUT_SECONDS` | 90; 5–180 seconds, bounds the whole operation. |
| `DOCTA_TUTOR_MAX_OUTPUT_TOKENS` | 2,000; 128–8,000, including the provider's completion budget. |
| `DOCTA_TUTOR_SCHEMA_PROFILE` | `standard`; `llama_cpp` omits grammar `maxLength` only, retaining local validation. |
| `DOCTA_TUTOR_PROVIDER` | `chat_completions`; `unsloth` additionally sends thinking/tools/MCP disabled and matching `max_tokens`. |

Restart API after environment changes. For example, tutor=175 and message=180 is valid;
tutor=180/message=180 or any tutor/message equality fails startup validation. Use numeric seconds, not unit
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
Typos and paraphrases/follow-ups may miss evidence. A unique case-only citation mismatch is
restored to the exact retrieved quote; other mismatches are rejected. Multiple quotes from one
chunk are checked individually, then stored as one citation. Retrieved chunks and valid quotes
do not prove every answer claim is supported. Inspect private evidence only with authorized
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

Windows options: set `$env:DOCTA_TEST_POSTGRES_PORT = "25432"` or
`$env:DOCTA_TEST_MINIO_PORT = "19500"` before both Compose and pytest if the default port is
reserved. With installed Edge, set `$env:DOCTA_E2E_BROWSER_CHANNEL = "msedge"` instead
of downloading Chromium. For temporary-directory permission problems:

```powershell
$doctaTestTemp = Join-Path $env:TEMP ('docta-tests-' + [guid]::NewGuid().ToString('N'))
uv run --cache-dir .uv-cache pytest -p no:cacheprovider --basetemp=$doctaTestTemp
```

Historical test results are recorded in [architecture](docs/ARCHITECTURE.md#accepted-decisions-and-history),
not claims that these commands ran during documentation edits. No quality-evaluation command
exists yet; its implementation and usage belong to Increment 5.
