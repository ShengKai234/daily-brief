"""F08 request protection and F09 environment, plus config and edge hardening."""

import logging
from uuid import uuid4

import pytest
from authkit import (
    Cli,
    login_options,
    one,
    options_body,
    owner_with_passkey,
    register,
    rows,
    sign_in,
)
from conftest import OWNER, production_settings
from fakeauth import FakeAuthenticator

from app import auth_admin
from app.config import Settings
from app.main import create_app

ORIGIN = "http://localhost:5173"
PROD_ORIGIN = "https://brief.example.com"


# ---------- F08 ----------


@pytest.mark.integration
@pytest.mark.parametrize(
    "method,path,headers",
    [
        ("POST", "/v1/auth/login/verify", [("Origin", "https://evil.example")]),
        ("POST", "/v1/auth/logout", "missing"),
        ("PATCH", "/v1/me", [("Origin", "http://brief.example.com")]),
        ("DELETE", "/v1/me/sessions/{id}", [("Origin", "null")]),
        ("POST", "/v1/jobs", [("Origin", ORIGIN), ("Origin", ORIGIN)]),
        ("PUT", "/v1/delivery-slots", [("Origin", ORIGIN + "/")]),
        ("POST", "/v1/auth/registration/options", [("Origin", "https://evil.example")]),
    ],
)
async def test_unsafe_browser_requests_need_exactly_one_trusted_origin(env, method, path, headers):
    browser, _ = await owner_with_passkey(env)
    path = path.replace("{id}", str(uuid4()))
    before = (
        await rows(env, "SELECT id, revoked_at FROM auth_sessions ORDER BY id"),
        await rows(env, "SELECT id FROM webauthn_ceremonies"),
    )
    kwargs = (
        {"json": {}}
        if method in {"PUT", "PATCH"} or "login" in path or "registration" in path
        else {}
    )
    if headers == "missing":
        response = await browser.request(method, path, origin=None, contract=False, **kwargs)
    else:
        response = await browser.request(method, path, headers=headers, contract=False, **kwargs)
    assert (response.status_code, response.json()["code"]) == (403, "origin_mismatch")
    after = (
        await rows(env, "SELECT id, revoked_at FROM auth_sessions ORDER BY id"),
        await rows(env, "SELECT id FROM webauthn_ceremonies"),
    )
    assert before == after
    assert "set-cookie" not in response.headers


@pytest.mark.integration
async def test_get_requests_do_not_need_an_origin(env):
    browser, _ = await owner_with_passkey(env)
    assert (await browser.get("/v1/me")).status_code == 200


@pytest.mark.integration
async def test_the_worker_is_independent_of_browser_origin(env):
    response = await env["browser"]().post(
        "/internal/jobs/claim", headers=env["internal"], json={"worker_id": "w"}, origin=None
    )
    assert response.status_code == 204
    with_cookie = await env["browser"]().post(
        "/internal/jobs/claim",
        headers=[*env["internal"].items(), ("Cookie", f"{env['settings'].session_cookie}=abc")],
        json={"worker_id": "w"},
        origin=None,
    )
    assert (with_cookie.status_code, with_cookie.json()["code"]) == (401, "unauthorized")


@pytest.mark.integration
@pytest.mark.parametrize(
    "headers",
    [{"X-Internal-Token": "x"}, {"Authorization": "Bearer x"}, {"Authorization": "Basic eDp5"}],
)
@pytest.mark.parametrize(
    "path,method",
    [("/v1/me", "GET"), ("/v1/auth/login/options", "POST"), ("/v1/auth/logout", "POST")],
)
async def test_credential_families_stay_separate(env, headers, path, method):
    browser, _ = await owner_with_passkey(env)
    kwargs = {"json": {}} if "options" in path else {}
    response = await browser.request(method, path, headers=headers, contract=False, **kwargs)
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")


async def chunks():
    for _ in range(70):
        yield b"x" * 1024


