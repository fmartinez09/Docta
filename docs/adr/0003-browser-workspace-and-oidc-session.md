# ADR 0003 — Minimal browser workspace and OIDC session

Date: 2026-09-07  
Status: Implemented for Increment 4

## Context

The API already owns authorization, ingestion and durable conversations. Increment 4 needs
a teacher/student browser path. The architecture selects assistant-ui and Authorization Code
with PKCE, prohibits browser token storage, and leaves the session implementation open.

## Decision

1. Next.js is the browser's same-origin backend. Its route handlers perform public-client OIDC
   discovery, state validation and S256 PKCE exchange. FastAPI verifies the resulting RS256 access
   token before the web creates a session. The token stays in an AES-256-GCM encrypted, HttpOnly,
   SameSite=Lax cookie (Secure on HTTPS); the encryption secret is server-only. Cookies expire
   after at most one hour or the provider's shorter token lifetime. No refresh tokens are stored.
   Logout clears Docta cookies; it does not claim to terminate the global identity-provider session.
2. An allowlisted BFF forwards only supported JSON commands and history/SSE reads. Mutations
   require the configured Origin and an idempotency key. FastAPI remains the authorization
   authority for every request. Responses and authentication routes are not cached. The BFF
   bounds request bodies and never exposes tokens through client props or localStorage.
3. PDFs go directly from the browser to the existing presigned S3 contract. Local MinIO CORS
   permits the configured web origin. The UI observes durable ingestion state and explicitly
   publishes an indexed corpus using the existing conditional activation operation.
4. assistant-ui's external-store runtime and composer adapt to durable API messages. The UI
   renders plain text from committed history, never unvalidated SSE text. Lifecycle events show
   progress; polling recovers pending work after refresh. An interrupted send retains its key
   while the component remains mounted. A page reload recovers accepted messages through history;
   an unaccepted unsaved draft is not retained across reloads.
5. Add authorized discovery lists for courses, teacher document states, and the current member's
   own conversations. Lists currently return the newest 100 entries; full conversation messages
   remain paginated and are loaded completely. Enrollment and teachers reading students' private
   conversations remain outside this increment.
6. Migration 0008 adds a per-user course-creation idempotency record. The API accepts an optional
   key for compatibility with existing Increment 1 clients; the browser always supplies it. A
   row lock serializes retries, and reusing a key with a changed title returns 409. The existing
   publication boundary uses compare-and-swap; reconciliation shows whether activation succeeded
   if its response was lost, without introducing a second publication protocol.
7. Browser tests run the production web build against a real API, PostgreSQL, MinIO and worker.
   Only model generation and an ephemeral OIDC issuer are test doubles, composed exclusively in
   tests/e2e. Login still exercises discovery, PKCE, signed JWT verification and cookie creation.
   The fixture uses isolated resources and cannot target the development database.

## Consequences

Deployment must provide a registered web callback, public-client PKCE support, a stable shared
session secret, HTTPS, and an S3 endpoint reachable by the browser. Secret rotation invalidates
sessions. A revoked membership is denied immediately by the API; provider token revocation is
bounded by JWT expiry. Users sign in again after expiry. A large token that cannot fit safely in
a browser cookie fails login explicitly; a server-side session store is deferred until needed.
Real model compatibility and pedagogical quality remain a separate live acceptance gate.

During local verification, Unsloth's llama-server accepted the structured contract only when
`maxLength` was omitted from its sampling grammar. Add an explicit `llama_cpp` HTTP adapter
schema profile for this compatibility case; `standard` remains the default. All original
Pydantic bounds, byte/token limits and evidence validation remain mandatory locally. No automatic
provider retry or general free-text mode is introduced. This refines ADR 0002's replaceable
model adapter without changing the domain response contract.

The explicit `unsloth` provider profile additionally sets Studio's `enable_thinking`,
`enable_tools` and `mcp_enabled` to false, and sends matching `max_tokens` and
`max_completion_tokens`. These fields were verified against the configured server's published
OpenAPI contract. They do not affect the standard Chat Completions adapter. A real synthetic
request with Qwen3.5-9B completed in 5.6 seconds with one validated citation, using a 512-token
budget; the earlier request exceeded 45 seconds. This is a compatibility/latency smoke test,
not a pedagogical evaluation or an end-to-end production-browser validation.
