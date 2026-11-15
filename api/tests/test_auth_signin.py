"""F03 passkey sign-in and F04 session lifecycle (design section 7)."""

import secrets
from datetime import timedelta
from uuid import uuid4

import pytest
from authkit import (
    execute,
    login_options,
    one,
    owner_with_passkey,
    rows,
    sign_in,
)
from conftest import OWNER
from fakeauth import FakeAuthenticator

from app import clock, passkeys
from app.security import token_hash
from app.webauthn_wrapper import WebAuthnFailure, apply_counter_rule, verify_assertion

HANDLE = OWNER.bytes


async def fresh_browser_with_owner(env, **auth_kw):
    _, auth = await owner_with_passkey(env, **auth_kw)
    return env["browser"](), auth


# ---------- F03 ----------


@pytest.mark.integration
async def test_discoverable_sign_in_creates_a_new_session(env):
    browser, auth = await fresh_browser_with_owner(env)
    data = await login_options(browser)
    public_key = data["public_key"]
    assert public_key["allowCredentials"] == []
    assert public_key["userVerification"] == "required"
    assert public_key["rpId"] == "localhost"
    response = await sign_in(browser, auth, OWNER)
    assert response.status_code == 200
    assert response.json()["user_id"] == str(OWNER)
    cookie = browser.cookies[env["settings"].session_cookie]
    session = await one(
        env, "SELECT * FROM auth_sessions WHERE token_hash=%s", (token_hash(cookie),)
    )
    assert session["reauth_at"] is None
    assert len(cookie) >= 43
    credential = await one(env, "SELECT last_used_at, sign_count FROM passkey_credentials")
    assert credential["last_used_at"] is not None and credential["sign_count"] == 1
    assert await one(env, "SELECT 1 AS n FROM auth_events WHERE event='login_succeeded'")
    # A second sign-in yields a different token.
    again = env["browser"]()
    await sign_in(again, auth, OWNER)
    assert again.cookies[env["settings"].session_cookie] != cookie


DEFECTS = {
    "origin": {"origin": "https://evil.example"},
    "cross_origin": {"client_extra": {"crossOrigin": True}},
    "top_origin": {"client_extra": {"topOrigin": "http://localhost:5173"}},
    "rp_hash": {"rp_id": "evil.example"},
    "challenge": {"challenge": secrets.token_bytes(32)},
    "no_presence": {"up": False},
    "no_verification": {"uv": False},
    "no_user_handle": {"drop_handle": True},
    "bad_signature": {"bad_signature": True},
    "backup_eligible_changed": {"be": True},
    "backup_state_without_eligible": {"bs": True},
}


@pytest.mark.integration
@pytest.mark.parametrize("defect", sorted(DEFECTS))
async def test_assertion_checks_fail_closed(env, defect):
    browser, auth = await fresh_browser_with_owner(env)
    before = await rows(env, "SELECT id FROM auth_sessions")
    response = await sign_in(browser, auth, OWNER, **DEFECTS[defect])
    assert (response.status_code, response.json()["code"]) == (401, "authentication_failed")
    assert await rows(env, "SELECT id FROM auth_sessions") == before
    assert env["settings"].session_cookie not in browser.cookies
    ceremony = await one(env, "SELECT consumed_at FROM webauthn_ceremonies WHERE purpose='login'")
    assert ceremony["consumed_at"] is not None


@pytest.mark.integration
async def test_every_failed_proof_returns_the_same_body(env):
    browser, auth = await fresh_browser_with_owner(env)
    bodies = set()
    cases = [DEFECTS[name] for name in sorted(DEFECTS)]
    cases.append({"user_handle": uuid4().bytes})
    for case in cases:
        case = dict(case)
        handle = case.pop("user_handle", HANDLE)
        data = await login_options(browser)
        credential = auth.get(data["public_key"], handle, **case)
        response = await browser.post(
            "/v1/auth/login/verify",
            json={"ceremony_id": data["ceremony_id"], "credential": credential},
        )
        bodies.add(response.text)
        assert response.status_code == 401
    # revoked and unknown credential ids
    await execute(env, "UPDATE passkey_credentials SET revoked_at=now()")
    revoked = await sign_in(browser, auth, OWNER)
    unknown = await sign_in(browser, FakeAuthenticator(), OWNER)
    bodies |= {revoked.text, unknown.text}
    assert revoked.status_code == unknown.status_code == 401
    assert len(bodies) == 1


