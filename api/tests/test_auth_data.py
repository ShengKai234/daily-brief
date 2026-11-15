"""F10 data protection: bounded cleanup and scheduler eligibility (AUTH-16)."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from authkit import execute, owner_with_passkey, rows
from conftest import OWNER, production_settings

from app import cleanup, clock
from app.scheduler import tick


@pytest.mark.integration
async def test_cleanup_is_bounded_and_never_ends_live_grants(env):
    browser, auth = await owner_with_passkey(env)
    old = clock.now() - timedelta(days=120)
    await execute(
        env,
        "INSERT INTO webauthn_ceremonies (purpose, challenge, browser_binding_hash, expires_at, created_at) "
        "SELECT 'login', %s, %s, %s, %s FROM generate_series(1, 1500)",
        (b"c" * 32, b"b" * 32, old + timedelta(minutes=5), old),
    )
    await execute(
        env,
        "INSERT INTO auth_rate_limits (bucket, window_start) SELECT 'ip:old:' || g, %s FROM generate_series(1, 1200) g",
        (clock.now() - timedelta(days=3),),
    )
    live = clock.now()
    await execute(
        env,
        "INSERT INTO enrollment_tokens (token_hash, purpose, user_id, created_by_actor, created_at, expires_at) "
        "VALUES (%s, 'recovery', %s, 'cli', %s, %s)",
        (b"L" * 32, OWNER, live, live + timedelta(hours=1)),
    )
    await execute(
        env,
        "INSERT INTO enrollment_tokens (token_hash, purpose, user_id, created_by_actor, created_at, expires_at, used_at) "
        "VALUES (%s, 'recovery', %s, 'cli', %s, %s, %s)",
        (b"U" * 32, OWNER, old - timedelta(days=1), old + timedelta(hours=1), old),
    )
    await execute(env, "UPDATE passkey_credentials SET revoked_at=%s", (old,))
    first = await cleanup.run(env["database"], clock.now())
    assert first["ceremonies"] == 1000 and first["rate_limits"] == 1000
    assert first["tokens"] >= 1  # the old used tokens only
    remaining = await rows(
        env, "SELECT count(*) AS n FROM webauthn_ceremonies WHERE challenge = %s", (b"c" * 32,)
    )
    assert remaining[0]["n"] == 500
    second = await cleanup.run(env["database"], clock.now())
    assert second["ceremonies"] >= 500 and second["rate_limits"] == 200
    # Live token, revoked passkey and the profile survive.
    assert await rows(env, "SELECT 1 FROM enrollment_tokens WHERE token_hash=%s", (b"L" * 32,))
    assert (
        await rows(env, "SELECT 1 FROM enrollment_tokens WHERE token_hash=%s", (b"U" * 32,)) == []
    )
    assert len(await rows(env, "SELECT 1 FROM passkey_credentials")) == 1


@pytest.mark.integration
async def test_cleanup_removes_old_sessions_and_events_only(env):
    browser, auth = await owner_with_passkey(env)
    now = clock.now()
    session = await rows(env, "SELECT id FROM auth_sessions WHERE kind='passkey'")
    await execute(
        env,
        "UPDATE auth_sessions SET created_at=%s, idle_expires_at=%s, absolute_expires_at=%s, "
        "revoked_at=%s, revoke_reason='logout' WHERE id=%s",
        (
            now - timedelta(days=100),
            now - timedelta(days=40),
            now - timedelta(days=35),
            now - timedelta(days=40),
            session[0]["id"],
        ),
    )
    await execute(
        env,
        "UPDATE auth_events SET created_at=%s WHERE event='token_used'",
        (now - timedelta(days=91),),
    )
    result = await cleanup.run(env["database"], now)
    assert result["sessions"] == 1 and result["events"] >= 1
    assert await rows(env, "SELECT 1 FROM auth_events WHERE event='recovery_completed'")


@pytest.mark.integration
async def test_the_scheduler_tick_runs_cleanup_and_survives_its_failure(env, monkeypatch):
    dispatcher = AsyncMock()
    called = []

    async def fail(database, now):
        called.append(1)
        raise RuntimeError("cleanup broke")

    monkeypatch.setattr(cleanup, "run", fail)
    result = await tick(env["database"], env["settings"], dispatcher)
    assert called and result["created"] == 0


async def schedule_owner(env):
    await env["client"].post("/v1/subscriptions", json={"title": "Technology"})
    await env["client"].put(
        "/v1/delivery-slots", json={"slots": [{"local_time": "00:00", "enabled": True}]}
    )
    await execute(env, "UPDATE profiles SET timezone='UTC'")


@pytest.mark.integration
async def test_scheduler_eligibility_in_production(env, database_url):
    await schedule_owner(env)
    production = production_settings(database_url)
    dispatcher = AsyncMock()
    now = clock.now().replace(hour=12)
    # No active passkey: no job in production, but the local seed owner still works locally.
    assert (await tick(env["database"], production, dispatcher, now))["created"] == 0
    assert await rows(env, "SELECT 1 FROM jobs") == []
    browser, _ = await owner_with_passkey(env)
    assert (await tick(env["database"], production, dispatcher, now))["created"] == 1


@pytest.mark.integration
async def test_scheduler_ignores_a_user_whose_only_passkey_was_revoked(env, database_url):
    browser, _ = await owner_with_passkey(env)
    await schedule_owner(env)
    await execute(env, "UPDATE passkey_credentials SET revoked_at=now()")
    production = production_settings(database_url)
    assert (await tick(env["database"], production, AsyncMock(), clock.now().replace(hour=12)))[
        "created"
    ] == 0


@pytest.mark.integration
async def test_local_scheduler_serves_the_seeded_owner_without_a_passkey(env):
    await schedule_owner(env)
    assert (
        await tick(env["database"], env["settings"], AsyncMock(), clock.now().replace(hour=12))
    )["created"] == 1
