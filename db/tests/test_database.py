import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, time
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.exc import IntegrityError

from alembic import command
from brief_db.seed import DEFAULT_TOPICS, LOCAL_OWNER_ID, seed
from tests.conftest import alembic_config

pytestmark = pytest.mark.asyncio


async def add_slot(connection, owner, position, local_time):
    return await connection.fetchval(
        "INSERT INTO delivery_slots (user_id, position, local_time) "
        "VALUES ($1, $2, $3) RETURNING id",
        owner,
        position,
        local_time,
    )


async def add_job(connection, owner, slot, scheduled_for):
    return await connection.fetchval(
        """INSERT INTO jobs (user_id, slot_id, scheduled_for, runner)
        VALUES ($1, $2, $3, 'claude_subscription') RETURNING id""",
        owner,
        slot,
        scheduled_for,
    )


async def add_report_item(connection, owner):
    report = await connection.fetchval(
        "INSERT INTO reports (user_id, report_date) VALUES ($1, '2026-11-13') RETURNING id", owner
    )
    section = await connection.fetchval(
        "INSERT INTO report_sections (report_id, title, position) "
        "VALUES ($1, 'News', 0) RETURNING id",
        report,
    )
    item = await connection.fetchval(
        """INSERT INTO report_items
        (section_id, rank, title, summary, source_name, source_url)
        VALUES ($1, 1, 'Title', 'Summary', 'Example', 'https://example.com') RETURNING id""",
        section,
    )
    return section, item


async def test_three_slots_and_fourth_rejected(connection, owner):
    for position in range(1, 4):
        await add_slot(connection, owner, position, time(6 + position))
    with pytest.raises(asyncpg.CheckViolationError):
        await add_slot(connection, owner, 4, time(10))
    with pytest.raises(asyncpg.UniqueViolationError):
        await add_slot(connection, owner, 1, time(10))
    assert await connection.fetchval("SELECT count(*) FROM delivery_slots") == 3


async def test_slot_limit_per_user_includes_disabled_slots(connection, owner):
    for position in range(1, 4):
        await add_slot(connection, owner, position, time(6 + position))
    await connection.execute("UPDATE delivery_slots SET enabled = false")
    with pytest.raises(asyncpg.UniqueViolationError):
        await add_slot(connection, owner, 1, time(10))
    other = uuid4()
    await connection.execute(
        "INSERT INTO profiles (user_id, email) VALUES ($1, 'reader@example.com')", other
    )
    await add_slot(connection, other, 1, time(7))


@pytest.mark.parametrize("invalid", [time(9, 15), time(9, 30, 1), time(9, 30, 0, 1)])
async def test_slot_granularity(connection, owner, invalid):
    with pytest.raises(asyncpg.CheckViolationError):
        await add_slot(connection, owner, 1, invalid)


async def test_duplicate_slot_time_rejected_at_commit(connection, owner):
    await add_slot(connection, owner, 1, time(7))
    with pytest.raises(asyncpg.UniqueViolationError):
        async with connection.transaction():
            await add_slot(connection, owner, 2, time(7))


async def test_slot_swap_preserves_job_and_report_references(connection, owner):
    first = await add_slot(connection, owner, 1, time(7))
    second = await add_slot(connection, owner, 2, time(8))
    job = await add_job(connection, owner, first, datetime(2026, 11, 12, tzinfo=UTC))
    await connection.execute(
        """INSERT INTO reports (user_id, job_id, report_date, slot_id)
        VALUES ($1, $2, '2026-11-12', $3)""",
        owner,
        job,
        first,
    )
    async with connection.transaction():
        await connection.execute("UPDATE delivery_slots SET position = 3 - position")
        await connection.execute(
            """UPDATE delivery_slots SET local_time =
            CASE WHEN local_time = '07:00' THEN '08:00'::time ELSE '07:00'::time END"""
        )
    assert await connection.fetchval("SELECT slot_id FROM jobs") == first
    assert await connection.fetchval("SELECT slot_id FROM reports") == first
    assert (
        await connection.fetchval("SELECT position FROM delivery_slots WHERE id = $1", second) == 1
    )


