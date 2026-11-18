"""SQL building blocks shared by the passkey flows, the CLI and the scheduler.

Lock order everywhere (docs/auth-passkey-design.md AUTH-4, 5.6):
profile -> enrollment token -> sessions -> credentials -> ceremony.
"""

from datetime import timedelta

import psycopg.errors

from app import clock
from app.auth import ABSOLUTE, IDLE
from app.problems import Problem
from app.security import new_secret, token_hash

CEREMONY_TTL = timedelta(minutes=5)
MAX_PASSKEYS = 10
MAX_SESSIONS = 20
MAX_CEREMONIES_PER_SESSION = 5
OPTIONS_BUDGET = 10


class Fail(Exception):
    """A controlled verification failure.

    The surrounding transaction rolls back; the caller then consumes the ceremony and writes
    `events` in a new transaction before answering with `problem`. Unexpected exceptions do not
    use this path, so a crash leaves the ceremony usable.
    """

    def __init__(self, problem: Problem, events=()):
        super().__init__(problem.code)
        self.problem = problem
        self.events = list(events)


async def record_event(
    db,
    event,
    *,
    actor="user",
    user_id=None,
    session_id=None,
    credential_id=None,
    token_id=None,
    now=None,
):
    await db.execute(
        "INSERT INTO auth_events (actor, user_id, event, session_id, credential_id, "
        "enrollment_token_id, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (actor, user_id, event, session_id, credential_id, token_id, now or clock.now()),
    )


async def lock_profile(db, user_id):
    return await db.one("SELECT * FROM profiles WHERE user_id = %s FOR UPDATE", (user_id,))


async def lock_token(db, token_id):
    return await db.one("SELECT * FROM enrollment_tokens WHERE id = %s FOR UPDATE", (token_id,))


async def lock_sessions(db, user_id):
    return await db.all(
        "SELECT * FROM auth_sessions WHERE user_id = %s AND revoked_at IS NULL "
        "ORDER BY id FOR UPDATE",
        (user_id,),
    )


async def lock_credentials(db, user_id):
    return await db.all(
        "SELECT * FROM passkey_credentials WHERE user_id = %s ORDER BY id FOR UPDATE", (user_id,)
    )


async def lock_ceremony(db, ceremony_id):
    return await db.one(
        "SELECT * FROM webauthn_ceremonies WHERE id = %s FOR UPDATE", (ceremony_id,)
    )


def ceremony_usable(row, now):
    return row is not None and row["consumed_at"] is None and row["expires_at"] > now


def active_credentials(credentials):
    return [c for c in credentials if c["revoked_at"] is None]


async def create_ceremony(
    db,
    now,
    *,
    purpose,
    challenge,
    binding_hash,
    user_id=None,
    pending_user_id=None,
    token_id=None,
    session_id=None,
    display_name=None,
):
    if session_id is not None:
        # Serialize on profile then session (design 5.6 order) so concurrent options cannot both
        # retire the same oldest ceremony and leave more than five open.
        await lock_profile(db, user_id)
        await db.execute("SELECT 1 FROM auth_sessions WHERE id = %s FOR UPDATE", (session_id,))
        # At most 5 unconsumed ceremonies per session: retire the oldest beyond the cap.
        await db.execute(
            "UPDATE webauthn_ceremonies SET consumed_at = %s WHERE id IN ("
            "SELECT id FROM webauthn_ceremonies WHERE session_id = %s AND consumed_at IS NULL "
            "AND expires_at > %s ORDER BY created_at DESC, id OFFSET %s)",
            (now, session_id, now, MAX_CEREMONIES_PER_SESSION - 1),
        )
    row = await db.one(
        "INSERT INTO webauthn_ceremonies (purpose, challenge, browser_binding_hash, user_id, "
        "pending_user_id, enrollment_token_id, session_id, display_name, expires_at, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            purpose,
            challenge,
            binding_hash,
            user_id,
            pending_user_id,
            token_id,
            session_id,
            display_name,
            now + CEREMONY_TTL,
            now,
        ),
    )
    return row["id"]


async def consume_ceremony(db, ceremony_id, now):
    await db.execute(
        "UPDATE webauthn_ceremonies SET consumed_at = %s WHERE id = %s AND consumed_at IS NULL",
        (now, ceremony_id),
    )


async def revoke_sessions(db, user_id, reason, now, *, except_id=None, credential_id=None):
    """Revoke the user's active sessions (callers hold the sessions lock)."""
    query = (
        "UPDATE auth_sessions SET revoked_at = %s, revoke_reason = %s "
        "WHERE user_id = %s AND revoked_at IS NULL"
    )
    params = [now, reason, user_id]
    if except_id is not None:
        query += " AND id <> %s"
        params.append(except_id)
    if credential_id is not None:
        query += " AND credential_id = %s"
        params.append(credential_id)
    return await db.all(query + " RETURNING id, credential_id", tuple(params))


async def create_session(db, user_id, credential_id, now, *, kind="passkey"):
    """New session with a fresh token. The caller holds the user's sessions lock."""
    active = await db.all(
        "SELECT id FROM auth_sessions WHERE user_id = %s AND revoked_at IS NULL "
        "AND idle_expires_at > %s AND absolute_expires_at > %s ORDER BY created_at, id",
        (user_id, now, now),
    )
    for old in active[: max(0, len(active) - MAX_SESSIONS + 1)]:
        await db.execute(
            "UPDATE auth_sessions SET revoked_at = %s, revoke_reason = 'limit' WHERE id = %s",
            (now, old["id"]),
        )
    token = new_secret()
    absolute = now + ABSOLUTE
    row = await db.one(
        "INSERT INTO auth_sessions (user_id, token_hash, kind, credential_id, last_seen_at, "
        "idle_expires_at, absolute_expires_at, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id",
        (user_id, token_hash(token), kind, credential_id, now, now + IDLE, absolute, now),
    )
    return token, row["id"], int((absolute - now).total_seconds())


async def insert_credential(db, user_id, credential, label, now):
    try:
        row = await db.one(
            "INSERT INTO passkey_credentials (user_id, credential_id, public_key, sign_count, "
            "transports, aaguid, backup_eligible, backup_state, label, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                user_id,
                credential.credential_id,
                credential.public_key,
                credential.sign_count,
                credential.transports,
                credential.aaguid,
                credential.backup_eligible,
                credential.backup_state,
                label,
                now,
            ),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise Fail(Problem(409, "credential_exists")) from exc
    return row["id"]


async def credential_exists(db, credential_id):
    row = await db.one(
        "SELECT 1 AS present FROM passkey_credentials WHERE credential_id = %s", (credential_id,)
    )
    return row is not None
