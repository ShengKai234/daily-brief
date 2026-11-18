"""F10 data protection and migration (docs/auth-passkey-design.md 7), @integration."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from tests.conftest import alembic_config

pytestmark = pytest.mark.asyncio

AUTH_TABLES = (
    "enrollment_tokens",
    "passkey_credentials",
    "auth_sessions",
    "webauthn_ceremonies",
    "auth_rate_limits",
    "auth_events",
)
NOW = datetime.now(UTC)


async def as_role(connection, role):
    await connection.execute(f"SET ROLE {role}")


async def reset(connection):
    await connection.execute("RESET ROLE")


async def add_credential(connection, user_id):
    return await connection.fetchval(
        """INSERT INTO passkey_credentials
        (user_id, credential_id, public_key, backup_eligible, backup_state)
        VALUES ($1, $2, '\\x01', false, false) RETURNING id""",
        user_id,
        uuid4().bytes,
    )


async def add_session(connection, user_id, credential, **extra):
    return await connection.fetchval(
        """INSERT INTO auth_sessions
        (user_id, token_hash, kind, credential_id, idle_expires_at, absolute_expires_at)
        VALUES ($1, $2, 'passkey', $3, now() + interval '30 days', now() + interval '90 days')
        RETURNING id""",
        user_id,
        uuid4().bytes + uuid4().bytes,
        credential,
    )


async def test_auth_tables_are_default_deny(connection, owner):
    for table in AUTH_TABLES:
        assert await connection.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", table
        )
        policies = await connection.fetch(
            "SELECT policyname, roles FROM pg_policies WHERE tablename = $1", table
        )
        for policy in policies:
            assert not {"public", "anon", "authenticated"} & set(policy["roles"]), policy
        for role in ("anon", "authenticated", "rls_probe"):
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, $2, "
                "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",
                role,
                table,
            )
    # No client role has any policy on any table either (business tables included).
    leaked = await connection.fetch(
        "SELECT tablename FROM pg_policies WHERE tablename <> 'alembic_version' "
        "AND roles && ARRAY['public', 'anon', 'authenticated']::name[]"
    )
    assert not leaked


async def test_backend_roles_are_not_privileged(connection):
    for role in ("brief_runtime", "brief_admin"):
        row = await connection.fetchrow(
            "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = $1", role
        )
        assert not row["rolbypassrls"] and not row["rolsuper"]
        owned = await connection.fetchval(
            "SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace "
            "AND pg_get_userbyid(relowner) = $1",
            role,
        )
        assert owned == 0
        for table in (*AUTH_TABLES, "profiles", "jobs", "reports"):
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, $2, 'TRUNCATE,REFERENCES,TRIGGER')", role, table
            )


async def test_runtime_role_works_under_rls_but_cannot_issue_tokens(connection, owner):
    await connection.execute("INSERT INTO agent_settings (user_id) VALUES ($1)", owner)
    report = await connection.fetchval(
        "INSERT INTO reports (user_id, report_date) VALUES ($1, '2026-11-15') RETURNING id", owner
    )
    await connection.execute(
        """INSERT INTO enrollment_tokens (token_hash, purpose, user_id, created_by_actor, expires_at)
        VALUES ($1, 'recovery', $2, 'cli', now() + interval '1 hour')""",
        b"t" * 32,
        owner,
    )
    await as_role(connection, "brief_runtime")
    try:
        assert await connection.fetchval("SELECT id FROM reports") == report
        credential = await add_credential(connection, owner)
        await add_session(connection, owner, credential)
        await connection.execute(
            "INSERT INTO auth_events (actor, user_id, event) VALUES ('user', $1, 'login_succeeded')",
            owner,
        )
        await connection.execute(
            "INSERT INTO auth_rate_limits (bucket, window_start, hits) VALUES ('ip:x:auth', now(), 1)"
        )
        assert await connection.fetchval("SELECT count(*) FROM enrollment_tokens") == 1
        assert (
            await connection.execute("UPDATE enrollment_tokens SET options_count = 1") == "UPDATE 1"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute(
                """INSERT INTO enrollment_tokens
                (token_hash, purpose, created_by_actor, expires_at)
                VALUES ($1, 'invite', 'cli', now() + interval '1 day')""",
                b"u" * 32,
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute("TRUNCATE auth_sessions")
        # Cleanup may delete only used/revoked/expired-90-days tokens; a live token is untouched.
        assert await connection.execute("DELETE FROM enrollment_tokens") == "DELETE 0"
    finally:
        await reset(connection)
    assert await connection.fetchval("SELECT count(*) FROM enrollment_tokens") == 1


async def test_runtime_cleanup_deletes_only_old_closed_tokens(connection, owner):
    old = NOW - timedelta(days=100)
    await connection.execute(
        """INSERT INTO enrollment_tokens (token_hash, purpose, user_id, created_by_actor,
            created_at, expires_at, used_at) VALUES ($1, 'recovery', $2, 'cli', $3, $4, $5)""",
        b"o" * 32,
        owner,
        old - timedelta(days=1),
        old + timedelta(hours=1),
        old,
    )
    await connection.execute(
        """INSERT INTO enrollment_tokens (token_hash, purpose, user_id, created_by_actor,
            expires_at, used_at) VALUES ($1, 'recovery', $2, 'cli', now() + interval '1 hour', now())""",
        b"n" * 32,
        owner,
    )
    await as_role(connection, "brief_runtime")
    try:
        assert await connection.execute("DELETE FROM enrollment_tokens") == "DELETE 1"
    finally:
        await reset(connection)


async def test_admin_role_can_bootstrap_and_issue_recovery_only(connection):
    owner = uuid4()
    await as_role(connection, "brief_admin")
    try:
        await connection.execute("INSERT INTO profiles (user_id) VALUES ($1)", owner)
        await connection.execute("INSERT INTO agent_settings (user_id) VALUES ($1)", owner)
        token = await connection.fetchval(
            """INSERT INTO enrollment_tokens (token_hash, purpose, user_id, created_by_actor,
            expires_at) VALUES ($1, 'recovery', $2, 'cli', now() + interval '1 hour') RETURNING id""",
            b"a" * 32,
            owner,
        )
        await connection.execute(
            "INSERT INTO auth_events (actor, event, enrollment_token_id) "
            "VALUES ('cli', 'token_issued', $1)",
            token,
        )
        await connection.execute(
            "UPDATE profiles SET status = 'disabled' WHERE user_id = $1", owner
        )
        await connection.execute("UPDATE webauthn_ceremonies SET consumed_at = now()")
        await connection.execute(
            "UPDATE auth_sessions SET revoked_at = now(), revoke_reason = 'admin'"
        )
        assert await connection.fetchval("SELECT count(*) FROM jobs") == 0
        for sql in (
            "DELETE FROM profiles",
            "TRUNCATE profiles",
            f"INSERT INTO topics (owner_id, title, normalized_key) VALUES ('{owner}', 't', 't')",
            "INSERT INTO auth_sessions (user_id, token_hash, kind, idle_expires_at, "
            f"absolute_expires_at) VALUES ('{owner}', '\\x00', 'dev', now(), now())",
            "UPDATE passkey_credentials SET revoked_at = now()",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute(sql)
    finally:
        await reset(connection)
    assert await connection.fetchval("SELECT count(*) FROM profiles") == 1


async def test_client_roles_can_do_none_of_it(connection, owner):
    for role in ("anon", "authenticated", "rls_probe"):
        await as_role(connection, role)
        try:
            for table in (*AUTH_TABLES, "profiles", "reports"):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.fetch(f"SELECT * FROM {table}")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(f"TRUNCATE {table}")
        finally:
            await reset(connection)


async def test_schema_constraints(connection, owner):
    credential = await add_credential(connection, owner)
    with pytest.raises(asyncpg.UniqueViolationError):
        await connection.execute(
            "INSERT INTO passkey_credentials (user_id, credential_id, public_key, "
            "backup_eligible, backup_state) SELECT user_id, credential_id, public_key, "
            "backup_eligible, backup_state FROM passkey_credentials"
        )
    with pytest.raises(asyncpg.CheckViolationError):  # BS requires BE
        await connection.execute(
            "INSERT INTO passkey_credentials (user_id, credential_id, public_key, "
            "backup_eligible, backup_state) VALUES ($1, $2, '\\x01', false, true)",
            owner,
            b"z",
        )
    with pytest.raises(asyncpg.CheckViolationError):  # passkey session needs a credential
        await connection.execute(
            "INSERT INTO auth_sessions (user_id, token_hash, kind, idle_expires_at, "
            "absolute_expires_at) VALUES ($1, $2, 'passkey', now() + interval '1 day', "
            "now() + interval '2 days')",
            owner,
            b"s" * 32,
        )
    with pytest.raises(asyncpg.CheckViolationError):  # invite tokens have no user
        await connection.execute(
            "INSERT INTO enrollment_tokens (token_hash, purpose, user_id, created_by_actor, "
            "expires_at) VALUES ($1, 'invite', $2, 'cli', now() + interval '1 day')",
            b"i" * 32,
            owner,
        )
    with pytest.raises(asyncpg.CheckViolationError):  # a login ceremony carries no user
        await connection.execute(
            "INSERT INTO webauthn_ceremonies (purpose, challenge, browser_binding_hash, user_id, "
            "expires_at) VALUES ('login', $1, $2, $3, now() + interval '5 minutes')",
            b"c" * 32,
            b"b" * 32,
            owner,
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await connection.execute("UPDATE profiles SET status = 'pending'")
    assert credential
    # Deleting a profile removes its credentials and sessions (cascade), keeps events (set null).
    await add_session(connection, owner, credential)
    await connection.execute("DELETE FROM profiles")
    assert await connection.fetchval("SELECT count(*) FROM auth_sessions") == 0


async def seed_legacy(connection, owner_id, *, extra=None):
    await connection.execute(
        "INSERT INTO profiles (user_id, email, display_name, status) "
        "VALUES ($1, 'owner@example.com', 'Local owner', 'active')",
        owner_id,
    )
    await connection.execute("INSERT INTO agent_settings (user_id) VALUES ($1)", owner_id)
    await connection.execute("INSERT INTO allowed_emails (email) VALUES ('owner@example.com')")
    if extra:
        await connection.execute(
            "INSERT INTO profiles (user_id, email, status) VALUES ($1, 'x@example.com', $2)",
            uuid4(),
            extra,
        )


async def test_upgrade_preserves_seeded_data(connection, monkeypatch):
    await asyncio.to_thread(command.downgrade, alembic_config(), "0005_source_url_host")
    owner_id = uuid4()
    await seed_legacy(connection, owner_id)
    topic = await connection.fetchval(
        "INSERT INTO topics (owner_id, title, normalized_key) VALUES ($1, 'AI', 'ai') RETURNING id",
        owner_id,
    )
    await connection.execute(
        "INSERT INTO subscriptions (user_id, topic_id) VALUES ($1, $2)", owner_id, topic
    )
    await connection.execute(
        "INSERT INTO delivery_slots (user_id, local_time, position) VALUES ($1, '07:00', 1)",
        owner_id,
    )
    report = await connection.fetchval(
        "INSERT INTO reports (user_id, report_date) VALUES ($1, '2026-11-14') RETURNING id",
        owner_id,
    )
    section = await connection.fetchval(
        "INSERT INTO report_sections (report_id, title, position) VALUES ($1, 's', 0) RETURNING id",
        report,
    )
    item = await connection.fetchval(
        "INSERT INTO report_items (section_id, rank, title, summary, source_name, source_url) "
        "VALUES ($1, 1, 't', 's', 'n', 'https://example.com') RETURNING id",
        section,
    )
    await connection.execute(
        "INSERT INTO item_marks (user_id, item_id, note) VALUES ($1, $2, 'n')", owner_id, item
    )
    monkeypatch.setenv("OWNER_USER_ID", str(owner_id))
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    assert await connection.fetchval("SELECT user_id FROM profiles") == owner_id
    assert await connection.fetchval("SELECT display_name FROM profiles") == "Local owner"
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'profiles' AND column_name = 'email'"
        )
        == 0
    )
    for table, expected in (
        ("topics", 1),
        ("subscriptions", 1),
        ("delivery_slots", 1),
        ("reports", 1),
        ("item_marks", 1),
        ("agent_settings", 1),
    ):
        assert await connection.fetchval(f"SELECT count(*) FROM {table}") == expected
    for table in ("allowed_emails", "invite_codes", "invite_redemptions"):
        assert await connection.fetchval("SELECT to_regclass($1)", table) is None
    assert (
        await connection.fetchval(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'profiles' AND column_name = 'status'"
        )
        == "'active'::text"
    )


@pytest.mark.parametrize("extra", ["pending", "active"])
async def test_migration_stops_on_unexpected_legacy_profiles(connection, monkeypatch, extra):
    await asyncio.to_thread(command.downgrade, alembic_config(), "0005_source_url_host")
    owner_id = uuid4()
    await seed_legacy(connection, owner_id, extra=extra)
    monkeypatch.setenv("OWNER_USER_ID", str(owner_id))
    with pytest.raises(Exception, match="Auth migration stopped"):
        await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    assert await connection.fetchval("SELECT count(*) FROM profiles") == 2
    assert await connection.fetchval("SELECT count(*) FROM profiles WHERE status = $1", extra) >= 1
    assert (
        await connection.fetchval("SELECT version_num FROM alembic_version")
        == "0005_source_url_host"
    )
    assert await connection.fetchval("SELECT to_regclass('enrollment_tokens')") is None


async def test_migration_stops_without_configured_owner(connection, monkeypatch):
    await asyncio.to_thread(command.downgrade, alembic_config(), "0005_source_url_host")
    await seed_legacy(connection, uuid4())
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    with pytest.raises(Exception, match="Auth migration stopped"):
        await asyncio.to_thread(command.upgrade, alembic_config(), "head")