@pytest.mark.integration
async def test_the_user_handle_must_match_the_credentials_user(env):
    browser, auth = await fresh_browser_with_owner(env)
    data = await login_options(browser)
    credential = auth.get(data["public_key"], uuid4().bytes)
    response = await browser.post(
        "/v1/auth/login/verify", json={"ceremony_id": data["ceremony_id"], "credential": credential}
    )
    assert (response.status_code, response.json()["code"]) == (401, "authentication_failed")


@pytest.mark.integration
async def test_a_wrong_browser_cannot_consume_someone_elses_ceremony(env):
    browser_a, auth = await fresh_browser_with_owner(env)
    data = await login_options(browser_a)
    credential = auth.get(data["public_key"], HANDLE)
    body = {"ceremony_id": data["ceremony_id"], "credential": credential}
    # Browser B has no binding cookie at all, then a binding of its own.
    stranger = env["browser"]()
    probe = await stranger.post("/v1/auth/login/verify", json=body)
    assert probe.json()["code"] == "ceremony_invalid"
    await login_options(stranger)
    response = await stranger.post("/v1/auth/login/verify", json=body)
    assert (response.status_code, response.json()["code"]) == (400, "ceremony_invalid")
    row = await one(
        env, "SELECT consumed_at FROM webauthn_ceremonies WHERE id=%s", (data["ceremony_id"],)
    )
    assert row["consumed_at"] is None
    assert (await browser_a.post("/v1/auth/login/verify", json=body)).status_code == 200


@pytest.mark.integration
async def test_two_tabs_in_one_browser(env):
    browser, auth = await fresh_browser_with_owner(env)
    tab1 = await login_options(browser)
    binding = browser.cookies[env["settings"].binding_cookie]
    tab2 = await login_options(browser)
    assert browser.cookies[env["settings"].binding_cookie] == binding
    first = await browser.post(
        "/v1/auth/login/verify",
        json={
            "ceremony_id": tab1["ceremony_id"],
            "credential": auth.get(tab1["public_key"], HANDLE),
        },
    )
    assert first.status_code == 200
    second = await browser.post(
        "/v1/auth/login/verify",
        json={
            "ceremony_id": tab2["ceremony_id"],
            "credential": auth.get(tab2["public_key"], HANDLE),
        },
    )
    assert second.status_code == 200


@pytest.mark.integration
async def test_replay_and_expiry(env, monkeypatch):
    browser, auth = await fresh_browser_with_owner(env)
    data = await login_options(browser)
    body = {"ceremony_id": data["ceremony_id"], "credential": auth.get(data["public_key"], HANDLE)}
    assert (await browser.post("/v1/auth/login/verify", json=body)).status_code == 200
    replay = await browser.post("/v1/auth/login/verify", json=body)
    assert (replay.status_code, replay.json()["code"]) == (400, "ceremony_invalid")
    old = await login_options(browser)
    real = clock.now
    monkeypatch.setattr(clock, "now", lambda: real() + timedelta(minutes=6))
    expired = await browser.post(
        "/v1/auth/login/verify",
        json={"ceremony_id": old["ceremony_id"], "credential": auth.get(old["public_key"], HANDLE)},
    )
    assert (expired.status_code, expired.json()["code"]) == (400, "ceremony_invalid")


@pytest.mark.integration
@pytest.mark.parametrize(
    "created,endpoint",
    [
        ("login", "registration/verify"),
        ("reauth", "login/verify"),
        ("login", "reauth/verify"),
        ("add_passkey", "login/verify"),
    ],
)
async def test_a_ceremony_only_works_at_its_own_endpoint(env, created, endpoint):
    owner_browser, auth = await owner_with_passkey(env)
    if created == "login":
        # reauth/verify needs a signed-in browser; its own binding cookie makes the ceremony its own.
        browser = owner_browser if endpoint == "reauth/verify" else env["browser"]()
        data = await login_options(browser)
    elif created == "reauth":
        browser = owner_browser
        data = (await browser.post("/v1/auth/reauth/options")).json()
    else:
        browser = owner_browser
        from authkit import reauth

        assert (await reauth(browser, auth, OWNER)).status_code == 204
        data = (await browser.post("/v1/me/passkeys/options")).json()
    before = len(await rows(env, "SELECT 1 FROM auth_sessions"))
    credential = (
        FakeAuthenticator().create(data["public_key"])
        if endpoint == "registration/verify"
        else auth.get(data["public_key"], HANDLE)
    )
    response = await browser.post(
        f"/v1/auth/{endpoint}", json={"ceremony_id": data["ceremony_id"], "credential": credential}
    )
    assert (response.status_code, response.json()["code"]) == (400, "ceremony_invalid")
    assert len(await rows(env, "SELECT 1 FROM auth_sessions")) == before
    assert len(await rows(env, "SELECT 1 FROM passkey_credentials")) == 1
    assert (
        await one(
            env, "SELECT consumed_at FROM webauthn_ceremonies WHERE id=%s", (data["ceremony_id"],)
        )
    )["consumed_at"] is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "stored,be,new,result",
    [
        (0, True, 0, "ok"),
        (41, False, 42, "ok"),
        (41, False, 12, "regression_denied"),
        (41, False, 41, "regression_denied"),
        (41, True, 12, "regression_allowed"),
        (0, False, 0, "ok"),
        (41, True, 0, "regression_allowed"),
        (0, False, 1, "ok"),
    ],
)
def test_counter_rule_table(stored, be, new, result):
    assert apply_counter_rule(stored, new, be) == result


