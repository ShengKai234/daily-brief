"""Admin CLI for enrollment and account control (docs/auth-passkey-design.md 6).

Run on Kai's machine: `uv run python -m app.auth_admin <command>`.
Connects with ADMIN_DATABASE_URL (role brief_admin). Every mutating command shows the target
environment and database host (never the password) and asks for confirmation. Token values are
printed only as the one-time enrollment link by the issue commands, never stored in clear text,
never logged. Locks follow the global order: profile -> tokens -> sessions.
"""

import argparse
import asyncio
import sys
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import UUID

from app import clock
from app.authdb import (
    create_session,
    lock_profile,
    lock_sessions,
    record_event,
    revoke_sessions,
)
from app.config import Settings
from app.database import Database
from app.security import new_secret, token_hash

DOC = "DESIGN 3.1 (docs/auth-passkey-design.md AUTH-14)"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db"}


class CliError(Exception):
    pass


def target(settings, url):
    parts = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    host = parts.hostname or "?"
    return f"environment={settings.app_env} database={host}:{parts.port or 5432}{parts.path}"


def confirm(settings, url, ask, yes):
    print("Target: " + target(settings, url))
    if yes:
        if not settings.local:
            raise CliError("--yes is only accepted when APP_ENV=local")
        return
    answer = ask("Proceed? Type 'yes' to continue: ")
    if answer.strip().lower() != "yes":
        raise CliError("Aborted; nothing was changed")


def link(settings, token):
    return f"{settings.app_origin}/enroll#t={token}"


async def supersede(db, user_id, purposes, now, actor_event_user=None):
    """Revoke the user's other unused tokens of these purposes and retire their ceremonies."""
    rows = await db.all(
        "UPDATE enrollment_tokens SET revoked_at = %s WHERE user_id = %s AND purpose = ANY(%s) "
        "AND used_at IS NULL AND revoked_at IS NULL RETURNING id",
        (now, user_id, list(purposes)),
    )
    for row in rows:
        await db.execute(
            "UPDATE webauthn_ceremonies SET consumed_at = %s WHERE enrollment_token_id = %s "
            "AND consumed_at IS NULL",
            (now, row["id"]),
        )
        await record_event(
            db, "token_revoked", actor="cli", user_id=user_id, token_id=row["id"], now=now
        )
    return len(rows)


async def insert_token(db, purpose, user_id, lifetime, now, note=None):
    secret = new_secret()
    row = await db.one(
        "INSERT INTO enrollment_tokens (token_hash, purpose, user_id, created_by_actor, note, "
        "expires_at, created_at) VALUES (%s, %s, %s, 'cli', %s, %s, %s) RETURNING id",
        (token_hash(secret), purpose, user_id, note, now + lifetime, now),
    )
    await record_event(
        db, "token_issued", actor="cli", user_id=user_id, token_id=row["id"], now=now
    )
    return secret


def require_owner(settings):
    if settings.owner_user_id is None:
        raise CliError("OWNER_USER_ID is required")
    return settings.owner_user_id


def non_owner_blocked(settings, user_id):
    return not settings.non_owner_admission and user_id != settings.owner_user_id


async def cmd_bootstrap_owner(settings, database, args):
    owner = require_owner(settings)
    now = clock.now()
    async with database.transaction() as db:
        await db.execute(
            "INSERT INTO profiles (user_id, display_name, status, created_at) "
            "VALUES (%s, 'Owner', 'active', %s) ON CONFLICT (user_id) DO NOTHING",
            (owner, now),
        )
        profile = await lock_profile(db, owner)
        if profile["status"] != "active":
            raise CliError("The owner profile is disabled; run enable-user first")
        await db.execute(
            "INSERT INTO agent_settings (user_id, runner) VALUES (%s, 'claude_subscription') "
            "ON CONFLICT (user_id) DO NOTHING",
            (owner,),
        )
        await supersede(db, owner, ["recovery"], now)
        secret = await insert_token(db, "recovery", owner, timedelta(hours=1), now, "bootstrap")
    print("Owner enrollment link (valid 1 hour, single use):")
    print(link(settings, secret))