async def test_job_unique_key_and_utc_instant(connection, owner):
    slot = await add_slot(connection, owner, 1, time(7))
    when = datetime(2026, 11, 12, tzinfo=UTC)
    await add_job(connection, owner, slot, when)
    with pytest.raises(asyncpg.UniqueViolationError):
        await add_job(connection, owner, slot, datetime.fromisoformat("2026-11-12T08:00:00+08:00"))
    other_slot = await add_slot(connection, owner, 2, time(8))
    await add_job(connection, owner, other_slot, when)
    await add_job(connection, owner, slot, datetime(2026, 11, 13, tzinfo=UTC))
    assert await connection.fetchval("SELECT count(*) FROM jobs") == 3


async def test_concurrent_scheduler_inserts_only_one_job(connection, database, owner):
    slot = await add_slot(connection, owner, 1, time(7))
    when = datetime(2026, 11, 12, tzinfo=UTC)

    async def insert():
        other = await asyncpg.connect(database)
        try:
            return await other.fetchval(
                """INSERT INTO jobs (user_id, slot_id, scheduled_for, runner)
                VALUES ($1, $2, $3, 'claude_subscription')
                ON CONFLICT (user_id, slot_id, scheduled_for) DO NOTHING RETURNING id""",
                owner,
                slot,
                when,
            )
        finally:
            await other.close()

    results = await asyncio.gather(insert(), insert())
    assert sum(result is not None for result in results) == 1


async def test_deleting_slots_preserves_historical_jobs(connection, owner):
    when = datetime(2026, 11, 12, tzinfo=UTC)
    for position in (1, 2):
        slot = await add_slot(connection, owner, position, time(6 + position))
        await add_job(connection, owner, slot, when)
    await connection.execute("DELETE FROM delivery_slots")
    assert await connection.fetchval("SELECT count(*) FROM jobs WHERE slot_id IS NULL") == 2


async def test_rls_enabled_everywhere_and_client_grants_revoked(connection, owner):
    tables = await connection.fetch(
        "SELECT relname, relrowsecurity FROM pg_class "
        "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
    )
    assert tables
    assert not [row["relname"] for row in tables if not row["relrowsecurity"]]
    for role in ("anon", "authenticated"):
        for row in tables:
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, $2, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')",
                role,
                row["relname"],
            )
        await connection.execute(f"SET ROLE {role}")
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.fetch("SELECT * FROM profiles")
        finally:
            await connection.execute("RESET ROLE")


@pytest.mark.parametrize("scope", ["", " IN SCHEMA public"])
async def test_future_tables_do_not_inherit_client_grants(connection, scope):
    # Reproduce an already-migrated database with global or schema defaults.
    await asyncio.to_thread(command.downgrade, alembic_config(), "0002_security_slots")
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES{scope} GRANT ALL ON TABLES TO PUBLIC, anon, authenticated"
    )
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    await connection.execute("CREATE TABLE future_table (id integer PRIMARY KEY)")
    await connection.execute("INSERT INTO future_table VALUES (1)")
    # No RLS on this probe: denial must come from privileges alone, including
    # TRUNCATE, which RLS cannot block. rls_probe receives only PUBLIC grants.
    for role in ("anon", "authenticated", "rls_probe"):
        assert not await connection.fetchval(
            "SELECT has_table_privilege($1, 'future_table', "
            "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",
            role,
        )
        await connection.execute(f"SET ROLE {role}")
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.fetch("SELECT * FROM future_table")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute("TRUNCATE future_table")
        finally:
            await connection.execute("RESET ROLE")
    assert await connection.fetchval("SELECT count(*) FROM future_table") == 1