@pytest.mark.integration
@pytest.mark.parametrize(
    "stored,be,new,ok,event",
    [
        (0, True, 0, True, False),
        (41, False, 42, True, False),
        (41, False, 12, False, True),
        (41, False, 41, False, True),
        (41, True, 12, True, True),
    ],
)
async def test_signature_counter_rules_over_http(env, stored, be, new, ok, event):
    kw = {"backup_eligible": True, "counts": False} if be else {}
    browser, auth = await fresh_browser_with_owner(env, **kw)
    await execute(env, "UPDATE passkey_credentials SET sign_count=%s", (stored,))
    response = await sign_in(browser, auth, OWNER, count=new)
    assert (response.status_code == 200) is ok
    if not ok:
        assert response.json()["code"] == "authentication_failed"
    regressions = await rows(env, "SELECT 1 FROM auth_events WHERE event='sign_count_regression'")
    assert bool(regressions) is event
    expected = max(stored, new) if ok else stored
    assert (await one(env, "SELECT sign_count FROM passkey_credentials"))["sign_count"] == expected
    # Never auto-revoked.
    assert (await one(env, "SELECT revoked_at FROM passkey_credentials"))["revoked_at"] is None


@pytest.mark.unit
def test_wrapper_passes_zero_to_the_library_so_the_counter_never_rejects_early(env_settings=None):
    """py_webauthn rejects on the counter before the signature; the wrapper hides that (5.7)."""
    from conftest import make_settings

    settings = make_settings("postgresql://postgres@127.0.0.1:1/x")
    auth = FakeAuthenticator()
    public_key = {"challenge": "A" * 43}
    from fakeauth import unb64

    challenge = unb64(public_key["challenge"])
    credential = auth.get(public_key, HANDLE, count=7)
    assertion = verify_assertion(settings, credential, challenge, auth.cose_key())
    assert assertion.sign_count == 7
    tampered = auth.get(public_key, HANDLE, count=7, bad_signature=True)
    with pytest.raises(WebAuthnFailure):
        verify_assertion(settings, tampered, challenge, auth.cose_key())


@pytest.mark.integration
async def test_sign_in_racing_with_credential_revocation(env, monkeypatch):
    browser, auth = await fresh_browser_with_owner(env)
    real = passkeys.commit_login

    async def revoke_then_commit(*args, **kwargs):
        await execute(env, "UPDATE passkey_credentials SET revoked_at=now()")
        return await real(*args, **kwargs)

    monkeypatch.setattr(passkeys, "commit_login", revoke_then_commit)
    before = await rows(env, "SELECT id FROM auth_sessions")
    response = await sign_in(browser, auth, OWNER)
    assert (response.status_code, response.json()["code"]) == (401, "authentication_failed")
    assert await rows(env, "SELECT id FROM auth_sessions") == before


@pytest.mark.integration
async def test_disabled_account_with_a_valid_passkey(env):
    browser, auth = await fresh_browser_with_owner(env)
    await execute(env, "UPDATE profiles SET status='disabled'")
    before = await rows(env, "SELECT id FROM auth_sessions")
    response = await sign_in(browser, auth, OWNER)
    assert (response.status_code, response.json()["code"]) == (403, "account_disabled")
    assert await rows(env, "SELECT id FROM auth_sessions") == before


# ---------- F04 ----------


