"""F05 reauthentication, F06 passkey management, F07 recovery (design section 7)."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from authkit import (
    Cli,
    add_passkey,
    cli_settings,
    execute,
    invited_user,
    issue_add,
    issue_recovery,
    login_options,
    one,
    options_body,
    owner_with_passkey,
    reauth,
    register,
    rows,
    seed_credential,
    set_gate,
    sign_in,
)
from conftest import OWNER
from fakeauth import FakeAuthenticator, b64

from app import cleanup, clock
from app.security import token_hash

HANDLE = OWNER.bytes


async def two_passkeys(env):
    """Owner with passkeys P (first) and Q, signed in with P and freshly reauthenticated."""
    browser, p = await owner_with_passkey(env)
    assert (await reauth(browser, p, OWNER)).status_code == 204
    q = FakeAuthenticator()
    _, verify = await add_passkey(browser, q)
    assert verify.status_code == 201, verify.text
    return browser, p, q


# ---------- F05 ----------


@pytest.mark.integration
async def test_deleting_a_passkey_needs_a_recent_reauth(env, monkeypatch):
    browser, p, q = await two_passkeys(env)
    ident = (await browser.get("/v1/me/passkeys")).json()["passkeys"]
    target = next(x for x in ident if not x["is_current"])["id"]
    real = clock.now
    monkeypatch.setattr(clock, "now", lambda: real() + timedelta(hours=2))
    denied = await browser.delete(f"/v1/me/passkeys/{target}")
    assert (denied.status_code, denied.json()["code"]) == (403, "reauth_required")
    assert (await reauth(browser, p, OWNER)).status_code == 204
    assert (await browser.delete(f"/v1/me/passkeys/{target}")).status_code == 204
    assert (await one(env, "SELECT revoked_at FROM passkey_credentials WHERE id=%s", (target,)))[
        "revoked_at"
    ]


@pytest.mark.unit
async def test_reauth_with_another_users_credential_fails(env):
    browser_1, p1 = await owner_with_passkey(env)
    other = uuid4()
    await execute(env, "INSERT INTO profiles (user_id) VALUES (%s)", (other,))
    p2 = FakeAuthenticator()
    await seed_credential(env, other, p2)
    options = (await browser_1.post("/v1/auth/reauth/options")).json()
    credential = p2.get(options["public_key"], other.bytes)
    response = await browser_1.post(
        "/v1/auth/reauth/verify",
        json={"ceremony_id": options["ceremony_id"], "credential": credential},
    )
    assert (response.status_code, response.json()["code"]) == (401, "authentication_failed")
    assert (await one(env, "SELECT reauth_at FROM auth_sessions WHERE kind='passkey'"))[
        "reauth_at"
    ] is None


@pytest.mark.unit
async def test_reauth_evidence_is_bound_to_its_session(env):
    s1, auth = await owner_with_passkey(env)
    s2 = env["browser"]()
    assert (await sign_in(s2, auth, OWNER)).status_code == 200
    # Share the binding cookie so only the session binding can reject the ceremony.
    options = (await s1.post("/v1/auth/reauth/options")).json()
    s2.cookies[env["settings"].binding_cookie] = s1.cookies[env["settings"].binding_cookie]
    credential = auth.get(options["public_key"], HANDLE)
    response = await s2.post(
        "/v1/auth/reauth/verify",
        json={"ceremony_id": options["ceremony_id"], "credential": credential},
    )
    assert (response.status_code, response.json()["code"]) == (400, "ceremony_invalid")


@pytest.mark.integration
async def test_reauth_expires_between_add_options_and_add_verify(env, monkeypatch):
    browser, p = await owner_with_passkey(env)
    assert (await reauth(browser, p, OWNER)).status_code == 204
    real = clock.now
    monkeypatch.setattr(clock, "now", lambda: real() + timedelta(minutes=4))
    options = await browser.post("/v1/me/passkeys/options")
    assert options.status_code == 200
    data = options.json()
    new = FakeAuthenticator()
    monkeypatch.setattr(clock, "now", lambda: real() + timedelta(minutes=6))
    response = await browser.post(
        "/v1/me/passkeys/verify",
        json={"ceremony_id": data["ceremony_id"], "credential": new.create(data["public_key"])},
    )
    assert (response.status_code, response.json()["code"]) == (403, "reauth_required")
    assert len(await rows(env, "SELECT 1 FROM passkey_credentials")) == 1


@pytest.mark.integration
async def test_adding_a_passkey_without_reauth_is_refused_at_options(env):
    browser, _ = await owner_with_passkey(env)
    response = await browser.post("/v1/me/passkeys/options")
    assert (response.status_code, response.json()["code"]) == (403, "reauth_required")


# ---------- F06 ----------


@pytest.mark.integration
async def test_add_a_passkey_lists_exclude_credentials_and_keeps_the_user(env):
    browser, p = await owner_with_passkey(env)
    await reauth(browser, p, OWNER)
    options = await browser.post("/v1/me/passkeys/options")
    excluded = options.json()["public_key"]["excludeCredentials"]
    assert [e["id"] for e in excluded] == [b64(p.credential_id)]
    q = FakeAuthenticator()
    verify = await browser.post(
        "/v1/me/passkeys/verify",
        json={
            "ceremony_id": options.json()["ceremony_id"],
            "credential": q.create(options.json()["public_key"]),
            "label": "Laptop",
        },
    )
    assert verify.status_code == 201 and verify.json()["label"] == "Laptop"
    assert verify.json()["is_current"] is False
    users = {r["user_id"] for r in await rows(env, "SELECT user_id FROM passkey_credentials")}
    assert users == {OWNER}
    # The new passkey signs in as the same user.
    fresh = env["browser"]()
    assert (await sign_in(fresh, q, OWNER)).json()["user_id"] == str(OWNER)


@pytest.mark.integration
async def test_a_revoked_credential_id_can_never_be_registered_again(env):
    browser, p = await owner_with_passkey(env)
    await execute(
        env,
        "UPDATE passkey_credentials SET revoked_at=now()-interval '200 days', "
        "created_at=now()-interval '300 days'",
    )
    for _ in range(3):
        await cleanup.run(env["database"], clock.now() + timedelta(days=200))
    assert len(await rows(env, "SELECT 1 FROM passkey_credentials")) == 1
    token = await issue_add(env, OWNER)
    _, verify, _ = await register(env, env["browser"](), token, auth=p)
    assert (verify.status_code, verify.json()["code"]) == (409, "credential_exists")


@pytest.mark.integration
async def test_per_user_limits(env):
    browser, p = await owner_with_passkey(env)
    for _ in range(9):
        await seed_credential(env, OWNER, FakeAuthenticator())
    await reauth(browser, p, OWNER)
    refused = await browser.post("/v1/me/passkeys/options")
    assert (refused.status_code, refused.json()["code"]) == (409, "passkey_limit")
    # A ceremony created before the limit was reached is still refused at verify.
    await execute(
        env,
        "DELETE FROM passkey_credentials WHERE id IN (SELECT id FROM passkey_credentials WHERE credential_id <> %s LIMIT 1)",
        (p.credential_id,),
    )
    options = (await browser.post("/v1/me/passkeys/options")).json()
    await seed_credential(env, OWNER, FakeAuthenticator())
    response = await browser.post(
        "/v1/me/passkeys/verify",
        json={
            "ceremony_id": options["ceremony_id"],
            "credential": FakeAuthenticator().create(options["public_key"]),
        },
    )
    assert (response.status_code, response.json()["code"]) == (409, "passkey_limit")
    # 20 active sessions: the 21st sign-in revokes the oldest with reason "limit".
    await execute(env, "DELETE FROM auth_sessions")
    first = None
    for index in range(20):
        session = env["browser"]()
        assert (await sign_in(session, p, OWNER)).status_code == 200, index
        first = first or session
    assert len(await rows(env, "SELECT 1 FROM auth_sessions WHERE revoked_at IS NULL")) == 20
    newest = env["browser"]()
    assert (await sign_in(newest, p, OWNER)).status_code == 200
    assert len(await rows(env, "SELECT 1 FROM auth_sessions WHERE revoked_at IS NULL")) == 20
    limited = await rows(env, "SELECT 1 FROM auth_sessions WHERE revoke_reason='limit'")
    assert len(limited) == 1
    assert (await first.get("/v1/me", contract=False)).status_code == 401


@pytest.mark.integration
async def test_ceremonies_per_session_are_capped_at_five(env):
    browser, p = await owner_with_passkey(env)
    for _ in range(8):
        assert (await browser.post("/v1/auth/reauth/options")).status_code == 200
    open_ceremonies = await rows(
        env, "SELECT 1 FROM webauthn_ceremonies WHERE purpose='reauth' AND consumed_at IS NULL"
    )
    assert len(open_ceremonies) == 5


@pytest.mark.integration
async def test_the_last_passkey_cannot_be_deleted(env):
    browser, p = await owner_with_passkey(env)
    await reauth(browser, p, OWNER)
    only = (await browser.get("/v1/me/passkeys")).json()["passkeys"][0]["id"]
    response = await browser.delete(f"/v1/me/passkeys/{only}")
    assert (response.status_code, response.json()["code"]) == (409, "last_passkey")


@pytest.mark.integration
async def test_concurrent_deletions_cannot_remove_every_passkey(env):
    s1, p, q = await two_passkeys(env)
    s2 = env["browser"]()
    assert (await sign_in(s2, p, OWNER)).status_code == 200
    assert (await reauth(s2, p, OWNER)).status_code == 204
    ids = {
        r["credential_id"]: r["id"]
        for r in await rows(env, "SELECT id, credential_id FROM passkey_credentials")
    }
    pid, qid = ids[p.credential_id], ids[q.credential_id]
    results = await asyncio.gather(
        s1.delete(f"/v1/me/passkeys/{qid}", contract=False),
        s2.delete(f"/v1/me/passkeys/{pid}", contract=False),
    )
    assert sorted(r.status_code for r in results) == [204, 409]
    assert next(r for r in results if r.status_code == 409).json()["code"] == "last_passkey"
    assert len(await rows(env, "SELECT 1 FROM passkey_credentials WHERE revoked_at IS NULL")) == 1


@pytest.mark.integration
async def test_deleting_the_passkey_behind_the_current_session(env):
    s1, p, q = await two_passkeys(env)
    other = env["browser"]()
    assert (await sign_in(other, p, OWNER)).status_code == 200
    via_q = env["browser"]()
    assert (await sign_in(via_q, q, OWNER)).status_code == 200
    pid = next(
        r["id"]
        for r in await rows(env, "SELECT id, credential_id FROM passkey_credentials")
        if r["credential_id"] == p.credential_id
    )
    response = await s1.delete(f"/v1/me/passkeys/{pid}")
    assert response.status_code == 204
    assert env["settings"].session_cookie not in s1.cookies
    revoked = await rows(
        env, "SELECT revoke_reason FROM auth_sessions WHERE credential_id=%s", (pid,)
    )
    assert revoked and all(r["revoke_reason"] == "passkey_revoked" for r in revoked)
    assert (await other.get("/v1/me", contract=False)).status_code == 401
    assert (await via_q.get("/v1/me")).status_code == 200


@pytest.mark.integration
async def test_a_deleted_passkey_no_longer_signs_in_and_is_not_listed(env):
    s1, p, q = await two_passkeys(env)
    qid = next(
        r["id"]
        for r in await rows(env, "SELECT id, credential_id FROM passkey_credentials")
        if r["credential_id"] == q.credential_id
    )
    assert (await s1.delete(f"/v1/me/passkeys/{qid}")).status_code == 204
    assert [x["id"] for x in (await s1.get("/v1/me/passkeys")).json()["passkeys"]] != []
    assert (await sign_in(env["browser"](), q, OWNER)).status_code == 401
    again = await s1.delete(f"/v1/me/passkeys/{qid}")
    assert (again.status_code, again.json()["code"]) == (404, "not_found")


# ---------- F07 ----------


@pytest.mark.integration
async def test_recovery_replaces_every_old_credential_and_session(env):
    s1, p, q = await two_passkeys(env)
    second_session = env["browser"]()
    await sign_in(second_session, q, OWNER)
    token = await issue_recovery(env, OWNER)
    fresh = env["browser"]()
    new_auth, verify, _ = await register(env, fresh, token)
    assert verify.status_code == 200, verify.text
    active = await rows(
        env, "SELECT credential_id FROM passkey_credentials WHERE revoked_at IS NULL"
    )
    assert [bytes(a["credential_id"]) for a in active] == [new_auth.credential_id]
    old = await rows(
        env,
        "SELECT revoked_at FROM passkey_credentials WHERE credential_id <> %s",
        (new_auth.credential_id,),
    )
    assert len(old) == 2 and all(r["revoked_at"] for r in old)
    reasons = await rows(
        env, "SELECT revoke_reason FROM auth_sessions WHERE revoked_at IS NOT NULL"
    )
    # Both passkey sessions and the local dev session are revoked, all with reason "recovery".
    assert len(reasons) == 3 and {r["revoke_reason"] for r in reasons} == {"recovery"}
    assert len(await rows(env, "SELECT 1 FROM auth_sessions WHERE revoked_at IS NULL")) == 1
    assert (await s1.get("/v1/me", contract=False)).status_code == 401
    assert (await sign_in(env["browser"](), p, OWNER)).status_code == 401
    assert await one(env, "SELECT 1 AS n FROM auth_events WHERE event='recovery_completed'")


@pytest.mark.integration
async def test_a_newer_recovery_link_supersedes_an_older_one(env):
    await owner_with_passkey(env)
    first = await issue_recovery(env, OWNER)
    second = await issue_recovery(env, OWNER)
    response = await env["browser"]().post(
        "/v1/auth/registration/options", json=options_body(first)
    )
    assert (response.status_code, response.json()["code"]) == (400, "enrollment_token_invalid")
    assert (
        await env["browser"]().post("/v1/auth/registration/options", json=options_body(second))
    ).status_code == 200


@pytest.mark.integration
async def test_completed_recovery_invalidates_other_grants_and_ceremonies(env):
    browser, p = await owner_with_passkey(env)
    add_token = await issue_add(env, OWNER)
    holder = env["browser"]()
    pending = (
        await holder.post("/v1/auth/registration/options", json=options_body(add_token))
    ).json()
    recovery = await issue_recovery(env, OWNER)
    _, verify, _ = await register(env, env["browser"](), recovery)
    assert verify.status_code == 200
    response = await holder.post(
        "/v1/auth/registration/verify",
        json={
            "ceremony_id": pending["ceremony_id"],
            "credential": FakeAuthenticator().create(pending["public_key"]),
        },
    )
    assert (response.status_code, response.json()["code"]) == (400, "ceremony_invalid")
    again = await env["browser"]().post(
        "/v1/auth/registration/options", json=options_body(add_token)
    )
    assert (again.status_code, again.json()["code"]) == (400, "enrollment_token_invalid")
    assert len(await rows(env, "SELECT 1 FROM passkey_credentials WHERE revoked_at IS NULL")) == 1


@pytest.mark.integration
async def test_two_recoveries_for_the_same_user_at_the_same_time(env):
    await owner_with_passkey(env)
    token = await issue_recovery(env, OWNER)
    a, b = env["browser"](), env["browser"]()
    bodies = []
    for browser in (a, b):
        options = (
            await browser.post("/v1/auth/registration/options", json=options_body(token))
        ).json()
        bodies.append(
            {
                "ceremony_id": options["ceremony_id"],
                "credential": FakeAuthenticator().create(options["public_key"]),
            }
        )
    results = await asyncio.gather(
        a.post("/v1/auth/registration/verify", json=bodies[0], contract=False),
        b.post("/v1/auth/registration/verify", json=bodies[1], contract=False),
    )
    assert sorted(r.status_code for r in results)[0] == 200
    loser = next(r for r in results if r.status_code != 200)
    assert loser.json()["code"] in {"ceremony_invalid", "enrollment_token_used"}
    assert len(await rows(env, "SELECT 1 FROM passkey_credentials WHERE revoked_at IS NULL")) == 1
    assert len(await rows(env, "SELECT 1 FROM auth_sessions WHERE revoked_at IS NULL")) == 1


@pytest.mark.integration
async def test_add_credential_keeps_existing_passkeys(env):
    browser, p = await owner_with_passkey(env)
    token = await issue_add(env, OWNER)
    fresh = env["browser"]()
    new_auth, verify, options = await register(env, fresh, token)
    assert verify.status_code == 200
    assert [e["id"] for e in options.json()["public_key"]["excludeCredentials"]] == [
        b64(p.credential_id)
    ]
    assert len(await rows(env, "SELECT 1 FROM passkey_credentials WHERE revoked_at IS NULL")) == 2
    assert (await browser.get("/v1/me")).status_code == 200


@pytest.mark.integration
async def test_recovery_never_creates_a_profile_or_enables_an_account(env):
    browser, auth, user = await invited_user(env)
    token = await Cli(env).token(
        "issue-recovery", "--user", str(user), settings=cli_settings(env, True)
    )
    await Cli(env).run("disable-user", str(user))
    response = await env["browser"]().post(
        "/v1/auth/registration/options", json=options_body(token)
    )
    assert (response.status_code, response.json()["code"]) == (400, "enrollment_token_invalid")
    assert (await one(env, "SELECT status FROM profiles WHERE user_id=%s", (user,)))[
        "status"
    ] == "disabled"
    assert len(await rows(env, "SELECT 1 FROM profiles")) == 2


@pytest.mark.integration
async def test_recovery_for_a_disabled_user_whose_token_survived_still_cannot_enable(env):
    _, _, user = await invited_user(env)
    token = await Cli(env).token(
        "issue-recovery", "--user", str(user), settings=cli_settings(env, True)
    )
    await execute(env, "UPDATE profiles SET status='disabled' WHERE user_id=%s", (user,))
    response = await env["browser"]().post(
        "/v1/auth/registration/options", json=options_body(token)
    )
    assert (response.status_code, response.json()["code"]) == (403, "account_disabled")
    assert (await one(env, "SELECT status FROM profiles WHERE user_id=%s", (user,)))[
        "status"
    ] == "disabled"


@pytest.mark.integration
async def test_non_owner_recovery_follows_the_gate(env):
    _, _, user = await invited_user(env)
    token = await Cli(env).token(
        "issue-recovery", "--user", str(user), settings=cli_settings(env, True)
    )
    set_gate(env, False)
    from app import auth_admin

    with pytest.raises(auth_admin.CliError, match="DESIGN 3.1"):
        await Cli(env).run("issue-recovery", "--user", str(user))
    response = await env["browser"]().post(
        "/v1/auth/registration/options", json=options_body(token)
    )
    assert (response.status_code, response.json()["code"]) == (403, "admission_disabled")


@pytest.mark.integration
async def test_non_owner_recovery_token_is_rechecked_at_verify(env):
    _, _, user = await invited_user(env)
    token = await Cli(env).token(
        "issue-recovery", "--user", str(user), settings=cli_settings(env, True)
    )
    browser = env["browser"]()
    options = (await browser.post("/v1/auth/registration/options", json=options_body(token))).json()
    set_gate(env, False)
    response = await browser.post(
        "/v1/auth/registration/verify",
        json={
            "ceremony_id": options["ceremony_id"],
            "credential": FakeAuthenticator().create(options["public_key"]),
        },
    )
    assert (response.status_code, response.json()["code"]) == (403, "admission_disabled")
    assert len(await rows(env, "SELECT 1 FROM passkey_credentials WHERE revoked_at IS NULL")) == 1


@pytest.mark.integration
async def test_disable_user_revokes_sessions_and_unused_tokens(env):
    browser, auth, user = await invited_user(env)
    token = await Cli(env).token(
        "issue-add-credential", "--user", str(user), settings=cli_settings(env, True)
    )
    await Cli(env).run("disable-user", str(user))
    assert (await browser.get("/v1/me", contract=False)).status_code == 401
    state = await one(
        env, "SELECT revoked_at FROM enrollment_tokens WHERE token_hash=%s", (token_hash(token),)
    )
    assert state["revoked_at"] is not None
    response = await sign_in(env["browser"](), auth, user)
    assert response.status_code == 403 and response.json()["code"] == "account_disabled"


@pytest.mark.integration
async def test_revoke_sessions_command(env):
    browser, auth = await owner_with_passkey(env)
    other = env["browser"]()
    await sign_in(other, auth, OWNER)
    await Cli(env).run("revoke-sessions", "--user", str(OWNER))
    assert (await browser.get("/v1/me", contract=False)).status_code == 401
    assert (await other.get("/v1/me", contract=False)).status_code == 401
    reasons = {
        r["revoke_reason"]
        for r in await rows(env, "SELECT revoke_reason FROM auth_sessions WHERE kind='passkey'")
    }
    assert reasons == {"admin"}


@pytest.mark.integration
async def test_issue_commands_validate_and_list_tokens_hides_secrets(env, capsys):
    from app import auth_admin

    with pytest.raises(auth_admin.CliError):
        await Cli(env).run("issue-recovery", "--user", str(OWNER), "--hours", "25")
    with pytest.raises(auth_admin.CliError):
        await Cli(env).run("issue-recovery", "--user", str(uuid4()))
    token = await issue_recovery(env, OWNER)
    listing = await Cli(env).run("list-tokens")
    assert token not in listing and "recovery" in listing
    token_id = (await one(env, "SELECT id FROM enrollment_tokens WHERE used_at IS NULL"))["id"]
    await Cli(env).run("revoke-token", str(token_id))
    response = await env["browser"]().post(
        "/v1/auth/registration/options", json=options_body(token)
    )
    assert response.json()["code"] == "enrollment_token_invalid"
    del capsys


__all__ = ["login_options"]