@pytest.mark.unit
async def test_media_types_and_sizes(env):
    browser = env["browser"]()
    form = await browser.post(
        "/v1/auth/login/options",
        content=b"a=b",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        contract=False,
    )
    assert (form.status_code, form.json()["code"]) == (415, "unsupported_media_type")
    assert await rows(env, "SELECT 1 FROM webauthn_ceremonies") == []
    no_type = await browser.post("/v1/auth/login/options", content=b"{}", contract=False)
    assert no_type.status_code == 415
    json_ok = await browser.post(
        "/v1/auth/login/options",
        content=b"{}",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert json_ok.status_code == 200
    big = await browser.post(
        "/v1/auth/registration/verify",
        content=b"{" + b"x" * (65 * 1024),
        headers={"Content-Type": "application/json"},
        contract=False,
    )
    assert (big.status_code, big.json()["code"]) == (413, "payload_too_large")
    chunked = await browser.post(
        "/v1/auth/login/verify",
        content=chunks(),
        headers={"Content-Type": "application/json"},
        contract=False,
    )
    assert chunked.status_code == 413
    owner, _ = await owner_with_passkey(env)
    empty = await owner.post("/v1/jobs", contract=False)
    assert empty.status_code not in {403, 413, 415}


@pytest.mark.integration
async def test_login_csrf_is_blocked(env):
    browser, auth = await owner_with_passkey(env)
    attacker = env["browser"]()
    data = await login_options(attacker)
    credential = auth.get(data["public_key"], OWNER.bytes)
    victim = env["browser"]()
    response = await victim.post(
        "/v1/auth/login/verify",
        json={"ceremony_id": data["ceremony_id"], "credential": credential},
        origin="https://evil.example",
        contract=False,
    )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers and victim.cookies == {}


@pytest.mark.integration
async def test_per_ip_limit_on_public_auth_endpoints(env, monkeypatch):
    monkeypatch.setattr("app.security.RATE_LIMIT", 20)
    browser = env["browser"]()
    for _ in range(20):
        assert (await browser.post("/v1/auth/login/options", json={})).status_code == 200
    limited = await browser.post("/v1/auth/login/options", json={})
    assert (limited.status_code, limited.json()["code"]) == (429, "rate_limited")
    assert int(limited.headers["retry-after"]) > 0
    # The business routes are not rate limited by this control.
    owner = await env["as_user"](OWNER)
    assert (await owner.get("/v1/me")).status_code == 200


@pytest.mark.integration
async def test_rate_limit_counts_by_address_not_source_port_in_production(prod, monkeypatch):
    monkeypatch.setattr("app.security.RATE_LIMIT", 20)
    for port in range(20):
        client = prod["browser"](f"198.51.100.7:{40000 + port}")
        assert (await client.post("/v1/auth/login/options", json={})).status_code == 200
    same_ip = prod["browser"]("198.51.100.7:55555")
    assert (await same_ip.post("/v1/auth/login/options", json={})).status_code == 429
    other_ip = prod["browser"]("198.51.100.8:40000")
    assert (await other_ip.post("/v1/auth/login/options", json={})).status_code == 200


@pytest.mark.integration
async def test_ipv6_addresses_share_a_64_bucket(prod, monkeypatch):
    monkeypatch.setattr("app.security.RATE_LIMIT", 3)
    for suffix in range(3):
        client = prod["browser"](f"[2001:db8:1:2::{suffix + 1}]:40000")
        assert (await client.post("/v1/auth/login/options", json={})).status_code == 200
    same_net = prod["browser"]("[2001:db8:1:2:ffff::9]:1")
    assert (await same_net.post("/v1/auth/login/options", json={})).status_code == 429
    elsewhere = prod["browser"]("[2001:db8:1:3::1]:1")
    assert (await elsewhere.post("/v1/auth/login/options", json={})).status_code == 200


@pytest.mark.integration
@pytest.mark.parametrize(
    "viewer", [None, "", "not-an-address", "198.51.100.7", "198.51.100.7:99999"]
)
async def test_untrusted_ingress_metadata_is_refused_in_production(prod, viewer):
    client = prod["browser"](None)
    headers = [("X-Forwarded-For", "203.0.113.9")]
    if viewer is not None:
        headers.append(("CloudFront-Viewer-Address", viewer))
    response = await client.post("/v1/auth/login/options", json={}, headers=headers, contract=False)
    assert response.status_code == 400
    assert response.json()["code"] == "bad_request"


@pytest.mark.integration
async def test_x_forwarded_for_never_selects_the_rate_limit_bucket(prod, monkeypatch):
    monkeypatch.setattr("app.security.RATE_LIMIT", 2)
    for index in range(2):
        client = prod["browser"]("198.51.100.7:1")
        r = await client.post(
            "/v1/auth/login/options", json={}, headers=[("X-Forwarded-For", f"10.0.0.{index}")]
        )
        assert r.status_code == 200
    client = prod["browser"]("198.51.100.7:1")
    r = await client.post(
        "/v1/auth/login/options", json={}, headers=[("X-Forwarded-For", "10.9.9.9")]
    )
    assert r.status_code == 429


@pytest.mark.integration
async def test_a_known_credential_id_cannot_lock_the_owner_out(env, prod, monkeypatch):
    settings = prod["settings"]
    auth = FakeAuthenticator(rp_id="brief.example.com", origin=PROD_ORIGIN)
    token = await Cli(env).token("bootstrap-owner")
    owner = prod["browser"]("203.0.113.1:1")
    _, verify, _ = await register(prod, owner, token, auth=auth)
    assert verify.status_code == 200, verify.text
    bad = FakeAuthenticator(
        rp_id="brief.example.com", origin=PROD_ORIGIN, credential_id=auth.credential_id
    )
    for index in range(100):
        attacker = prod["browser"](f"192.0.2.{index % 250 + 1}:{1000 + index}")
        data = await login_options(attacker)
        credential = bad.get(data["public_key"], OWNER.bytes)
        response = await attacker.post(
            "/v1/auth/login/verify",
            json={"ceremony_id": data["ceremony_id"], "credential": credential},
            contract=False,
        )
        assert response.status_code == 401
    victim = prod["browser"]("203.0.113.1:2")
    assert (await sign_in(victim, auth, OWNER)).status_code == 200
    assert settings.session_cookie in victim.cookies


@pytest.mark.integration
async def test_login_failed_events_are_limited_to_one_per_ip_window(env, prod):
    token = await Cli(env).token("bootstrap-owner")
    auth = FakeAuthenticator(rp_id="brief.example.com", origin=PROD_ORIGIN)
    _, verify, _ = await register(prod, prod["browser"]("203.0.113.1:1"), token, auth=auth)
    assert verify.status_code == 200
    stranger = FakeAuthenticator(rp_id="brief.example.com", origin=PROD_ORIGIN)
    for _ in range(5):
        client = prod["browser"]("192.0.2.5:1")
        await sign_in(client, stranger, OWNER)
    from conftest import Client  # noqa: F401

    async with prod["app"].state.database.transaction() as db:
        count = (await db.one("SELECT count(*) AS n FROM auth_events WHERE event='login_failed'"))[
            "n"
        ]
    assert count == 1


@pytest.mark.integration
async def test_api_responses_are_never_cached(env):
    anonymous = env["browser"]()
    for method, path in [
        ("GET", "/v1/me"),
        ("GET", "/v1/does-not-exist"),
        ("POST", "/v1/auth/login/options"),
        ("GET", "/health"),
        ("GET", "/internal/jobs/nope"),
    ]:
        kwargs = {"json": {}} if method == "POST" else {}
        response = await anonymous.request(method, path, contract=False, **kwargs)
        assert response.headers["cache-control"] == "no-store", path
    error = await anonymous.get("/v1/me", contract=False)
    assert error.headers["content-type"].startswith("application/problem+json")


# ---------- F09 ----------


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, "", "prod", "staging", "LOCAL"])
def test_app_env_is_required(monkeypatch, value):
    monkeypatch.delenv("APP_ENV", raising=False)
    if value is not None:
        monkeypatch.setenv("APP_ENV", value)
    with pytest.raises(ValueError, match="APP_ENV"):
        Settings.from_env()
    assert auth_admin.main(["list-tokens"]) == 1