@pytest.mark.integration
async def test_every_request_checks_session_credential_and_profile(env):
    browser, auth = await owner_with_passkey(env)
    assert (await browser.get("/v1/reports")).status_code == 200
    await execute(env, "UPDATE profiles SET status='disabled'")
    response = await browser.get("/v1/reports")
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")
    await execute(env, "UPDATE profiles SET status='active'")
    assert (await browser.get("/v1/reports")).status_code == 200
    await execute(env, "UPDATE passkey_credentials SET revoked_at=now()")
    assert (await browser.get("/v1/reports", contract=False)).status_code == 401


@pytest.mark.integration
async def test_a_passkey_session_must_match_the_credentials_owner(env):
    browser, auth = await owner_with_passkey(env)
    other = uuid4()
    await execute(env, "INSERT INTO profiles (user_id) VALUES (%s)", (other,))
    await execute(env, "UPDATE passkey_credentials SET user_id=%s", (other,))
    assert (await browser.get("/v1/reports", contract=False)).status_code == 401


@pytest.mark.integration
async def test_logout_takes_effect_immediately(env):
    browser, _ = await owner_with_passkey(env)
    old = dict(browser.cookies)
    response = await browser.post("/v1/auth/logout")
    assert response.status_code == 204
    header = next(
        h for h in response.headers.get_list("set-cookie") if env["settings"].session_cookie in h
    )
    assert "Max-Age=0" in header and "HttpOnly" in header and "Path=/" in header
    assert "SameSite=strict" in header.replace("Strict", "strict")
    stale = env["browser"]()
    stale.cookies = old
    assert (await stale.get("/v1/me", contract=False)).status_code == 401


@pytest.mark.integration
async def test_idle_renewal_and_expiry_with_a_controlled_clock(env):
    browser, _ = await owner_with_passkey(env)
    cookie = browser.cookies[env["settings"].session_cookie]
    ident = token_hash(cookie)
    now = clock.now()
    await execute(
        env,
        "UPDATE auth_sessions SET last_seen_at=%s, idle_expires_at=%s WHERE token_hash=%s",
        (now - timedelta(hours=2), now + timedelta(days=5), ident),
    )
    assert (await browser.get("/v1/me")).status_code == 200
    row = await one(env, "SELECT * FROM auth_sessions WHERE token_hash=%s", (ident,))
    assert abs((row["last_seen_at"] - clock.now()).total_seconds()) < 30
    assert timedelta(days=29) < row["idle_expires_at"] - clock.now() <= timedelta(days=30)
    # Within one hour nothing is written.
    seen = row["last_seen_at"]
    await browser.get("/v1/me")
    assert (await one(env, "SELECT last_seen_at FROM auth_sessions WHERE token_hash=%s", (ident,)))[
        "last_seen_at"
    ] == seen
    # An expired idle window is rejected and not extended.
    expired = now - timedelta(minutes=1)
    await execute(
        env,
        "UPDATE auth_sessions SET idle_expires_at=%s, last_seen_at=%s, created_at=%s WHERE token_hash=%s",
        (expired, now - timedelta(days=31), now - timedelta(days=32), ident),
    )
    assert (await browser.get("/v1/me", contract=False)).status_code == 401
    assert (
        await one(env, "SELECT idle_expires_at FROM auth_sessions WHERE token_hash=%s", (ident,))
    )["idle_expires_at"] == expired


@pytest.mark.integration
async def test_idle_extension_is_capped_by_the_absolute_lifetime(env):
    browser, _ = await owner_with_passkey(env)
    ident = token_hash(browser.cookies[env["settings"].session_cookie])
    now = clock.now()
    await execute(
        env,
        "UPDATE auth_sessions SET last_seen_at=%s, idle_expires_at=%s, absolute_expires_at=%s "
        "WHERE token_hash=%s",
        (now - timedelta(hours=3), now + timedelta(days=2), now + timedelta(days=3), ident),
    )
    await browser.get("/v1/me")
    row = await one(
        env,
        "SELECT idle_expires_at, absolute_expires_at FROM auth_sessions WHERE token_hash=%s",
        (ident,),
    )
    assert row["idle_expires_at"] == row["absolute_expires_at"]