async def cmd_issue_invite(settings, database, args):
    # Re-checked before any connection is opened; see run().
    if not settings.non_owner_admission:
        raise CliError(f"Invites are disabled until the non-owner gate opens: {DOC}")
    now = clock.now()
    async with database.transaction() as db:
        secret = await insert_token(db, "invite", None, timedelta(days=args.days), now, args.note)
    print(f"Invite link (valid {args.days} days, single use):")
    print(link(settings, secret))


async def issue_for_user(settings, database, args, purpose, event_purposes):
    user_id = UUID(args.user)
    hours = args.hours
    if not 1 <= hours <= 24:
        raise CliError("--hours must be between 1 and 24")
    if non_owner_blocked(settings, user_id):
        raise CliError(f"Non-owner enrollment is disabled: {DOC}")
    now = clock.now()
    async with database.transaction() as db:
        profile = await lock_profile(db, user_id)
        if profile is None:
            raise CliError("No such user")
        if profile["status"] != "active":
            raise CliError("The user is disabled; enabling is a separate decision")
        await supersede(db, user_id, event_purposes, now)
        secret = await insert_token(db, purpose, user_id, timedelta(hours=hours), now)
    print(f"{purpose} link (valid {hours} hour(s), single use):")
    print(link(settings, secret))


async def cmd_issue_recovery(settings, database, args):
    await issue_for_user(settings, database, args, "recovery", ["recovery"])


async def cmd_issue_add_credential(settings, database, args):
    await issue_for_user(settings, database, args, "add_credential", ["add_credential"])


async def cmd_list_tokens(settings, database, args):
    async with database.transaction() as db:
        rows = await db.all(
            "SELECT id, purpose, user_id, options_count, created_at, expires_at, used_at, "
            "revoked_at FROM enrollment_tokens ORDER BY created_at DESC LIMIT 50"
        )
    now = clock.now()
    for r in rows:
        state = (
            "used"
            if r["used_at"]
            else "revoked"
            if r["revoked_at"]
            else "expired"
            if r["expires_at"] <= now
            else "open"
        )
        print(
            f"{r['id']} {r['purpose']:<14} {state:<8} user={r['user_id'] or '-'} "
            f"expires={r['expires_at']:%Y-%m-%d %H:%M}Z attempts={r['options_count']}"
        )


async def cmd_revoke_token(settings, database, args):
    now = clock.now()
    token_id = UUID(args.token_id)
    async with database.transaction() as db:
        # Same order as recovery (design 5.6): profile first, then the token.
        target = await db.one("SELECT user_id FROM enrollment_tokens WHERE id = %s", (token_id,))
        if target is not None and target["user_id"] is not None:
            await lock_profile(db, target["user_id"])
        row = await db.one(
            "UPDATE enrollment_tokens SET revoked_at = %s WHERE id = %s "
            "AND used_at IS NULL AND revoked_at IS NULL RETURNING id, user_id",
            (now, token_id),
        )
        if row is None:
            raise CliError("No unused token with that id")
        await db.execute(
            "UPDATE webauthn_ceremonies SET consumed_at = %s WHERE enrollment_token_id = %s "
            "AND consumed_at IS NULL",
            (now, token_id),
        )
        await record_event(
            db, "token_revoked", actor="cli", user_id=row["user_id"], token_id=token_id, now=now
        )
    print("Token revoked.")


async def cmd_disable_user(settings, database, args):
    user_id = UUID(args.user)
    now = clock.now()
    async with database.transaction() as db:
        profile = await lock_profile(db, user_id)
        if profile is None:
            raise CliError("No such user")
        await db.execute("UPDATE profiles SET status = 'disabled' WHERE user_id = %s", (user_id,))
        await supersede(db, user_id, ["recovery", "add_credential"], now)
        await lock_sessions(db, user_id)
        sessions = await revoke_sessions(db, user_id, "disabled", now)
        await db.execute(
            "UPDATE webauthn_ceremonies SET consumed_at = %s WHERE user_id = %s "
            "AND consumed_at IS NULL",
            (now, user_id),
        )
        await record_event(db, "user_disabled", actor="cli", user_id=user_id, now=now)
    print(f"User disabled; {len(sessions)} session(s) revoked.")


