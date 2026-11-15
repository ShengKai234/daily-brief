"""F01 Owner bootstrap and F02 invite-only registration, non-owner gate (design section 7)."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from authkit import (
    Cli,
    bootstrap,
    execute,
    invite,
    login_options,
    one,
    options_body,
    register,
    rows,
    set_gate,
)
from conftest import OWNER
from fakeauth import FakeAuthenticator, b64

from app import auth_admin, clock
from app.security import token_hash

# ---------- F01 ----------


@pytest.mark.integration
async def test_first_bootstrap_creates_the_owner_and_a_one_hour_link(env):
    await execute(env, "DELETE FROM profiles WHERE user_id=%s", (OWNER,))
    out = await Cli(env).run("bootstrap-owner")
    assert "http://localhost:5173/enroll#t=" in out
    secret = out.split("#t=")[1].split()[0]
    profile = await one(env, "SELECT status FROM profiles WHERE user_id=%s", (OWNER,))
    assert profile["status"] == "active"
    runner = await one(env, "SELECT runner FROM agent_settings WHERE user_id=%s", (OWNER,))
    assert runner["runner"] == "claude_subscription"
    token = await one(
        env, "SELECT * FROM enrollment_tokens WHERE purpose='recovery' AND used_at IS NULL"
    )
    assert token["user_id"] == OWNER
    assert timedelta(minutes=59) < token["expires_at"] - token["created_at"] <= timedelta(hours=1)
    # Only the SHA-256 is stored.
    assert bytes(token["token_hash"]) == token_hash(secret)
    assert secret.encode() not in bytes(token["token_hash"])
    event = await one(
        env,
        "SELECT actor FROM auth_events WHERE event='token_issued' AND enrollment_token_id=%s",
        (token["id"],),
    )
    assert event["actor"] == "cli"


@pytest.mark.integration
async def test_bootstrapping_again_supersedes_the_previous_link(env):
    first = await bootstrap(env)
    second = await bootstrap(env)
    browser = env["browser"]()
    response = await browser.post("/v1/auth/registration/options", json=options_body(first))
    assert response.status_code == 400
    assert response.json()["code"] == "enrollment_token_invalid"
    assert (
        await browser.post("/v1/auth/registration/options", json=options_body(second))
    ).status_code == 200


@pytest.mark.unit
async def test_cli_confirmation_names_target_and_aborts_without_yes(env, capsys):
    with pytest.raises(auth_admin.CliError, match="Aborted"):
        await auth_admin.run(
            ["bootstrap-owner"], env_settings=env["settings"], ask=lambda prompt: "no"
        )
    assert "Target: environment=local database=127.0.0.1" in capsys.readouterr().out
    assert await rows(env, "SELECT 1 FROM enrollment_tokens") == []


# ---------- F02 ----------


@pytest.mark.unit
async def test_cli_refuses_invites_while_non_owner_admission_is_off(env):
    with pytest.raises(auth_admin.CliError, match="DESIGN 3.1"):
        await Cli(env).run("issue-invite")
    assert await rows(env, "SELECT 1 FROM enrollment_tokens") == []


@pytest.mark.integration
async def test_issued_invite_stops_working_when_the_gate_closes(env):
    token = await invite(env)
    set_gate(env, False)
    response = await env["browser"]().post(
        "/v1/auth/registration/options", json=options_body(token)
    )
    assert response.status_code == 403
    assert response.json()["code"] == "admission_disabled"
    assert await rows(env, "SELECT 1 FROM webauthn_ceremonies") == []


@pytest.mark.integration
async def test_invited_user_registers_with_the_api_runner_and_a_fresh_session(env):
    token = await invite(env)
    set_gate(env, True)
    browser = env["browser"]()
    auth, verify, options = await register(env, browser, token, display_name="Mei")
    assert verify.status_code == 200, verify.text
    profile = verify.json()
    assert profile["display_name"] == "Mei" and profile["status"] == "active"
    assert "email" not in profile
    assert profile["user_id"] != str(OWNER)
    runner = await one(
        env, "SELECT runner FROM agent_settings WHERE user_id=%s", (profile["user_id"],)
    )
    assert runner["runner"] == "anthropic_api"
    used = await one(
        env, "SELECT used_by FROM enrollment_tokens WHERE token_hash=%s", (token_hash(token),)
    )
    assert str(used["used_by"]) == profile["user_id"]
    session = await one(
        env, "SELECT reauth_at, kind FROM auth_sessions WHERE user_id=%s", (profile["user_id"],)
    )
    assert session["reauth_at"] is None and session["kind"] == "passkey"
    assert (await browser.get("/v1/me")).json()["user_id"] == profile["user_id"]
    # user handle in the options is the new user_id
    public_key = options.json()["public_key"]
    assert public_key["user"]["id"] == b64(__import__("uuid").UUID(profile["user_id"]).bytes)
    assert public_key["authenticatorSelection"]["userVerification"] == "required"
    assert public_key["authenticatorSelection"]["residentKey"] == "required"
    assert public_key["attestation"] == "none"
    assert {p["alg"] for p in public_key["pubKeyCredParams"]} == {-7, -257}


@pytest.mark.integration
async def test_an_invited_user_never_gets_a_subscription_job(env):
    from unittest.mock import AsyncMock

    from app.scheduler import tick

    token = await invite(env)
    set_gate(env, True)
    browser = env["browser"]()
    _, verify, _ = await register(env, browser, token, display_name="Mei")
    user = verify.json()["user_id"]
    async with env["super"].transaction() as db:
        topic = await db.one(
            "INSERT INTO topics (owner_id,title,normalized_key) VALUES (%s,'T','t') RETURNING id",
            (user,),
        )
        await db.execute(
            "INSERT INTO subscriptions (user_id,topic_id) VALUES (%s,%s)", (user, topic["id"])
        )
        await db.execute(
            "INSERT INTO delivery_slots (user_id,local_time,position) VALUES (%s,'00:00',1)",
            (user,),
        )
    await tick(env["database"], env["settings"], AsyncMock())
    assert (
        await rows(
            env, "SELECT 1 FROM jobs WHERE runner='claude_subscription' AND user_id=%s", (user,)
        )
        == []
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "state,status,code",
    [
        ("unknown", 400, "enrollment_token_invalid"),
        ("revoked", 400, "enrollment_token_invalid"),
        ("used", 409, "enrollment_token_used"),
        ("expired", 409, "enrollment_token_expired"),
    ],
)
async def test_an_unusable_token_never_creates_anything(env, state, status, code):
    set_gate(env, True)
    token = await invite(env)
    column = {"revoked": "revoked_at", "used": "used_at"}.get(state)
    if column:
        await execute(env, f"UPDATE enrollment_tokens SET {column}=now()")  # noqa: S608
    elif state == "expired":
        await execute(
            env,
            "UPDATE enrollment_tokens SET created_at=now()-interval '9 days', expires_at=now()-interval '1 day'",
        )
    elif state == "unknown":
        token = "u" * 43
    response = await env["browser"]().post(
        "/v1/auth/registration/options", json=options_body(token)
    )
    assert (response.status_code, response.json()["code"]) == (status, code)
    assert await rows(env, "SELECT 1 FROM webauthn_ceremonies") == []
    assert len(await rows(env, "SELECT 1 FROM profiles")) == 1  # the owner fixture only


@pytest.mark.integration
async def test_token_revoked_between_options_and_verify(env):
    set_gate(env, True)
    token = await invite(env)
    browser = env["browser"]()
    options = (await browser.post("/v1/auth/registration/options", json=options_body(token))).json()
    await execute(env, "UPDATE enrollment_tokens SET revoked_at=now()")
    auth = FakeAuthenticator()
    verify = await browser.post(
        "/v1/auth/registration/verify",
        json={
            "ceremony_id": options["ceremony_id"],
            "credential": auth.create(options["public_key"]),
        },
    )
    assert (verify.status_code, verify.json()["code"]) == (400, "enrollment_token_invalid")
    assert len(await rows(env, "SELECT 1 FROM profiles")) == 1
    assert await rows(env, "SELECT 1 FROM passkey_credentials") == []
    assert (await one(env, "SELECT consumed_at FROM webauthn_ceremonies"))[
        "consumed_at"
    ] is not None


@pytest.mark.integration
async def test_the_gate_closes_between_options_and_verify(env):
    set_gate(env, True)
    token = await invite(env)
    browser = env["browser"]()
    options = (await browser.post("/v1/auth/registration/options", json=options_body(token))).json()
    set_gate(env, False)
    auth = FakeAuthenticator()
    verify = await browser.post(
        "/v1/auth/registration/verify",
        json={
            "ceremony_id": options["ceremony_id"],
            "credential": auth.create(options["public_key"]),
        },
    )
    assert (verify.status_code, verify.json()["code"]) == (403, "admission_disabled")
    assert len(await rows(env, "SELECT 1 FROM profiles")) == 1
    assert (await one(env, "SELECT used_at FROM enrollment_tokens"))["used_at"] is None


@pytest.mark.integration
async def test_the_token_expires_between_options_and_verify(env, monkeypatch):
    set_gate(env, True)
    token = await invite(env)
    await execute(env, "UPDATE enrollment_tokens SET expires_at=now()+interval '2 minutes'")
    browser = env["browser"]()
    options = (await browser.post("/v1/auth/registration/options", json=options_body(token))).json()
    real = clock.now
    monkeypatch.setattr(clock, "now", lambda: real() + timedelta(minutes=3))
    auth = FakeAuthenticator()
    verify = await browser.post(
        "/v1/auth/registration/verify",
        json={
            "ceremony_id": options["ceremony_id"],
            "credential": auth.create(options["public_key"]),
        },
    )
    # The ceremony itself (5 minutes) is still valid; the token (2 minutes) is not.
    assert (verify.status_code, verify.json()["code"]) == (409, "enrollment_token_expired")
    assert len(await rows(env, "SELECT 1 FROM profiles")) == 1
    assert await rows(env, "SELECT 1 FROM passkey_credentials") == []
    assert await rows(env, "SELECT 1 FROM auth_sessions WHERE kind='passkey'") == []
    assert (await one(env, "SELECT used_at FROM enrollment_tokens"))["used_at"] is None


@pytest.mark.integration
async def test_a_crash_before_commit_leaves_nothing_behind(env, monkeypatch):
    set_gate(env, True)
    token = await invite(env)
    browser = env["browser"]()
    options = (await browser.post("/v1/auth/registration/options", json=options_body(token))).json()
    auth = FakeAuthenticator()
    body = {"ceremony_id": options["ceremony_id"], "credential": auth.create(options["public_key"])}

    from app import passkeys

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated crash after the profile insert")

    with monkeypatch.context() as patch:
        patch.setattr(passkeys, "insert_credential", boom)
        with pytest.raises(RuntimeError):
            await browser.post("/v1/auth/registration/verify", json=body, contract=False)
    assert len(await rows(env, "SELECT 1 FROM profiles")) == 1
    assert await rows(env, "SELECT 1 FROM passkey_credentials") == []
    assert (await one(env, "SELECT used_at FROM enrollment_tokens"))["used_at"] is None
    assert (await one(env, "SELECT consumed_at FROM webauthn_ceremonies"))["consumed_at"] is None
    # The same browser retries while token and ceremony are still valid: succeeds exactly once.
    first = await browser.post("/v1/auth/registration/verify", json=body)
    second = await browser.post("/v1/auth/registration/verify", json=body)
    assert first.status_code == 200
    assert (second.status_code, second.json()["code"]) == (400, "ceremony_invalid")


@pytest.mark.integration
async def test_a_lost_verify_response_cannot_be_replayed(env):
    set_gate(env, True)
    token = await invite(env)
    browser = env["browser"]()
    options = (await browser.post("/v1/auth/registration/options", json=options_body(token))).json()
    auth = FakeAuthenticator()
    body = {"ceremony_id": options["ceremony_id"], "credential": auth.create(options["public_key"])}
    assert (await browser.post("/v1/auth/registration/verify", json=body)).status_code == 200
    again = await browser.post("/v1/auth/registration/verify", json=body)
    assert (again.status_code, again.json()["code"]) == (400, "ceremony_invalid")
    assert len(await rows(env, "SELECT 1 FROM profiles")) == 2
    assert len(await rows(env, "SELECT 1 FROM passkey_credentials")) == 1


@pytest.mark.integration
async def test_two_browsers_race_with_the_same_invite(env):
    set_gate(env, True)
    token = await invite(env)
    a, b = env["browser"](), env["browser"]()
    bodies = []
    for browser in (a, b):
        options = (
            await browser.post("/v1/auth/registration/options", json=options_body(token))
        ).json()
        auth = FakeAuthenticator()
        bodies.append(
            {
                "ceremony_id": options["ceremony_id"],
                "credential": auth.create(options["public_key"]),
            }
        )
    results = await asyncio.gather(
        a.post("/v1/auth/registration/verify", json=bodies[0], contract=False),
        b.post("/v1/auth/registration/verify", json=bodies[1], contract=False),
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409]
    loser = next(r for r in results if r.status_code == 409)
    assert loser.json()["code"] == "enrollment_token_used"
    assert len(await rows(env, "SELECT 1 FROM profiles")) == 2
    assert len(await rows(env, "SELECT 1 FROM passkey_credentials")) == 1


@pytest.mark.integration
async def test_the_token_budget_survives_cleanup(env):
    from app import cleanup

    set_gate(env, True)
    token = await invite(env)
    await execute(env, "UPDATE enrollment_tokens SET options_count=10")
    await cleanup.run(env["database"], clock.now())
    response = await env["browser"]().post(
        "/v1/auth/registration/options", json=options_body(token)
    )
    assert (response.status_code, response.json()["code"]) == (429, "rate_limited")


@pytest.mark.integration
async def test_each_options_call_spends_one_attempt_of_ten(env):
    set_gate(env, True)
    token = await invite(env)
    browser = env["browser"]()
    for _ in range(10):
        assert (
            await browser.post("/v1/auth/registration/options", json=options_body(token))
        ).status_code == 200
    eleventh = await browser.post("/v1/auth/registration/options", json=options_body(token))
    assert eleventh.status_code == 429
    assert (await one(env, "SELECT options_count FROM enrollment_tokens"))["options_count"] == 10


@pytest.mark.unit
@pytest.mark.parametrize("value", ["", "x" * 81, "bad\x07name"])
async def test_display_name_is_validated(env, value):
    set_gate(env, True)
    token = await invite(env)
    response = await env["browser"]().post(
        "/v1/auth/registration/options", json=options_body(token, value)
    )
    assert (response.status_code, response.json()["code"]) == (422, "validation_error")
    assert await rows(env, "SELECT 1 FROM webauthn_ceremonies") == []


@pytest.mark.integration
async def test_a_duplicate_credential_id_is_rejected_without_using_the_token(env):
    set_gate(env, True)
    first_token = await invite(env)
    first = await register(env, env["browser"](), first_token, display_name="Mei")
    stolen = first[0]
    second_token = await invite(env)
    _, verify, _ = await register(
        env, env["browser"](), second_token, auth=stolen, display_name="Li"
    )
    assert (verify.status_code, verify.json()["code"]) == (409, "credential_exists")
    state = await one(
        env,
        "SELECT used_at FROM enrollment_tokens WHERE token_hash=%s",
        (token_hash(second_token),),
    )
    assert state["used_at"] is None
    consumed = await rows(env, "SELECT consumed_at FROM webauthn_ceremonies ORDER BY created_at")
    assert all(row["consumed_at"] for row in consumed)


@pytest.mark.integration
async def test_signing_in_never_creates_a_profile(env):
    browser = env["browser"]()
    data = await login_options(browser)
    stranger = FakeAuthenticator()
    credential = stranger.get(data["public_key"], uuid4().bytes)
    response = await browser.post(
        "/v1/auth/login/verify", json={"ceremony_id": data["ceremony_id"], "credential": credential}
    )
    assert (response.status_code, response.json()["code"]) == (401, "authentication_failed")
    assert len(await rows(env, "SELECT 1 FROM profiles")) == 1


@pytest.mark.integration
async def test_registration_rejects_untrusted_origin_and_rp_and_missing_verification(env):
    token = await bootstrap(env)
    cases = [
        {"origin": "https://evil.example"},
        {"rp_id": "evil.example"},
        {"uv": False},
        {"up": False},
        {"client_extra": {"crossOrigin": True}},
        {"client_extra": {"topOrigin": "http://localhost:5173"}},
        {"fmt": "packed"},
        {"be": False, "bs": True},
    ]
    for kw in cases:
        browser = env["browser"]()
        _, verify, _ = await register(env, browser, token, **kw)
        assert (verify.status_code, verify.json()["code"]) == (400, "registration_failed"), kw
    assert await rows(env, "SELECT 1 FROM passkey_credentials") == []
    assert (await one(env, "SELECT used_at FROM enrollment_tokens"))["used_at"] is None