async def test_restrictive_rls_blocks_reads_and_writes_even_with_grants(connection, owner):
    await connection.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON profiles TO rls_probe")
    # A future permissive policy must not accidentally reopen direct access.
    await connection.execute(
        "CREATE POLICY accidental_allow ON profiles TO rls_probe USING (true) WITH CHECK (true)"
    )
    await connection.execute("SET ROLE rls_probe")
    try:
        assert await connection.fetch("SELECT * FROM profiles") == []
        assert await connection.execute("UPDATE profiles SET bio = 'changed'") == "UPDATE 0"
        assert await connection.execute("DELETE FROM profiles") == "DELETE 0"
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute(
                "INSERT INTO profiles (user_id, email) VALUES ($1, 'reader@example.com')", uuid4()
            )
    finally:
        await connection.execute("RESET ROLE")
    assert await connection.fetchval("SELECT count(*) FROM profiles") == 1


async def test_updated_at_trigger(connection, owner):
    previous = await connection.fetchval("SELECT updated_at FROM profiles")
    await connection.execute("UPDATE profiles SET bio = 'updated' WHERE user_id = $1", owner)
    assert await connection.fetchval("SELECT updated_at FROM profiles") > previous


@pytest.mark.parametrize(
    "locale,valid",
    [("", False), ("x", False), ("en", True), ("x" * 35, True), ("x" * 36, False), ("中文", True)],
)
async def test_profile_locale_length_on_insert_and_update(connection, owner, locale, valid):
    statements = (
        (
            "INSERT INTO profiles (user_id, email, locale) VALUES ($1, 'reader@example.com', $2)",
            uuid4(),
        ),
        ("UPDATE profiles SET locale = $2 WHERE user_id = $1", owner),
    )
    for sql, user_id in statements:
        if valid:
            await connection.execute(sql, user_id, locale)
            assert (
                await connection.fetchval("SELECT locale FROM profiles WHERE user_id = $1", user_id)
                == locale
            )
        else:
            with pytest.raises(asyncpg.CheckViolationError) as error:
                await connection.execute(sql, user_id, locale)
            assert error.value.constraint_name == "profiles_locale_check"


async def test_locale_migration_validates_existing_rows_and_downgrades(connection, owner):
    await asyncio.to_thread(command.downgrade, alembic_config(), "0003_default_privileges")
    await connection.execute("UPDATE profiles SET locale = 'x' WHERE user_id = $1", owner)
    with pytest.raises(IntegrityError):
        await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    assert (
        await connection.fetchval("SELECT version_num FROM alembic_version")
        == "0003_default_privileges"
    )
    assert await connection.fetchval("SELECT locale FROM profiles") == "x"
    await connection.execute("UPDATE profiles SET locale = 'zh-TW' WHERE user_id = $1", owner)
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    with pytest.raises(asyncpg.CheckViolationError):
        await connection.execute("UPDATE profiles SET locale = 'x' WHERE user_id = $1", owner)


@pytest.mark.parametrize(
    "url,valid",
    [
        ("http://example.com", True),
        ("https://news.example.com/item?q=1#section", True),
        ("https://example.com?query=1", True),
        ("https://example.com#section", True),
        ("https://example.com:8443/path", True),
        ("https://[::1]/path", True),
        ("https://example.com/" + "x" * (2048 - len("https://example.com/")), True),
        ("https://example.com/" + "x" * (2049 - len("https://example.com/")), False),
        ("https://", False),
        ("http://", False),
        ("https:///path", False),
        ("https://?query=1", False),
        ("https://#section", False),
        ("https://@/path", False),
        ("https://exa mple.com/path", False),
        ("https://example.com/path with spaces", False),
        ("https://example.com/\npath", False),
        ("ftp://example.com/path", False),
    ],
    ids=[
        "http",
        "path-query-fragment",
        "query",
        "fragment",
        "port",
        "ipv6",
        "max-length",
        "too-long",
        "empty-https",
        "empty-http",
        "empty-path",
        "empty-query",
        "empty-fragment",
        "empty-userinfo",
        "space-host",
        "space-path",
        "newline-path",
        "wrong-scheme",
    ],
)
async def test_source_url_host_on_insert_and_update(connection, owner, url, valid):
    section, item = await add_report_item(connection, owner)
    statements = (
        (
            """INSERT INTO report_items
        (section_id, rank, title, summary, source_name, source_url)
        VALUES ($1, 2, 'Title', 'Summary', 'Example', $2)""",
            section,
        ),
        ("UPDATE report_items SET source_url = $2 WHERE id = $1", item),
    )
    for sql, identifier in statements:
        if valid:
            await connection.execute(sql, identifier, url)
        else:
            with pytest.raises(asyncpg.CheckViolationError) as error:
                await connection.execute(sql, identifier, url)
            assert error.value.constraint_name == "report_items_source_url_check"
    assert await connection.fetchval("SELECT source_url FROM report_items WHERE id = $1", item) == (
        url if valid else "https://example.com"
    )


