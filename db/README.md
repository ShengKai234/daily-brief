# Database

Postgres 16 migrations derived from `contracts/schema.sql` at `6ada9a6`, with
portable SQL and no dependency on Supabase Auth tables. Alembic revision
`0002_security_slots` adds explicit direct-client denial and deferred slot
uniqueness; `0003_default_privileges` protects future tables from inherited
client grants. The historical migration is self-contained; changing a contract
does not silently change an existing revision. Revision `0004_profile_locale`
aligns `profiles.locale` with contract `c35e8de`: 2–35 characters on insert and
update. Existing invalid values make the upgrade fail without changing data;
correct those values before retrying. Downgrade removes only this length check.

From the repository root, with the worktree's git-ignored `.env` configured:

```sh
make migrate
make seed
```

`make migrate` starts the local compose database if necessary, builds the
migration image when absent, and upgrades an empty database to the current head.
After changing migration/image source, first run `docker compose build migrate`.
To migrate and seed in one command:

```sh
docker compose run --rm --build migrate init
```

The default container action is `migrate`; `seed` never runs automatically on
startup. A missing or invalid configuration fails without printing environment
values or database exceptions. Supply `DATABASE_URL` to the process; the host
CLI does not load `.env` files. Compose injects `.env` into the container.

For host development, export a `DATABASE_URL` pointing to the local compose
database at `127.0.0.1:5532` (the db worktree port), then:

```sh
cd db
uv sync --frozen
uv run python -m brief_db migrate
uv run python -m brief_db seed
uv run alembic current
```

Both `postgresql://` and `postgresql+asyncpg://` URLs are accepted. Migrations
use an async SQLAlchemy engine and `NullPool`. Use a direct connection or the
session pooler for hosted migrations; transaction-pooler compatibility is not
claimed for the migration engine. Keep database credentials in environment
variables; do not put a URL in `alembic.ini` or command history.

## Seed behavior

`OWNER_EMAIL` and an explicit `APP_ENV=local|production` are required. Emails are
trimmed and lowercased. The seed is transactional and repeatable.

- Every environment inserts the configured email into `allowed_emails`.
- Production creates no profile or synthetic auth subject. The API creates the
  profile from the verified sign-in subject and activates it through the allowlist.
- Local development creates active owner
  `00000000-0000-4000-8000-000000000001`, default agent settings, six topics and
  subscriptions (stock market, AI, information security, weather, Ruby/Python
  releases, travel), and one 07:00 slot using `Asia/Taipei`.
- Existing subscription preferences and slot times are preserved on reseeding.
  Changing the local owner email after initial seeding fails atomically.

The API dev verifier and local worker's `OWNER_USER_ID` must use this fixed UUID.
No production owner UUID is inferred from an email. Enforcing the subscription
runner's owner restriction remains an API/worker responsibility (DESIGN 3.1).

## Constraints and client access

Slot positions are 1–3 and unique per user, including disabled slots. Times must
be on the hour or half hour with no seconds. Position and local-time uniqueness
are `DEFERRABLE INITIALLY DEFERRED`: full-set replacements may temporarily swap
values but must be valid when the transaction commits. Update existing slots by
position and insert/delete only the difference to preserve historical references.
These deferred keys cannot be used as `ON CONFLICT` targets; use explicit updates
or a primary-key target instead.

Jobs have immediate uniqueness on `(user_id, slot_id, scheduled_for)`. Concurrent
schedulers can use that key with `ON CONFLICT DO NOTHING`. Scheduled jobs must
provide `slot_id`. Deleting slots sets historical job/report references to NULL;
NULL slot IDs remain distinct so deleting multiple slots with jobs scheduled at
the same instant succeeds. This is not an idempotency key for slotless jobs.

Every application table and `alembic_version` has RLS enabled with a restrictive
deny-all policy. PUBLIC table privileges are revoked, as are grants to `anon`
and `authenticated` when those roles exist, including TRUNCATE (which RLS does
not control). The API connects through a trusted table-owner or BYPASSRLS role
and must authorize every request. A Supabase service-role HTTP key is not a SQL
login. No JWT setting or Supabase-specific function is used in policies.

Revision `0003_default_privileges` also revokes default table grants from PUBLIC
and, when present, `anon` and `authenticated`. It removes both database-wide and
`public` schema defaults for the role running the migration: schema revokes alone
cannot override global grants. Defaults apply only to objects created by that
role, so future migrations must use the same role. Another table-creating role
needs equivalent defaults configured separately. Defaults for unrelated roles
are unchanged. See [Postgres default privileges](https://www.postgresql.org/docs/16/sql-alterdefaultprivileges.html).

Every new application table still needs RLS enabled, a restrictive deny policy,
and explicit client privilege revocation in its migration. Default privilege
revocation is an extra safeguard; it does not enable RLS or prevent a later
explicit grant from reopening access.

Downgrade removes application tables and the timestamp function; it preserves
the potentially pre-existing `pgcrypto` extension. Security downgrade does not
restore revoked client privileges or default grants. Use downgrade only on
disposable/local data unless a separate production migration plan has been reviewed.

## Verification

```sh
cd db
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests start an isolated Postgres 16 container bound to an ephemeral loopback
port, create a fresh database per test, and remove those databases/container on
exit. They ignore the ambient `DATABASE_URL` and never contact a cloud database.
Docker must be running. Test coverage includes the three-slot limit, time
granularity, deferred swaps, concurrent job deduplication, slot deletion history,
client grants/RLS (including future-table default grants), timestamp updates,
seed behavior and downgrade/upgrade.

The image copies only dependency metadata, migrations and the command package.
The per-service `.dockerignore` excludes other files by default, including docs,
agent configuration, tests, local environments and `.env` files.