async def cmd_enable_user(settings, database, args):
    user_id = UUID(args.user)
    now = clock.now()
    async with database.transaction() as db:
        profile = await lock_profile(db, user_id)
        if profile is None:
            raise CliError("No such user")
        await db.execute("UPDATE profiles SET status = 'active' WHERE user_id = %s", (user_id,))
        await record_event(db, "user_enabled", actor="cli", user_id=user_id, now=now)
    print("User enabled. Old sessions and tokens stay revoked.")


async def cmd_revoke_sessions(settings, database, args):
    user_id = UUID(args.user)
    now = clock.now()
    async with database.transaction() as db:
        if await lock_profile(db, user_id) is None:
            raise CliError("No such user")
        await lock_sessions(db, user_id)
        sessions = await revoke_sessions(db, user_id, "admin", now)
        for row in sessions:
            await record_event(
                db, "session_revoked", actor="cli", user_id=user_id, session_id=row["id"], now=now
            )
    print(f"{len(sessions)} session(s) revoked.")


async def cmd_dev_session(settings, database, args):
    user_id = UUID(args.user) if args.user else require_owner(settings)
    now = clock.now()
    async with database.transaction() as db:
        if await lock_profile(db, user_id) is None:
            raise CliError("No such user")
        await lock_sessions(db, user_id)
        token, _id, _age = await create_session(db, user_id, None, now, kind="dev")
    print(f"{settings.session_cookie}={token}")


COMMANDS = {
    "bootstrap-owner": (cmd_bootstrap_owner, True),
    "issue-invite": (cmd_issue_invite, True),
    "issue-recovery": (cmd_issue_recovery, True),
    "issue-add-credential": (cmd_issue_add_credential, True),
    "list-tokens": (cmd_list_tokens, False),
    "revoke-token": (cmd_revoke_token, True),
    "disable-user": (cmd_disable_user, True),
    "enable-user": (cmd_enable_user, True),
    "revoke-sessions": (cmd_revoke_sessions, True),
    "dev-session": (cmd_dev_session, False),
}


def parser():
    p = argparse.ArgumentParser(prog="auth_admin")
    p.add_argument("--yes", action="store_true", help="skip confirmation (APP_ENV=local only)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap-owner")
    inv = sub.add_parser("issue-invite")
    inv.add_argument("--days", type=int, default=7)
    inv.add_argument("--note", default=None)
    for name in ("issue-recovery", "issue-add-credential"):
        c = sub.add_parser(name)
        c.add_argument("--user", required=True)
        c.add_argument("--hours", type=int, default=1)
    sub.add_parser("list-tokens")
    rt = sub.add_parser("revoke-token")
    rt.add_argument("token_id")
    for name in ("disable-user", "enable-user", "revoke-sessions"):
        c = sub.add_parser(name)
        if name == "revoke-sessions":
            c.add_argument("--user", required=True)
        else:
            c.add_argument("user")
    ds = sub.add_parser("dev-session")
    ds.add_argument("--user", default=None)
    return p


async def run(argv, env_settings=None, ask=input):
    args = parser().parse_args(argv)
    settings = env_settings or Settings.from_env(admin_cli=True)
    handler, mutates = COMMANDS[args.command]
    if args.command == "issue-invite":
        if not settings.non_owner_admission:
            raise CliError(f"Invites are disabled until the non-owner gate opens: {DOC}")
        if not 1 <= args.days <= 30:
            raise CliError("--days must be between 1 and 30")
    if args.command == "dev-session":
        if not settings.local:
            raise CliError("dev-session only runs with APP_ENV=local")
        url = settings.database_url
        host = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://", 1)).hostname
        if host not in LOCAL_HOSTS:
            raise CliError("dev-session only connects to a local database")
    else:
        url = settings.admin_database_url
        if not url:
            raise CliError("ADMIN_DATABASE_URL is required")
    if mutates:
        confirm(settings, url, ask, args.yes)
    await handler(settings, Database(settings, url), args)


def main(argv=None):
    try:
        asyncio.run(run(sys.argv[1:] if argv is None else argv))
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # Driver errors can contain connection details.
        print("Command failed; check configuration and database state.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