@pytest.mark.unit
def test_production_configuration_is_strict(monkeypatch):
    base = {
        "app_env": "production",
        "database_url": "postgresql://x@h/db",
        "internal_service_token": "t" * 32,
        "rate_limit_key": "k" * 32,
        "webauthn_rp_id": "brief.example.com",
        "webauthn_origins": (PROD_ORIGIN,),
        "worker_dispatcher": "github",
        "agent_runner": "claude_subscription",
    }
    Settings(**base)
    for change in [
        {"webauthn_origins": ("http://brief.example.com",)},
        {"webauthn_origins": ("https://evil.example",)},
        {"webauthn_origins": ("https://brief.example.com/path",)},
        {"webauthn_rp_id": "localhost", "webauthn_origins": ("https://localhost",)},
        {"rate_limit_key": ""},
        {"worker_dispatcher": "local"},
        {"agent_runner": "stub"},
        {"internal_service_token": ""},
    ]:
        with pytest.raises(ValueError):
            Settings(**{**base, **change})


@pytest.mark.unit
def test_local_cookie_names_carry_the_compose_project():
    main = Settings(app_env="local", compose_project_name="brief-main")
    api = Settings(app_env="local", compose_project_name="brief-api")
    assert main.session_cookie == "brief_session_brief-main"
    assert api.session_cookie == "brief_session_brief-api"
    assert main.binding_cookie == "brief_ceremony_brief-main"
    production = Settings(**production_args())
    assert production.session_cookie == "__Host-brief_session"
    assert production.binding_cookie == "__Host-brief_ceremony"