async def test_source_url_migration_validates_existing_rows_and_downgrades(connection, owner):
    _, item = await add_report_item(connection, owner)
    await asyncio.to_thread(command.downgrade, alembic_config(), "0004_profile_locale")
    await connection.execute(
        "UPDATE report_items SET source_url = 'https:///missing' WHERE id = $1", item
    )
    with pytest.raises(IntegrityError):
        await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    assert (
        await connection.fetchval("SELECT version_num FROM alembic_version")
        == "0004_profile_locale"
    )
    assert await connection.fetchval("SELECT source_url FROM report_items") == "https:///missing"
    await connection.execute(
        "UPDATE report_items SET source_url = 'https://example.com' WHERE id = $1", item
    )
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    with pytest.raises(asyncpg.CheckViolationError):
        await connection.execute(
            "UPDATE report_items SET source_url = 'https://?query=1' WHERE id = $1", item
        )


async def test_seed_local_is_repeatable_and_preserves_settings(connection, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "Owner@Example.com")
    monkeypatch.setenv("APP_ENV", "local")
    await seed()
    await connection.execute("UPDATE subscriptions SET item_count = 5")
    await connection.execute("UPDATE delivery_slots SET local_time = '08:30'")
    await seed()
    assert await connection.fetchval("SELECT user_id FROM profiles") == LOCAL_OWNER_ID
    assert await connection.fetchval("SELECT email FROM allowed_emails") == "owner@example.com"
    assert await connection.fetchval("SELECT count(*) FROM topics") == len(DEFAULT_TOPICS)
    assert await connection.fetchval("SELECT count(*) FROM subscriptions WHERE item_count = 5") == 6
    assert await connection.fetchval("SELECT local_time FROM delivery_slots") == time(8, 30)


async def test_production_seed_only_allowlists(connection, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("APP_ENV", "production")
    await seed()
    await seed()
    assert await connection.fetchval("SELECT count(*) FROM allowed_emails") == 1
    for table in ("profiles", "topics", "subscriptions", "delivery_slots", "agent_settings"):
        assert await connection.fetchval(f"SELECT count(*) FROM {table}") == 0


async def test_seed_refuses_changed_local_identity_atomically(connection, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("APP_ENV", "local")
    await seed()
    monkeypatch.setenv("OWNER_EMAIL", "reader@example.com")
    with pytest.raises(ValueError, match="identity differs"):
        await seed()
    assert await connection.fetchval("SELECT count(*) FROM allowed_emails") == 1


@pytest.mark.parametrize(
    "app_env,email", [("production", ""), ("local", "invalid"), ("staging", "owner@example.com")]
)
async def test_seed_requires_explicit_configuration(connection, monkeypatch, app_env, email):
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("OWNER_EMAIL", email)
    with pytest.raises(ValueError):
        await seed()
    assert await connection.fetchval("SELECT count(*) FROM allowed_emails") == 0


@pytest.mark.asyncio
async def test_cli_does_not_log_configuration_or_parameters(database, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("APP_ENV", "bad-config")
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "brief_db", "seed"],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode != 0
    assert "owner@example.com" not in result.stdout + result.stderr
    assert database not in result.stdout + result.stderr


async def test_upgrade_downgrade_upgrade(database, connection):
    await connection.close()
    await asyncio.to_thread(command.downgrade, alembic_config(), "base")
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    fresh = await asyncpg.connect(database)
    try:
        assert (
            await fresh.fetchval("SELECT version_num FROM alembic_version")
            == "0005_source_url_host"
        )
        assert await fresh.fetchval("SELECT count(*) FROM profiles") == 0
    finally:
        await fresh.close()