@pytest.mark.integration
async def test_absolute_expiry_even_when_active(env):
    browser, _ = await owner_with_passkey(env)
    ident = token_hash(browser.cookies[env["settings"].session_cookie])
    now = clock.now()
    await execute(
        env,
        "UPDATE auth_sessions SET created_at=%s, idle_expires_at=%s, absolute_expires_at=%s, "
        "last_seen_at=%s WHERE token_hash=%s",
        (now - timedelta(days=91), now - timedelta(days=2), now - timedelta(days=1), now, ident),
    )
    assert (await browser.get("/v1/me", contract=False)).status_code == 401


@pytest.mark.integration
async def test_revoking_one_of_my_sessions(env):
    mac, auth = await owner_with_passkey(env)
    phone = env["browser"]()
    assert (await sign_in(phone, auth, OWNER)).status_code == 200
    sessions = (await mac.get("/v1/me/sessions")).json()["sessions"]
    assert len(sessions) == 2
    current = [s for s in sessions if s["is_current"]]
    assert len(current) == 1
    other = next(s for s in sessions if not s["is_current"])
    assert (await mac.delete(f"/v1/me/sessions/{other['id']}")).status_code == 204
    assert (await phone.get("/v1/me", contract=False)).status_code == 401
    assert (await mac.get("/v1/me")).status_code == 200
    # Deleting the current session is a logout and clears the cookie.
    response = await mac.delete(f"/v1/me/sessions/{current[0]['id']}")
    assert response.status_code == 204
    assert env["settings"].session_cookie not in mac.cookies


@pytest.mark.integration
async def test_another_users_ids_are_not_found(env):
    browser, auth = await owner_with_passkey(env)
    other = uuid4()
    await execute(env, "INSERT INTO profiles (user_id) VALUES (%s)", (other,))
    stranger = await env["as_user"](other)
    own_session = (await browser.get("/v1/me/sessions")).json()["sessions"][0]["id"]
    passkey = (await browser.get("/v1/me/passkeys")).json()["passkeys"][0]["id"]
    before = await rows(env, "SELECT id, revoked_at FROM auth_sessions ORDER BY id")
    assert (await stranger.delete(f"/v1/me/sessions/{own_session}")).json()["code"] == "not_found"
    from authkit import execute as ex

    await ex(env, "UPDATE auth_sessions SET reauth_at=now() WHERE user_id=%s", (other,))
    response = await stranger.delete(f"/v1/me/passkeys/{passkey}")
    assert (response.status_code, response.json()["code"]) == (404, "not_found")
    assert await rows(env, "SELECT id, revoked_at FROM auth_sessions ORDER BY id") == before
    assert (await one(env, "SELECT revoked_at FROM passkey_credentials"))["revoked_at"] is None


@pytest.mark.integration
async def test_re_enabling_does_not_revive_anything(env):
    from authkit import Cli

    browser, _ = await owner_with_passkey(env)
    other = uuid4()
    await execute(env, "INSERT INTO profiles (user_id) VALUES (%s)", (other,))
    user = await env["as_user"](other)
    cli = Cli(env)
    from dataclasses import replace

    gate_open = replace(env["settings"], non_owner_admission=True)
    token = await cli.token("issue-recovery", "--user", str(other), settings=gate_open)
    await cli.run("disable-user", str(other))
    await cli.run("enable-user", str(other))
    assert (await user.get("/v1/me", contract=False)).status_code == 401
    row = await one(
        env, "SELECT revoked_at FROM enrollment_tokens WHERE token_hash=%s", (token_hash(token),)
    )
    assert row["revoked_at"] is not None
    assert (await one(env, "SELECT status FROM profiles WHERE user_id=%s", (other,)))[
        "status"
    ] == "active"
    assert (await browser.get("/v1/me")).status_code == 200


@pytest.mark.unit
async def test_session_list_does_not_expose_secrets(env):
    browser, _ = await owner_with_passkey(env)
    for entry in (await browser.get("/v1/me/sessions")).json()["sessions"]:
        assert set(entry) == {"id", "created_at", "last_seen_at", "is_current"}
    for entry in (await browser.get("/v1/me/passkeys")).json()["passkeys"]:
        assert set(entry) == {
            "id",
            "label",
            "created_at",
            "last_used_at",
            "backup_eligible",
            "backup_state",
            "is_current",
        }


@pytest.mark.integration
async def test_duplicate_session_cookies_are_rejected(env):
    browser, _ = await owner_with_passkey(env)
    name = env["settings"].session_cookie
    value = browser.cookies[name]
    response = await env["browser"]().get(
        "/v1/me", headers={"Cookie": f"{name}={value}; {name}={value}"}, contract=False
    )
    assert response.status_code == 401