def production_args():
    return {
        "app_env": "production",
        "database_url": "postgresql://x@h/db",
        "internal_service_token": "t" * 32,
        "rate_limit_key": "k" * 32,
        "webauthn_rp_id": "brief.example.com",
        "webauthn_origins": (PROD_ORIGIN,),
        "worker_dispatcher": "github",
        "agent_runner": "claude_subscription",
    }


@pytest.mark.integration
async def test_worktrees_do_not_share_local_cookies(env, database_url):
    from dataclasses import replace

    import httpx
    from conftest import Client

    other_settings = replace(env["settings"], compose_project_name="brief-api")
    other_app = create_app(other_settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=other_app), base_url="http://test"
    ) as raw:
        auth_a = FakeAuthenticator()
        token_a = await Cli(env).token("bootstrap-owner")
        a = env["browser"]()
        await register(env, a, token_a, auth=auth_a)
        b = Client(raw, other_settings)
        assert (await sign_in(b, auth_a, OWNER)).status_code == 200
    assert "brief_session_test" in a.cookies and "brief_session_brief-api" in b.cookies
    both = env["browser"]()
    both.cookies = {**a.cookies, **b.cookies}
    assert (await both.get("/v1/me")).status_code == 200
    await both.post("/v1/auth/logout")
    assert "brief_session_brief-api" in both.cookies and "brief_session_test" not in both.cookies


@pytest.mark.integration
async def test_dev_sessions_are_rejected_outside_local(env, prod):
    cookie = env["client"].cookies[env["settings"].session_cookie]
    kind = await one(env, "SELECT kind FROM auth_sessions")
    assert kind["kind"] == "dev"
    client = prod["browser"]()
    client.cookies[prod["settings"].session_cookie] = cookie
    response = await client.get("/v1/me", contract=False)
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")


@pytest.mark.unit
async def test_dev_session_only_runs_locally(env):
    settings = production_settings("postgresql://postgres@127.0.0.1:1/x", admin_cli=True)
    with pytest.raises(auth_admin.CliError, match="APP_ENV=local"):
        await auth_admin.run(["dev-session"], env_settings=settings)
    remote = Settings(
        app_env="local", database_url="postgresql://u@db.example.com/x", owner_user_id=OWNER
    )
    with pytest.raises(auth_admin.CliError, match="local database"):
        await auth_admin.run(["dev-session"], env_settings=remote)


