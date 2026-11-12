# API and scheduler

FastAPI modules implement the Phase 0 `/api/v1` and `/api/internal` contract.
`app.main:handler` adapts Function URL events with Mangum. EventBridge invokes
`app.scheduler.handler` directly; there is no scheduler HTTP endpoint.

Install and verify from `api/`:

```sh
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Integration tests start and stop a disposable local Postgres 16 container and
apply the actual `db/` Alembic migrations. They never use an ambient database URL.
Responses are checked against the contract, including the external BriefResult
schema. `uv run python scripts/export_contract.py` regenerates the packaged JSON
snapshot after an approved contract change; a test detects drift. No repository
docs, tests, scripts, `.env`, or agent configuration are copied into the image.

## Local stack

Use the root `.env` with project `brief-api` and ports 5632/8200/5373, per
AGENTS.md. Required configuration:

- `APP_ENV=local`, `AUTH_VERIFIER=dev`, `DEV_JWT_SECRET` (local signing key).
- `DATABASE_URL` (the compose `db` host when running in Docker). The existing
  `postgresql+asyncpg://` URL is accepted for compatibility, but the API uses
  Psycopg async. Standard `postgresql://` is also accepted.
- `OWNER_USER_ID=00000000-0000-4000-8000-000000000001` for the DB local seed;
  `OWNER_EMAIL` comes from `.env`. A missing owner ID rejects subscription jobs.
- `INTERNAL_SERVICE_TOKEN`, shared only with the trusted worker.
- `WORKER_DISPATCHER=local`, `AGENT_RUNNER=stub` for the deterministic local
  adapter. This adapter claims jobs through the same queue service and persists
  complete reports without invoking a model.

From the root: `make migrate`, `make seed`, then `make up`. Scheduler and API
must start after migration; the local scheduler retries failures each interval.
To run immediately: `docker compose run --rm scheduler python -m app.scheduler`.
For a dev bearer token: `docker compose exec api python -m app.dev_token`.
This helper prints an ephemeral token; do not save or commit it. It issues only
the configured local owner's identity and has no HTTP endpoint. The frontend
must arrange its local sign-in adapter separately.

For a real local worker, set `LOCAL_WORKER_COMMAND` to an installed worker
executable and its arguments, and choose its runner via `AGENT_RUNNER`. The
command runs without a shell; the worker uses `API_BASE_URL` and
`INTERNAL_SERVICE_TOKEN` from its environment. The worker checkout/CLI must be
available in that runtime; the API image bundles only the deterministic stub.

## Authentication and production configuration

Production requires `APP_ENV=production`, `AUTH_VERIFIER=supabase`,
`SUPABASE_URL`, optional `SUPABASE_JWKS_URL`, `DATABASE_URL`,
`INTERNAL_SERVICE_TOKEN`, `OWNER_USER_ID`, `AGENT_RUNNER=claude_subscription`,
`WORKER_DISPATCHER=github`, `GITHUB_REPOSITORY`, and `GITHUB_DISPATCH_TOKEN`.
The dev verifier refuses startup outside local mode. JWT verification pins
issuer, authenticated audience/role, expiry and asymmetric RS256/ES256 keys.
Legacy shared-secret Supabase signing is intentionally unsupported: configure
asymmetric signing keys before M2. User JWTs and internal credentials are
accepted only by their respective route families.

Use the actual Supavisor transaction URL (port 6543) from Connect; do not infer
the hostname from the region. Psycopg uses `prepare_threshold=None`, closes
connections after each transaction, and requires TLS outside local mode.
Migrations retain the DB agent's separate direct/session connection setup.

GitHub dispatch targets `agent-worker.yml` on `main`, without inputs, using API
version `2026-03-10`. Scope a fine-grained token to this repository with Actions
write permission. No workflow dispatch, cloud access or deployment is performed
by the test suite. Infrastructure must provide CloudFront origin protection,
concurrency limits and the Lambda runtime packaging before cloud use.

## Queue behavior and limits

Scheduler ticks serialize each user's daily quota decision, preserve slot IDs,
skip nonexistent DST times and use the first occurrence of repeated times.
A slot is scheduled at most once per local date, even after its time is edited.
Queued jobs survive dispatch failures and are dispatched on the next tick.
Claims use `FOR UPDATE SKIP LOCKED`; the initial claim increments attempts.
Retryable failures allow two attempts total. Running leases expire after 35
minutes; the next scheduler tick requeues or fails them. Scheduler and claim
reject non-owner `claude_subscription` jobs, and result ingestion checks again.

Report, section, item, usage and success writes share one transaction. The API
checks result schema, source URL schemes, topic set, counts, ranks, runner,
date and locale. Free-form worker errors are reduced to a category before DB
storage. Agent settings remain locked in the POC. Report/item ownership is
checked by the backend independently of RLS.

Known contract limits: claim/result/fail have no attempt lease token, so trusted
workers must not submit after their lease expires. Result checks use the user's
current subscriptions, timezone and locale; changing those during a run can
reject the result, which the worker can report as a retryable failure. Persisted
request snapshots and attempt fencing need coordination with contracts and DB.
Live Supabase, Lambda, GitHub dispatch and browser integration remain M2/M1
integration checks respectively.
