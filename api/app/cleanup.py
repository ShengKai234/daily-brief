"""Bounded cleanup of auth tables (docs/auth-passkey-design.md 4.3).

Retention is only housekeeping: validity is always decided at query time, so this never ends a
live grant. At most BATCH rows per table per tick, each under a statement timeout. Revoked
passkeys are never deleted (AUTH-6).
"""

from datetime import timedelta

BATCH = 1000
STATEMENT_TIMEOUT_MS = 5000

# (name, table, key, condition on a row alias `t`, retention)
RULES = (
    ("ceremonies", "webauthn_ceremonies", "id", "t.expires_at < %(cut)s", timedelta(hours=1)),
    (
        "sessions",
        "auth_sessions",
        "id",
        "least(coalesce(t.revoked_at, 'infinity'::timestamptz), t.idle_expires_at, "
        "t.absolute_expires_at) < %(cut)s",
        timedelta(days=30),
    ),
    (
        "tokens",
        "enrollment_tokens",
        "id",
        "coalesce(t.used_at, t.revoked_at, t.expires_at) < %(cut)s",
        timedelta(days=90),
    ),
    ("rate_limits", "auth_rate_limits", "ctid", "t.window_start < %(cut)s", timedelta(days=1)),
    ("events", "auth_events", "id", "t.created_at < %(cut)s", timedelta(days=90)),
)


async def run(database, now):
    deleted = {}
    for name, table, key, condition, keep in RULES:
        async with database.transaction() as db:
            await db.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
            # Identifiers come from the constant RULES above, never from input.
            result = await db.execute(
                f"DELETE FROM {table} WHERE {key} IN ("  # noqa: S608
                f"SELECT t.{key} FROM {table} t WHERE {condition} LIMIT %(batch)s)",
                {"cut": now - keep, "batch": BATCH},
            )
            deleted[name] = result.rowcount
    return deleted
