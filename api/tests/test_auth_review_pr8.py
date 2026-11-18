"""Codex review of PR #8 (REVIEWS.md 8-2 .. 8-7): real PostgreSQL concurrency and adapter tests."""

import asyncio
from datetime import timedelta

import pytest
from authkit import (
    Cli,
    execute,
    invite,
    options_body,
    owner_with_passkey,
    reauth,
    rows,
    set_gate,
)
from conftest import OWNER
from fakeauth import FakeAuthenticator

from app import clock


class Offset:
    """A clock that tests can push forward while a request is blocked on a database lock."""

    def __init__(self, monkeypatch):
        self.seconds = 0
        real = clock.now
        monkeypatch.setattr(clock, "now", lambda: real() + timedelta(seconds=self.seconds))


async def blocked_then_expired(env, offset, lock_sql, params, request):
    """Run `request` while another connection holds `lock_sql`; jump the clock; release."""
    async with env["super"].transaction() as holder:
        await holder.execute(lock_sql, params)
        task = asyncio.create_task(request())
        await asyncio.sleep(0.7)
        assert not task.done(), "the request must be waiting on the lock"
        offset.seconds = 400
    return await task


@pytest.mark.integration
async def test_ceremony_expiry_is_rechecked_with_fresh_time_after_the_lock(env, monkeypatch):
    """8-2: a verify blocked on a lock must not succeed on a ceremony that expired meanwhile."""
    offset = Offset(monkeypatch)
    token = await Cli(env).token("bootstrap-owner")
    await execute(env, "UPDATE enrollment_tokens SET expires_at = now() + interval '1 hour'")
    browser = env["browser"]()
    options = (await browser.post("/v1/auth/registration/options", json=options_body(token))).json()
    body = {
        "ceremony_id": options["ceremony_id"],
        "credential": FakeAuthenticator().create(options["public_key"]),
    }
    response = await blocked_then_expired(
        env,
        offset,
        "SELECT 1 FROM profiles WHERE user_id = %s FOR UPDATE",
        (OWNER,),
        lambda: browser.post("/v1/auth/registration/verify", json=body, contract=False),
    )
    assert (response.status_code, response.json()["code"]) == (400, "ceremony_invalid")
    assert await rows(env, "SELECT 1 FROM passkey_credentials") == []


@pytest.mark.integration
async def test_token_expiry_is_rechecked_with_fresh_time_after_the_lock(env, monkeypatch):
    offset = Offset(monkeypatch)
    set_gate(env, True)
    token = await invite(env)
    browser = env["browser"]()
    options = (await browser.post("/v1/auth/registration/options", json=options_body(token))).json()
    # The token dies 1 minute from now, the ceremony only after 5: only the token check can catch it.
    await execute(env, "UPDATE enrollment_tokens SET expires_at = now() + interval '1 minute'")
    body = {
        "ceremony_id": options["ceremony_id"],
        "credential": FakeAuthenticator().create(options["public_key"]),
    }

    def after_ceremony_is_still_valid():
        offset.seconds = 120

    async with env["super"].transaction() as holder:
        await holder.execute("SELECT 1 FROM enrollment_tokens FOR UPDATE")
        task = asyncio.create_task(
            browser.post("/v1/auth/registration/verify", json=body, contract=False)
        )
        await asyncio.sleep(0.7)
        assert not task.done()
        after_ceremony_is_still_valid()
    response = await task
    assert (response.status_code, response.json()["code"]) == (409, "enrollment_token_expired")
    assert len(await rows(env, "SELECT 1 FROM profiles")) == 1


@pytest.mark.integration
async def test_reauth_window_is_rechecked_with_fresh_time_after_the_lock(env, monkeypatch):
    """8-2: the 5-minute reauth window is judged after the locks, not before."""
    offset = Offset(monkeypatch)
    browser, auth = await owner_with_passkey(env)
    assert (await reauth(browser, auth, OWNER)).status_code == 204
    options = (await browser.post("/v1/me/passkeys/options")).json()
    body = {
        "ceremony_id": options["ceremony_id"],
        "credential": FakeAuthenticator().create(options["public_key"]),
    }
    async with env["super"].transaction() as holder:
        await holder.execute("SELECT 1 FROM profiles WHERE user_id = %s FOR UPDATE", (OWNER,))
        task = asyncio.create_task(
            browser.post("/v1/me/passkeys/verify", json=body, contract=False)
        )
        await asyncio.sleep(0.7)
        assert not task.done()
        offset.seconds = 301
    response = await task
    assert (response.status_code, response.json()["code"]) in {
        (403, "reauth_required"),
        (400, "ceremony_invalid"),
    }
    assert len(await rows(env, "SELECT 1 FROM passkey_credentials")) == 1