@pytest.mark.integration
async def test_dev_session_command_gives_a_working_cookie(env):
    out = await Cli(env).run("dev-session")
    name, _, value = out.strip().partition("=")
    assert name == env["settings"].session_cookie
    client = env["browser"]()
    client.cookies[name] = value
    assert (await client.get("/v1/me")).json()["user_id"] == str(OWNER)


@pytest.mark.integration
async def test_production_cookie_attributes(env, prod):
    token = await Cli(env).token("bootstrap-owner")
    auth = FakeAuthenticator(rp_id="brief.example.com", origin=PROD_ORIGIN)
    browser = prod["browser"]()
    options = await browser.post("/v1/auth/registration/options", json=options_body(token))
    binding = next(
        h for h in options.headers.get_list("set-cookie") if h.startswith("__Host-brief_ceremony=")
    )
    for attribute in ("Secure", "HttpOnly", "SameSite=strict", "Path=/", "Max-Age=600"):
        assert attribute.lower() in binding.lower()
    assert "domain" not in binding.lower()
    data = options.json()
    verify = await browser.post(
        "/v1/auth/registration/verify",
        json={"ceremony_id": data["ceremony_id"], "credential": auth.create(data["public_key"])},
    )
    session = next(
        h for h in verify.headers.get_list("set-cookie") if h.startswith("__Host-brief_session=")
    )
    for attribute in ("Secure", "HttpOnly", "SameSite=strict", "Path=/", "Max-Age="):
        assert attribute.lower() in session.lower()
    assert "domain" not in session.lower()
    assert data["public_key"]["rp"]["id"] == "brief.example.com"
    assert data["public_key"]["user"]["name"].endswith("· brief.example.com")


@pytest.mark.integration
async def test_secrets_never_reach_logs_or_audit(env, caplog):
    caplog.set_level(logging.DEBUG)
    secrets_seen = []
    token = await Cli(env).token("bootstrap-owner")
    secrets_seen.append(token)
    browser = env["browser"]()
    options = await browser.post("/v1/auth/registration/options", json=options_body(token))
    secrets_seen += [
        options.json()["public_key"]["challenge"],
        browser.cookies[env["settings"].binding_cookie],
    ]
    auth = FakeAuthenticator()
    credential = auth.create(options.json()["public_key"])
    verify = await browser.post(
        "/v1/auth/registration/verify",
        json={"ceremony_id": options.json()["ceremony_id"], "credential": credential},
    )
    assert verify.status_code == 200
    secrets_seen.append(browser.cookies[env["settings"].session_cookie])
    secrets_seen.append(credential["response"]["attestationObject"])
    other = env["browser"]()
    await sign_in(other, auth, OWNER)
    await sign_in(other, FakeAuthenticator(), OWNER)  # a failing proof as well
    await browser.post(
        "/v1/auth/login/verify",
        content=b"{broken",
        headers={"Content-Type": "application/json"},
        contract=False,
    )
    logs = caplog.text
    for secret in secrets_seen:
        assert secret not in logs
    events = repr(await rows(env, "SELECT * FROM auth_events"))
    ceremonies = repr(await rows(env, "SELECT id FROM auth_events"))
    for secret in secrets_seen:
        assert secret not in events and secret not in ceremonies
    stored = repr(await rows(env, "SELECT * FROM enrollment_tokens"))
    assert token not in stored


@pytest.mark.integration
async def test_roles_enforce_who_may_create_tokens_and_sessions(env):
    from app.database import Database

    runtime = Database(env["settings"])
    admin = Database(env["settings"], env["settings"].admin_database_url)
    import psycopg

    async with runtime.transaction() as db:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await db.execute(
                "INSERT INTO enrollment_tokens (token_hash, purpose, created_by_actor, expires_at) "
                "VALUES (%s, 'invite', 'cli', now() + interval '1 day')",
                (b"x" * 32,),
            )
    async with admin.transaction() as db:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await db.execute(
                "INSERT INTO auth_sessions (user_id, token_hash, kind, idle_expires_at, "
                "absolute_expires_at) VALUES (%s, %s, 'dev', now() + interval '1 day', now() + interval '2 days')",
                (OWNER, b"y" * 32),
            )
