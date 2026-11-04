# Docta

Docta is a course-scoped pedagogical RAG tutor. It is a modular monolith with a Next.js web
application/BFF, a FastAPI API, PostgreSQL, RustFS, and a separate ingestion worker that uses
Redis Streams. ZITADEL provides local identity and OIDC tokens; Docta remains responsible for
course authorization, memberships, and private conversations.

Phase 0 (the PDF-to-chat path) is implemented. Tutor quality has not been established yet. The
next planned slice is an offline evaluation harness and reviewed dataset. See
[the architecture and decision record](docs/ARCHITECTURE.md) for implemented contracts and
[the engineering rules](AGENTS.md) for repository constraints.

## Recommended development environment

The dev container is the canonical development environment. It provides the pinned Python,
uv, Node/npm, Docker CLI/Compose, Git, and Chromium versions used by the repository. Keep the
checkout in the Linux filesystem when using WSL2; Windows-mounted dependency and Docker build
caches can have incompatible permissions.

The host needs Docker Engine or Docker Desktop with Compose 2.24.4 or newer. With Docker
Desktop, enable the WSL distribution under **Resources → WSL Integration**. Host networking is
used so the browser callback, OIDC issuer, and signed RustFS URLs use the same loopback addresses.

Open a clone in VS Code with **Dev Containers: Reopen in Container**, or start it from a host
terminal with the Dev Containers CLI:

```bash
devcontainer up --workspace-folder /path/to/Docta
devcontainer exec --workspace-folder /path/to/Docta npm run dev:setup
```

For a linked Git worktree, create it with relative Git paths and make the repository's common
Git directory available to the container. When using the CLI, pass
`--mount-git-worktree-common-dir` to `devcontainer up`. This keeps branch switching and the
repository's shared Git metadata available from the container. VS Code normally forwards the
host Git identity and credentials; with the CLI, configure `git user.name`/`user.email` and your
HTTPS credential helper or SSH agent in the container before committing or pushing. Worktree
commands are examples of host setup; project commands below always run inside the container.

For example, from the main checkout:

```bash
git worktree add --relative-paths ../docta-feature feature
devcontainer up --workspace-folder ../docta-feature --mount-git-worktree-common-dir
```

Every checkout uses the same private development profile on this Docker host:

```text
DOCTA_DEV_HOME=/var/lib/docta-dev
named volume: docta-dev-state
```

The profile contains the managed environment at `/var/lib/docta-dev/.env` and private identity
state under `/var/lib/docta-dev/state/`. It is deliberately outside the checkout so a new clone
or worktree does not generate credentials for data that already exists. Only one development
runtime may use the profile at a time. A file lock is inherited by the API, worker, and web
processes; stop the current runtime before switching branches or worktrees.

The container's first `postCreateCommand` installs locked dependencies and Chromium. The
`postStartCommand` is read-only: it prints status and the next command, but does not provision
ZITADEL, migrate PostgreSQL, start application servers, or ask for secrets.

RustFS uses a new volume; existing MinIO objects are not imported. Existing managed profiles
retain their storage credentials and ports through legacy aliases, without rewriting the profile.
For native Compose, update the ignored root `.env` using `.env.example` and set the RustFS
credentials and `DOCTA_STORAGE_HEALTH_URL`; the native and managed stacks remain separate.

## Single-VM deployment

`infra/compose.vm.yaml` prepares PostgreSQL, Redis, RustFS, migrations, API, worker, web and a
Caddy HTTPS proxy on one Linux VM. This is an initial deployment path, not a verified production
environment: public DNS/certificate issuance, a real identity provider, backups and restore
have not been tested. Use a dedicated VM and protect all persistent volumes and private files.

Prerequisites: Docker Engine with Compose 2.24.4+, the Dev Containers CLI, two distinct DNS names
pointing to the VM, inbound TCP 80/443, and an existing HTTPS OIDC provider. Register a public Authorization
Code + PKCE client with callback `https://<web-domain>/auth/callback`; obtain the issuer, JWKS
URL, audience/project ID and client ID. The default scopes target ZITADEL. Other providers may
need `DOCTA_WEB_OIDC_SCOPES` in the optional `.env.vm.web` file.

From the checkout on the VM, start the dev container, initialize the private profile inside it,
and start the stack:

