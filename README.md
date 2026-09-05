# Docta

Docta is being built as a trustworthy pedagogical RAG tutor. This repository currently contains
Increments 0 through 2B of the [walking skeleton](docs/WALKING_SKELETON.md): an executable Next.js
web app, a FastAPI boundary, PostgreSQL/MinIO infrastructure, OIDC authentication, course-scoped
membership, direct PDF upload, page-aware ingestion, PostgreSQL FTS and atomic corpus activation.
Ingestion runs in a separate worker through a PostgreSQL outbox and Redis Streams, with
idempotent execution, leases, bounded retries and recovery after interruption.

## Prerequisites

- Node.js 22 and npm 10
- Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/)
- Docker with Compose v2

## Clean checkout to green checks

Run these commands from the repository root:

```powershell
Copy-Item .env.example .env
npm install
uv sync --cache-dir .uv-cache
docker compose --env-file .env -f infra/compose.yaml up -d --wait
uv run --cache-dir .uv-cache alembic -c apps/api/alembic.ini upgrade head
docker compose -f infra/compose.test.yaml up -d --wait
docker compose --env-file .env -f infra/compose.yaml --profile worker build worker
uv run --cache-dir .uv-cache pytest tests/unit
npm run lint:web
npm run test:web
uv run --cache-dir .uv-cache ruff check apps/api/src scripts tests
uv run --cache-dir .uv-cache pytest
npm run build:web
docker compose --env-file .env -f infra/compose.yaml config --quiet
```

The integration suite uses `infra/compose.test.yaml` on ports 55432 (PostgreSQL), 56379
(Redis), and 59000 (MinIO). Each run creates a separate database, bucket and stream and
cleans only those generated resources. Its destructive migration rebuild never targets `.env`
or the development database. A Docker-worker test restarts the dedicated test services and
uses the built `docta-worker:latest` image; do not run multiple integration suites concurrently.
Unit tests do not need these services. If Windows denies the
default pytest temporary directory, append `--basetemp=.pytest_cache/local-test-temp`.

## Run locally

Start PostgreSQL, Redis and MinIO first, and apply pending migrations. When upgrading from
Increment 2, stop the old API and any workers before migration 0006:

```powershell
docker compose --env-file .env -f infra/compose.yaml up -d --wait
uv run --cache-dir .uv-cache alembic -c apps/api/alembic.ini upgrade head
```

Then use three terminals (API, web, worker):

```powershell
uv run --cache-dir .uv-cache uvicorn docta_api.main:app --app-dir apps/api/src --env-file .env --host 127.0.0.1 --port 8000
```

```powershell
npm run dev:web
```

```powershell
uv run --cache-dir .uv-cache python -m docta_api.worker
```

Alternatively, run the worker in Docker after migrations, instead of the third terminal:

```powershell
docker compose --env-file .env -f infra/compose.yaml --profile worker up -d --build worker
```

The worker image installs from `uv.lock`, runs as a non-root user, and restarts unless stopped.
Its build context excludes `.env` and all files except package metadata and API source.
See the [ingestion operations runbook](docs/runbooks/ingestion.md) for recovery and diagnostics.

Open `http://127.0.0.1:3000`. Direct service probes are available at:

- API liveness: `http://127.0.0.1:8000/api/v1/health/live`
- API readiness: `http://127.0.0.1:8000/api/v1/health/ready`
- Web liveness: `http://127.0.0.1:3000/api/health`
- MinIO console: `http://127.0.0.1:9001`

Authenticated course operations are available at:

- `POST /api/v1/courses` creates a course and atomically makes the authenticated identity its
  teacher.
- `GET /api/v1/courses/{course_id}` returns a course only when the authenticated identity is a
  member. Missing and unauthorized courses deliberately have the same safe response.
- `POST /api/v1/courses/{course_id}/documents/uploads` creates an immutable document version and
  returns a short-lived presigned PUT. It requires `Idempotency-Key`.
- `POST /api/v1/courses/{course_id}/documents/{document_id}/versions/{version_id}/complete`
  validates the object and commits ingestion/outbox together, returning `202` with `QUEUED`.
  It also requires `Idempotency-Key`. The worker completes indexing asynchronously.
