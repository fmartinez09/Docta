# Docta

Docta is being built as a trustworthy pedagogical RAG tutor. This repository currently contains
Increments 0 through 2 of the [walking skeleton](docs/WALKING_SKELETON.md): an executable Next.js
web app, a FastAPI boundary, PostgreSQL/MinIO infrastructure, OIDC authentication, course-scoped
membership, direct PDF upload, page-aware ingestion, PostgreSQL FTS and atomic corpus activation.

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
uv run --cache-dir .uv-cache pytest tests/unit
npm run lint:web
npm run test:web
uv run --cache-dir .uv-cache ruff check apps/api/src scripts tests
uv run --cache-dir .uv-cache pytest
npm run build:web
docker compose --env-file .env -f infra/compose.yaml config --quiet
```

The integration suite expects the Compose dependencies to be healthy. It applies the migration
from a clean Alembic baseline and verifies the real API readiness path against PostgreSQL and
MinIO.

## Run locally

Start PostgreSQL and MinIO first, and apply pending migrations:

```powershell
docker compose --env-file .env -f infra/compose.yaml up -d --wait
uv run --cache-dir .uv-cache alembic -c apps/api/alembic.ini upgrade head
```

Then use two terminals:

```powershell
uv run --cache-dir .uv-cache uvicorn docta_api.main:app --app-dir apps/api/src --env-file .env --host 127.0.0.1 --port 8000
```

```powershell
npm run dev:web
```

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
  validates the object and dispatches ingestion. It also requires `Idempotency-Key`.
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

The upload command forwards the exact presigned headers returned by FastAPI and never prints the
token or the presigned URL.

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
implemented yet. Ingestion currently uses an inline `JobDispatcher` behind a replaceable port, as
per the original walking-skeleton baseline; PostgreSQL remains the durable job state.
[ADR 0001](docs/adr/0001-ingestion-durability-and-response-delivery.md) schedules Redis
Streams/outbox and crash recovery in Increment 2B, before conversational RAG in Increment 3.
SSE is accepted for Increment 3; LiteLLM and the tutor model await evaluation. OCR, vector
retrieval and model providers remain deliberately absent.