```bash
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . python3 scripts/init_vm.py \
  --web-domain app.example.com --storage-domain storage.example.com \
  --issuer https://identity.example.com --jwks https://identity.example.com/oauth/v2/keys \
  --audience YOUR_PROJECT_ID --client-id YOUR_PUBLIC_CLIENT_ID
devcontainer exec --workspace-folder . docker compose --env-file .env.vm -f infra/compose.vm.yaml up -d --build --wait
```

Replace every example with real values. The initializer generates credentials once in `.env.vm`
with owner-only permissions and refuses to replace an existing profile or silently accept changed
domain/identity settings. Save the file and volumes securely; losing them prevents recovery. The
proxy obtains certificates automatically when DNS and ports are reachable. Only the proxy exposes
ports; storage, databases and the API use the private Compose network. The API signs upload URLs
for the public storage hostname, and the proxy preserves the host and path. Proxy error logs omit
request and error details to avoid recording signed URLs or authorization callback data.

For an optional model, configure `DOCTA_TUTOR_*` in an owner-only `.env.vm.api`. Dense retrieval
requires matching `DOCTA_EMBED_*` settings in both `.env.vm.api` and `.env.vm.worker`, plus the
API retrieval mode and distance threshold. These files are ignored by Git. Restart with the same
Compose command after configuration changes. Provision a backup and test restoration before
depending on VM data; this repository does not yet automate that operation.

## Two development flows

Run all commands in the following sections in a terminal inside the dev container.

### First setup

From a fresh clone, run:

```bash
npm run dev:setup
npm run dev:all
```

If no managed profile exists, `dev:setup` opens the interactive configuration assistant first.
For a new local profile, `npm run dev:setup -- --defaults` selects generated credentials without
prompting and stores them privately. Existing profiles are never replaced.

After configuration it starts the isolated ZITADEL/Login V2 stack, provisions Docta's public
OIDC Web application, starts PostgreSQL, Redis, and RustFS, and applies migrations. It does not
start the API, worker, or web development servers; `dev:all` does that after setup succeeds.
No LLM request is made during setup.

If a checkout already contains an old managed `.devcontainer/.env`, setup imports those existing
credentials and identity into the shared profile. If the ZITADEL database already exists, setup
reuses its administrator credentials. If that database is absent, setup asks whether the new IAM
instance should reuse the imported administrator or use a new login and password; retained Docta
data must first have its matching IAM data restored. `dev:status` shows which profile is active.
Choosing new infrastructure database/storage credentials requires a genuinely new environment
with no existing Docta data volumes; editing imported passwords does not change passwords held
by running services.

The assistant is intentionally developer-controlled. The generated values are defaults, not
mandatory credentials. Press Enter to accept a displayed default. Secret prompts are hidden;
press Enter there to keep the generated secret. You may choose your own administrator login and
password, database roles and passwords, RustFS credentials, ZITADEL database credentials, IAM
organization/project/application names, service-account names, and local ports. The administrator
password must satisfy ZITADEL's local policy. Values are validated before the private profile is
published.

The first-run defaults are:

| Setting | Default |
| --- | --- |
| ZITADEL administrator | `developer-<suffix>@docta.local`, with a generated hidden password |
| Docta PostgreSQL role/database | `docta_<suffix>` / `docta_<suffix>`, with a generated password |
| ZITADEL PostgreSQL role/database | `iam_<suffix>` / `iam_<suffix>`, with a generated password |
| RustFS access key/bucket | `docta-<suffix>` / `docta-documents-<suffix>`, with a generated secret key |
| ZITADEL organization/project/application | `Docta Development <suffix>` / `Docta Dev <suffix>` / `Docta PKCE <suffix>` |
| Provisioning and Login V2 accounts | `bootstrap-<suffix>` / `login-<suffix>` |
| Tutor | `none` (no provider, model, or API key is selected) |
| Embeddings | `none` (no endpoint, model, dimensions, or API key is selected) |
| Web / API | `http://127.0.0.1:13000` / `http://127.0.0.1:18000` |
| ZITADEL issuer / console | `http://localhost:18080` / `http://localhost:18080/ui/console` |
| Helper callback | `http://127.0.0.1:18765/callback` |
| PostgreSQL / Redis | `15432` / `16379` |
| RustFS API / console | `19000` / `19001` |