- `GET /api/v1/courses/{course_id}/documents/{document_id}/versions/{version_id}` returns the
  durable processing state and safe failure code.
- `POST /api/v1/courses/{course_id}/corpus/activate` atomically activates only a `READY` corpus
  when `expected_course_version` still matches.

Configure `DOCTA_OIDC_ISSUER`, `DOCTA_OIDC_AUDIENCE`, and `DOCTA_OIDC_JWKS_URL` together to use
these endpoints. Only RS256 bearer tokens with valid signature, issuer, audience, subject, issued
time, and expiration are accepted. Production configuration fails validation when OIDC is absent;
development also fails closed on authenticated endpoints rather than trusting a local identity
header.

### Development bearer token

In the local ZITADEL application, register `http://127.0.0.1:8765/callback` as a redirect URI and
set **Token Type** to **JWT** under Token Settings. Copy its OIDC **Client ID** to
`DOCTA_DEV_OIDC_CLIENT_ID` in `.env`; the Application ID is conceptually different and must not be
assumed to be the client identifier. Then run:

```powershell
uv run python scripts/get_dev_token.py
```

The helper uses OIDC Discovery and Authorization Code + PKCE, opens the browser with
`fernando.dev` as the login hint, validates the callback state, and verifies the signed access
token's issuer, expiry, and Project ID audience through the same JWT verifier as FastAPI. It prints
the token to standard output and does not save it. Use it as `Authorization: Bearer <token>`.

To create a development course, upload a text-based PDF, index it, and activate its corpus in one
step, run:

```powershell
uv run python scripts/upload_dev_pdf.py .\ejemplo.pdf --course-title "Curso de prueba"
```

The command obtains a fresh token through the browser. To reuse a token already held in the current
PowerShell session without writing it to a file, set it only for the command and remove it afterward:

```powershell
$env:DOCTA_DEV_ACCESS_TOKEN = $Token
try {
    uv run python scripts/upload_dev_pdf.py .\ejemplo.pdf
} finally {
    Remove-Item Env:\DOCTA_DEV_ACCESS_TOKEN
}
```

The upload command forwards the exact presigned headers returned by FastAPI, polls the existing
document version for up to ten minutes, and activates only after indexing. It never prints the
token or presigned URL. If the wait expires, it prints the stable identifiers to query; it does
not upload another copy.

The object-storage adapter uses `DOCTA_S3_ENDPOINT_URL`, `DOCTA_S3_ACCESS_KEY`,
`DOCTA_S3_SECRET_KEY`, `DOCTA_S3_BUCKET`, and `DOCTA_S3_REGION`. Upload, page and chunk limits are
configured by the remaining `DOCTA_*` values in `.env.example`. PDF bytes go directly from the
client to MinIO; FastAPI only creates the authorization and later verifies the stored object.

If a local port is already occupied, change `POSTGRES_PORT`, `MINIO_API_PORT`, and
`MINIO_CONSOLE_PORT` in `.env`, together with the matching ports in `DOCTA_DATABASE_URL` and
`DOCTA_MINIO_HEALTH_URL`.

Stop local infrastructure without deleting durable volumes:

```powershell
docker compose --env-file .env -f infra/compose.yaml down
```

## Current boundary

Conversation durability, online retrieval, pedagogical generation and citations are not
implemented yet. Ingestion uses a transactional `JobDispatcher`, Redis Streams and a separate
worker; PostgreSQL owns job state and the outbox. Redis unavailability leaves confirmed work
queued and does not trigger inline processing. API readiness covers PostgreSQL and object
storage because it can accept durable work while Redis is unavailable; it does not assert
that a worker is healthy. Worker failures are visible in structured logs and database job state.
[ADR 0001](docs/adr/0001-ingestion-durability-and-response-delivery.md) records the scope.
SSE is accepted for Increment 3; LiteLLM and the tutor model await evaluation. OCR, vector
retrieval and model providers remain deliberately absent.
