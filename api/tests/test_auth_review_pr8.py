"""Codex review of PR #8 (REVIEWS.md 8-2 .. 8-7): real PostgreSQL concurrency and adapter tests."""

import asyncio
import base64
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


@pytest.mark.integration
async def test_revoke_token_and_recovery_do_not_deadlock(env):
    """8-3: revoke-token must lock the profile before the token (design 5.6 order)."""
    from authkit import issue_add, issue_recovery

    from app import auth_admin

    browser, _ = await owner_with_passkey(env)
    add_token = await issue_add(env, OWNER)
    recovery = await issue_recovery(env, OWNER)
    add_id = (await rows(env, "SELECT id FROM enrollment_tokens WHERE purpose='add_credential'"))[
        0
    ]["id"]
    fresh = env["browser"]()
    options = (
        await fresh.post("/v1/auth/registration/options", json=options_body(recovery))
    ).json()
    body = {
        "ceremony_id": options["ceremony_id"],
        "credential": FakeAuthenticator().create(options["public_key"]),
    }
    del add_token
    async with env["super"].transaction() as holder:
        # Recovery will take profile, token, sessions and credentials, then wait for the ceremony.
        await holder.execute(
            "SELECT 1 FROM webauthn_ceremonies WHERE id = %s FOR UPDATE", (options["ceremony_id"],)
        )
        recovering = asyncio.create_task(
            fresh.post("/v1/auth/registration/verify", json=body, contract=False)
        )
        await asyncio.sleep(0.7)
        revoking = asyncio.create_task(Cli(env).run("revoke-token", str(add_id)))
        await asyncio.sleep(0.7)
    response, cli = await asyncio.gather(recovering, revoking, return_exceptions=True)
    assert not isinstance(response, Exception), repr(response)
    assert response.status_code == 200, response.text
    # The token was already revoked by the recovery, so the CLI reports that cleanly.
    assert isinstance(cli, auth_admin.CliError) or isinstance(cli, str), repr(cli)


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@db.example.com/x",
        "postgresql://u:p@db.example.com/x?sslmode=require",
        "postgresql://u:p@db.example.com/x?sslmode=disable",
        "postgresql://u:p@db.example.com/x?sslmode=verify-full&sslrootcert=/etc/ca.pem",
    ],
)
async def test_production_connections_verify_the_server_identity(monkeypatch, url):
    """8-4: DB identity is verified (verify-full); a weaker URL setting cannot lower it."""
    from conftest import production_settings

    from app import database as database_module

    captured = {}

    class Stop(Exception):
        pass

    async def fake_connect(conninfo, **kwargs):
        captured["conninfo"], captured["kwargs"] = conninfo, kwargs
        raise Stop

    monkeypatch.setattr(database_module.AsyncConnection, "connect", fake_connect)
    settings = production_settings(url, database_sslmode="")
    with pytest.raises(Stop):
        async with database_module.Database(settings, url).transaction():
            pass
    import psycopg.conninfo

    merged = psycopg.conninfo.conninfo_to_dict(captured["conninfo"], **captured["kwargs"])
    assert merged["sslmode"] == "verify-full"
    if "sslrootcert" in url:
        assert merged["sslrootcert"] == "/etc/ca.pem"


def function_url_event(path, body="{}", method="POST", origin="http://localhost:5173"):
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"origin": origin, "content-type": "application/json"},
        "requestContext": {
            "http": {
                "method": method,
                "path": path,
                "sourceIp": "127.0.0.1",
                "protocol": "HTTP/1.1",
            }
        },
        "body": body,
        "isBase64Encoded": False,
    }