`<suffix>` is generated for the profile. It prevents accidental name collisions when creating a
new environment; it is not a password. The wizard can also generate all infrastructure values
without asking for custom names. Keep the resulting values private.

### Daily work

After setup, reopening the container does not repeat setup. Start the existing environment with:

```bash
npm run dev:all
```

This command starts the existing managed ZITADEL, PostgreSQL, Redis, and RustFS containers,
checks public OIDC discovery and signing keys, verifies that the database schema matches the
current checkout, and runs the API, ingestion worker, and web application. It does not create
IAM resources and does not run migrations. If containers are missing or the schema belongs to a
different branch, it stops with an actionable error; run `npm run dev:setup` from a compatible
checkout.
Already healthy containers are reused without a Compose restart. A failed start reports which
stack and service state needs inspection, without printing private Docker logs.

The three application processes stay attached to this command. Press Ctrl+C to stop them, then
stop the managed infrastructure when needed:

```bash
npm run dev:stop
```

`dev:stop` stops containers but retains named volumes, credentials, identity state, migrations,
documents, and Redis data. It does not delete development data.

The lifecycle commands are:

| Command | Effect |
| --- | --- |
| `npm run dev:configure` | Interactive configuration only. It never starts services, runs migrations, or calls model providers. On an existing profile it preserves infrastructure credentials and identity and edits tutor, embedding, and retrieval settings. |
| `npm run dev:setup` | First-run configuration when needed, ZITADEL provisioning, infrastructure startup, and migrations. |
| `npm run dev:all` | Daily startup and schema/OIDC checks, then API + worker + web. No provisioning or migrations. |
| `npm run dev:status` | Read-only configuration and Docker service status. It never changes identity or data. |
| `npm run dev:stop` | Stop managed infrastructure and retain its volumes. |
| `npm run dev:recover-identity` | Explicit recovery after both IAM and Docta data volumes were intentionally deleted; see [identity recovery](#identity-recovery). |

### Changing branches or worktrees

Use one active runtime and alternate checkouts only after stopping it:

```bash
# terminal running dev:all
Ctrl+C
npm run dev:stop

# switch branch or worktree, then reopen that checkout in the same dev-container setup
npm run dev:all
```

The shared profile and the managed Compose projects are host-wide. A second checkout must reuse
the same credentials and identity IDs; it must not create a second IAM or database environment.
The lock rejects concurrent setup, configuration, stop, or application startup. If the checkout
contains older migrations, `dev:all` reports the mismatch. Stop the runtime and run setup from a
compatible branch; migrations are upgrade-only and never downgraded automatically.

## ZITADEL and Docta authorization

ZITADEL is the identity provider. Setup creates or reconciles one local project and a public Web
OIDC application using Authorization Code + PKCE and JWT access tokens. The web BFF exchanges the
code server-side and stores an encrypted HttpOnly session. There is no client secret in the web
application.

The Docta API validates the issuer, project audience, signature, and expiry, then makes the
authorization decision from its own course and membership data. ZITADEL identity does not grant
access to a course, and a teacher role does not grant access to another user's conversation. The
OIDC Project ID is the API audience. The OIDC Client ID is distinct from the ZITADEL Application
ID. The helper callback and web callback are registered during setup.

The provisioning PAT is used only by the setup/provisioning process. Compose still needs each
service's own database, storage, or IAM startup credentials when it starts those services. The
API, worker, and web processes receive only the settings needed by their boundary. Do not copy the
managed environment into a root `.env`, logs, bug reports, or an API collection.

### Credentials and rotation

The profile is private and contains:

| Location / setting | Purpose |
| --- | --- |
| `/var/lib/docta-dev/.env` | Managed local credentials, URLs, tutor settings, and the generated OIDC IDs |
| `/var/lib/docta-dev/state/identity.json` | ZITADEL instance, project, application, and pending-mutation state |
| `/var/lib/docta-dev/state/admin.pat` | Local provisioning token used by setup; never share it |
| `DOCTA_DEV_IAM_MASTERKEY` | ZITADEL encryption key; preserve it with the IAM database |
| `DOCTA_WEB_SESSION_SECRET` | Docta browser-session key; preserve it for existing sessions |

Changing a value in `.env` does not rotate a password already stored by PostgreSQL or ZITADEL.
Restore the original managed profile when reusing existing volumes. Rotate credentials in
the service's administration flow, then update the matching private configuration. The initial
provisioning PAT expires according to `DOCTA_DEV_PAT_EXPIRATION` (one year by default); changing
that field later does not renew an already-issued token. Do not regenerate infrastructure
credentials just because a checkout was rebuilt.
For an existing ZITADEL database, change the human administrator password in the ZITADEL
Console. `DOCTA_DEV_LOGIN_PASSWORD` is a first-instance input; changing it in the profile or
rerunning `dev:setup` does not change that user's password inside ZITADEL.

If the ignored profile was lost while development volumes remain, stop and restore the matching
`.env` and `state/` before running setup. Setup refuses to generate new credentials over known
development volumes. An old managed `.devcontainer/.env` profile can be imported when the shared
profile is empty; the source is preserved and is never overwritten.

For a private environment backup outside the checkout, restore the environment first and then
restore its matching `state/` directory into `/var/lib/docta-dev/state/`:

```bash
npm run dev:configure -- --from-env /private/path/original-managed.env
```

The import refuses to replace an existing shared profile and never generates credentials.

### Identity recovery

If both the IAM and Docta data volumes were intentionally deleted, saved ZITADEL IDs refer to the
old instance. After confirming that the Docta PostgreSQL, RustFS, and Redis volumes are absent,
run:

```bash
npm run dev:recover-identity
```

Recovery archives the private inputs, preserves tutor settings and infrastructure credentials,
creates a new local identity, and applies migrations. It refuses to run when application data is
still present or when the previous project state is ambiguous. Deleted data cannot be restored by
this command. When creating a new ZITADEL database from an existing profile, it also asks
whether to reuse or change the initial administrator credentials.

## Application addresses and first exercise

With `npm run dev:all` running, the default addresses are:

| Component | Address |
| --- | --- |
| Web | <http://127.0.0.1:13000> |
| API docs | <http://127.0.0.1:18000/docs> |
| API liveness/readiness | `/api/v1/health/live` and `/api/v1/health/ready` on the API address |
| ZITADEL console | <http://localhost:18080/ui/console> |
| RustFS API / console | <http://127.0.0.1:19000> / <http://127.0.0.1:19001> |

Sign in, create a course, upload a digital text PDF, wait for indexing, and publish the indexed
corpus. Ask a question supported by the material and inspect the citations. Try an unsupported
question separately to observe abstention. Only digital text PDFs are supported; invalid,
encrypted, and image-only PDFs are rejected explicitly.

For direct API exercises, keep tokens private and use the managed environment wrapper:

```bash
uv run --frozen python -m scripts.dev run -- uv run python scripts/get_dev_token.py
uv run --frozen python -m scripts.dev run -- uv run python scripts/upload_dev_pdf.py ./example.pdf
```

The token helper uses Authorization Code + PKCE and validates the configured issuer, audience,
signature, and expiry. The upload helper authenticates, uploads, polls indexing, and activates
only after the corpus is ready. A question POST requires an `Idempotency-Key`; the exact question
is persisted before retrieval or model calls. A same-key retry observes the same execution, while
changed text conflicts. Do not infer a completed answer from a disconnected SSE stream; inspect
the durable message status or conversation history.

## Tutor configuration

The tutor is optional. Setup defaults to `none`; it never invents a provider, model, or API key.
To configure or change it:

```bash
npm run dev:configure
npm run dev:all
```

The assistant supports `google`, `llama_cpp`, `unsloth`, `custom`, and `none`. A provider preset
does not select a developer model or credential: enter the full endpoint, model/server alias, and
hidden API key. Custom services also ask for the provider and schema profile. Local services still
need a nonempty key value for the current adapter contract.
Enter the model ID in the model prompt, then the credential in the hidden API key prompt. When
editing an existing profile with the same endpoint, Enter at the API key prompt keeps its saved
key; typing a new key replaces it in the private profile.

The default limits are a 45-second tutor timeout, a 90-second message deadline, and a 2,000-token
completion budget. The tutor timeout must remain below the message deadline. Restart `dev:all`
after changing configuration. The API keeps strict response and citation validation; provider
failures are safe failure codes, and an unsupported or insufficiently evidenced question abstains.
There is no general-knowledge fallback, automatic provider retry, tool loop, or unvalidated token
stream.

Useful failure codes include:

| Code / observation | Meaning |
| --- | --- |
| `MODEL_NOT_CONFIGURED` | Configure endpoint, model, and key together, then restart. |
| `MODEL_UNAVAILABLE` | The selected provider timed out or returned an HTTP failure. |
| `MODEL_OUTPUT_INVALID` | The response was refused, truncated, malformed, or cited unsupported text. |
| `COURSE_CORPUS_NOT_READY` | Publish an indexed corpus and submit a new question. |
| `RETRIEVAL_SCOPE_INVALID` | Authorization or course/corpus binding failed closed; inspect scope rather than loosening filters. |
| `RETRIEVAL_UNAVAILABLE` | Dense retrieval could not embed the question (not retried); the question stays saved and the message is `failed`. |
| `RETRIEVAL_INDEX_MISSING` | Some indexed chunks lack embeddings for the configured embedding version; re-import the documents. |
| `EMBEDDING_UNAVAILABLE` | Ingestion: transient embedding failure; the job retries within its existing bounded retry. |
| `EMBEDDING_INVALID` | Ingestion: non-transient embedding failure; the document is marked `FAILED`. |
| `PROCESSING_INTERRUPTED` | Shutdown, deadline, or abandoned execution; the question remains durable and is not automatically retried. |

## Retrieval modes

Retrieval uses `DOCTA_RETRIEVAL_MODE=fts` by default (PostgreSQL full-text search). `dense` is
opt-in and ranks chunks of the active corpus by embedding similarity.

- `dense` requires the complete embedder: `DOCTA_EMBED_ENDPOINT_URL`, `DOCTA_EMBED_MODEL`,
  `DOCTA_EMBED_API_KEY` and `DOCTA_EMBED_DIMENSIONS`. Configure all four or none. It also needs
  `DOCTA_DENSE_MAX_DISTANCE`, the cosine-distance threshold.
- `DOCTA_EMBED_INPUT_TYPE=nvidia` sends `query` for questions and `passage` for documents. The
  default, `off`, sends no input type.
- `DOCTA_EMBED_TIMEOUT_SECONDS` defaults to 10. Free endpoints can take 3 to 16 seconds, so use
  at least 30. With `dense`, startup rejects a configuration where the embed timeout plus the
  tutor timeout is not shorter than the message timeout.
- Embeddings are computed during ingestion whenever an embedder is configured, in either mode.
  There is no backfill: to obtain embeddings for a document, upload and publish it again.
- With `dense`, an active corpus without embeddings for the configured embedding version fails
  with `RETRIEVAL_INDEX_MISSING`. Dense retrieval never falls back to FTS.
- The distance threshold is provisional. It depends on the embedding version (model, dimensions,
  and a hash of the templates and extra request options), so recalibrate it when the model,
  templates or input type change.
- Dense retrieval does not filter out questions the document does not answer. Abstention is the
  tutor's responsibility.
- `:free` model variants are rejected in production.
- Keys belong only in the private profile. In the dev container that is
  `/var/lib/docta-dev/.env`; use `npm run dev:configure`, keep the existing tutor, and select
  `configure` for embeddings. Enter the full embeddings endpoint, model, hidden API key,
  dimensions, timeout, batch size, input type, query/document templates and optional provisional
  threshold separately from the tutor. With an unchanged endpoint, Enter retains the saved key;
  a changed endpoint requires a key. Configuration makes no model request. Existing provider
  routing is retained only for the same endpoint. Never place keys in versioned files.
- Select `dense` as the retrieval mode in `npm run dev:configure` to opt in; `fts` remains the
  default. Dense requires the complete embedder and a calibrated distance threshold. The sum of
  embedding and tutor timeouts must be strictly less than the message deadline (at most 180s);
  the assistant asks for a compatible tutor timeout when necessary. Selecting dense without an
  embedder fails validation and preserves the private profile. Existing corpora need matching
  embeddings before dense retrieval can use them.
- `dev:all` runs both API and worker in the dev container, so they share the endpoint. A worker
  started separately with Compose has its own network namespace: `127.0.0.1` then refers to the
  worker container. Configure an address reachable by both processes for that deployment.
- The PostgreSQL image with pgvector is rebuilt by `npm run dev:setup` and by the checks.

## Ingestion and durability

The API authorizes a direct RustFS upload, verifies the object and checksum, and records the
document version and job in PostgreSQL. The worker extracts text with PyMuPDF, creates page-aware
chunks, and publishes a READY immutable corpus. Redis Streams transports the job; PostgreSQL's
outbox and job tables are canonical. Activation uses compare-and-swap and only succeeds for an
indexed corpus.

Conversation generation remains API-owned. It captures the authorized active corpus when the
question is accepted, stores the exact question before retrieval/model calls, persists evidence,
validates the complete response, and atomically commits citations. Message state is
`pending → completed|failed`; expired or terminal messages cannot be overwritten. Redis loss is
reconciled from PostgreSQL. Do not clear volumes, jobs, or stream state as a recovery shortcut.

The worker runs in the `dev:all` terminal. If it stops, stop the application and run
`npm run dev:all` again. Inspect the saved document-version and message failure codes and the
correlated service output; logs never include PDF text, prompts, answers, private reasoning,
credentials, presigned URLs, or raw provider payloads.

## Verification

Run checks inside the dev container. The short check uses deterministic fakes and does not require
the development services to be running:

```bash
npm run check:dev
```

It runs Python unit tests, Ruff, web lint/tests/build, and `git diff --check`. The full check adds
the worker image and serial PostgreSQL/RustFS/Redis integration and browser suites using isolated
test resources:

```bash
npm run check:dev -- --full
```

Run suites serially because some integration tests restart shared test services. These checks
exercise contracts and isolation; they do not establish real-model availability, pedagogical
quality, or human learning. The offline evaluation harness and reviewed dataset are not yet
implemented.

## Troubleshooting

Start with read-only status:

```bash
npm run dev:status
```

Common cases:

- **ZITADEL port already in use**: in VS Code's Ports panel, stop forwarding port `18080` if it
  is auto-forwarded, then rerun `npm run dev:setup`. Otherwise stop the conflicting listener.
  The dev container settings ignore auto-forwarding for the default IAM port after the container
  is rebuilt. For a customized IAM port, disable forwarding for that port as well.

- **“Run `npm run dev:configure` first”**: configure the private profile, then run setup.
- **Existing volumes but missing configuration**: restore the original `/var/lib/docta-dev/.env`
  and `state/` backup. Do not generate new credentials or delete volumes.
- **ZITADEL password authentication failed**: the IAM PostgreSQL volume has its original
  password. Restore the matching managed profile and `DOCTA_DEV_IAM_MASTERKEY`.
- **OIDC discovery or signing keys unavailable**: check Docker health and the issuer address; no
  identity mutation is performed by `dev:all`.
- **Cannot start a managed stack**: use `npm run dev:status` to see the affected service states,
  then inspect that stack's Docker health checks. The command preserves configuration and volumes.
- **Schema mismatch**: stop API/worker/web and run `npm run dev:setup` from a compatible branch.
  The setup flow applies upgrades and never downgrades a database.
- **Port already in use**: on a fresh profile choose custom ports in the assistant. Existing
  initialized ports are a contract; changing them requires an explicit service migration.
- **Another runtime owns the profile**: stop the running `dev:all`, `dev:setup`, or
  `dev:configure` process before using another checkout. The lock is released when its process
  exits; do not remove it by hand.
- **Dev Container cannot access a worktree**: use relative Git worktree paths and
  `--mount-git-worktree-common-dir`; reopen the intended checkout and keep one runtime active.
- **Commit or push fails in a CLI-created container**: configure Git identity and credentials in
  that container, or use VS Code's credential/SSH forwarding. The project setup does not store Git
  credentials in the Docta profile.
- **Docker is unavailable**: fix Docker/WSL integration on the host, then rerun the command inside
  the container. Development state is preserved when a stage fails.

Do not run project tooling on the host in the canonical workflow. Do not commit `.env`, the
`state/` directory, tokens, tutor keys, or private backups. Native Compose files and
`.env.example` remain for explicitly managed advanced setups; they are separate from the managed
dev-container profile and must never be mixed with its volumes.
