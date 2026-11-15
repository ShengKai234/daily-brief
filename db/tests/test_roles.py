"""Local role bootstrap (brief_db.roles): the three backend login roles and table ownership."""

import asyncio
from urllib.parse import urlsplit

import asyncpg
import pytest

from alembic import command
from brief_db import roles
from tests.conftest import alembic_config

pytestmark = pytest.mark.asyncio


def role_urls(database, monkeypatch):
    parts = urlsplit(database)
    base = f"postgresql://{{role}}:{{pw}}@{parts.hostname}:{parts.port}{parts.path}"
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("POSTGRES_USER", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "not-checked-by-trust-auth")
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL", base.format(role="brief_migrator", pw="m!gr%40te pw")
    )
    monkeypatch.setenv("DATABASE_URL", base.format(role="brief_runtime", pw="runtime-pw"))
    monkeypatch.setenv("ADMIN_DATABASE_URL", base.format(role="brief_admin", pw="admin-pw"))
    return base


async def test_bootstrap_adopts_a_legacy_database_and_migrates_as_the_migrator(
    database, connection, monkeypatch
):
    await asyncio.to_thread(command.downgrade, alembic_config(), "0005_source_url_host")
    await connection.execute(
        "INSERT INTO profiles (user_id, email, status) VALUES (gen_random_uuid(), 'a@example.com', 'active')"
    )
    owner = await connection.fetchval("SELECT user_id FROM profiles")
    monkeypatch.setenv("OWNER_USER_ID", str(owner))
    base = role_urls(database, monkeypatch)
    assert roles.configured()
    await roles.bootstrap_local_roles()
    await roles.bootstrap_local_roles()  # idempotent
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace "
            "AND relkind = 'r' AND pg_get_userbyid(relowner) <> 'brief_migrator'"
        )
        == 0
    )
    # The migration runs as brief_migrator (MIGRATION_DATABASE_URL wins over DATABASE_URL).
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    for role in ("brief_migrator", "brief_runtime", "brief_admin"):
        row = await connection.fetchrow(
            "SELECT rolcanlogin, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = $1", role
        )
        assert row["rolcanlogin"] and not row["rolsuper"] and not row["rolbypassrls"]
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace "
            "AND relkind = 'r' AND pg_get_userbyid(relowner) <> 'brief_migrator'"
        )
        == 0
    )
    # Connect as the real login roles (the password with special characters round-trips).
    runtime = await asyncpg.connect(base.format(role="brief_runtime", pw="runtime-pw"))
    try:
        assert await runtime.fetchval("SELECT count(*) FROM profiles") == 1
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.execute(
                "INSERT INTO enrollment_tokens (token_hash, purpose, created_by_actor, expires_at) "
                "VALUES ('\\x" + "00" * 32 + "', 'invite', 'cli', now() + interval '1 day')"
            )
    finally:
        await runtime.close()
    admin = await asyncpg.connect(base.format(role="brief_admin", pw="admin-pw"))
    try:
        await admin.execute(
            "INSERT INTO enrollment_tokens (token_hash, purpose, user_id, created_by_actor, expires_at) "
            "VALUES ('\\x" + "11" * 32 + "', 'recovery', $1, 'cli', now() + interval '1 hour')",
            owner,
        )
    finally:
        await admin.close()


async def test_bootstrap_refuses_production_and_mismatched_roles(monkeypatch, database):
    role_urls(database, monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    assert not roles.configured()
    with pytest.raises(ValueError, match="APP_ENV=local"):
        await roles.bootstrap_local_roles()
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "postgresql://someone_else:pw@127.0.0.1:1/x")
    with pytest.raises(ValueError, match="brief_runtime"):
        await roles.bootstrap_local_roles()