@pytest.mark.integration
async def test_unexpected_failures_are_contained_before_the_adapter_logs(env, monkeypatch, caplog):
    """8-5: Mangum probe: a failure carrying a secret gives sanitized no-store problem+json."""
    import json
    import logging

    from mangum import Mangum

    from app import passkeys

    secret = "CHALLENGE-SECRET-8f3a"

    async def explode(*args, **kwargs):
        raise RuntimeError(f"DETAIL: Failing row contains ({secret})")

    monkeypatch.setattr(passkeys, "create_ceremony", explode)
    handler = Mangum(env["app"], lifespan="off")
    caplog.set_level(logging.DEBUG)

    def invoke():
        asyncio.set_event_loop(asyncio.new_event_loop())
        return handler(function_url_event("/api/v1/auth/login/options"), None)

    result = await asyncio.to_thread(invoke)
    assert result["statusCode"] == 500
    headers = {k.lower(): v for k, v in result["headers"].items()}
    assert headers["content-type"].startswith("application/problem+json")
    assert headers["cache-control"] == "no-store"
    body = result["body"]
    if result.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    assert json.loads(body)["code"] == "internal_error"
    assert secret not in body
    assert secret not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.integration
async def test_malformed_transports_fail_registration_in_a_controlled_way(env):
    """8-6: a non-list `transports` is a registration failure that consumes the ceremony."""
    token = await Cli(env).token("bootstrap-owner")
    browser = env["browser"]()
    options = (await browser.post("/v1/auth/registration/options", json=options_body(token))).json()
    credential = FakeAuthenticator().create(options["public_key"])
    credential["response"]["transports"] = 42
    response = await browser.post(
        "/v1/auth/registration/verify",
        json={"ceremony_id": options["ceremony_id"], "credential": credential},
        contract=False,
    )
    assert (response.status_code, response.json()["code"]) == (400, "registration_failed")
    consumed = await rows(env, "SELECT consumed_at FROM webauthn_ceremonies")
    assert all(row["consumed_at"] for row in consumed)
    assert await rows(env, "SELECT 1 FROM passkey_credentials") == []


@pytest.mark.integration
async def test_the_five_ceremony_cap_holds_under_concurrent_options(env):
    """8-7: simultaneous reauth and add-passkey options must never leave more than five open."""
    browser, auth = await owner_with_passkey(env)
    assert (await reauth(browser, auth, OWNER)).status_code == 204
    calls = []
    for index in range(16):
        path = "/v1/auth/reauth/options" if index % 2 else "/v1/me/passkeys/options"
        calls.append(browser.post(path, contract=False))
    responses = await asyncio.gather(*calls)
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    open_ceremonies = await rows(
        env,
        "SELECT 1 FROM webauthn_ceremonies WHERE session_id IS NOT NULL "
        "AND consumed_at IS NULL AND expires_at > now()",
    )
    assert len(open_ceremonies) <= 5


@pytest.mark.integration
async def test_session_revocation_does_not_deadlock_with_options(env):
    """8-8: every session write locks the profile first (design 5.6), including revocation."""
    from authkit import sign_in

    mac, auth = await owner_with_passkey(env)
    phone = env["browser"]()
    assert (await sign_in(phone, auth, OWNER)).status_code == 200
    target = next(
        s for s in (await mac.get("/v1/me/sessions")).json()["sessions"] if not s["is_current"]
    )
    async with env["super"].transaction() as holder:
        # Plays options: it owns the profile and then wants the session row.
        await holder.execute("SELECT 1 FROM profiles WHERE user_id = %s FOR UPDATE", (OWNER,))
        revoking = asyncio.create_task(
            mac.delete(f"/v1/me/sessions/{target['id']}", contract=False)
        )
        await asyncio.sleep(0.7)
        locking = asyncio.create_task(
            holder.execute("SELECT 1 FROM auth_sessions WHERE id = %s FOR UPDATE", (target["id"],))
        )
        await asyncio.sleep(2)
    response, locked = await asyncio.gather(revoking, locking, return_exceptions=True)
    assert not isinstance(locked, Exception), repr(locked)
    assert not isinstance(response, Exception), repr(response)
    assert response.status_code == 204, response.text
    reason = await rows(
        env, "SELECT revoke_reason FROM auth_sessions WHERE id = %s", (target["id"],)
    )
    assert reason[0]["revoke_reason"] == "user_revoked"
