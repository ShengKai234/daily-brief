"""Helpers that drive the real app and the real admin CLI with the fake authenticator."""

import contextlib
import io
from dataclasses import replace
from uuid import UUID

from fakeauth import FakeAuthenticator

from app import auth_admin


class Cli:
    def __init__(self, env):
        self.env = env

    async def run(self, *argv, settings=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            await auth_admin.run(["--yes", *argv], env_settings=settings or self.env["settings"])
        return out.getvalue()

    async def token(self, *argv, settings=None):
        text = await self.run(*argv, settings=settings)
        return text.split("#t=")[1].split()[0]


async def bootstrap(env):
    return await Cli(env).token("bootstrap-owner")


async def issue_recovery(env, user_id, hours=1):
    return await Cli(env).token("issue-recovery", "--user", str(user_id), "--hours", str(hours))


async def issue_add(env, user_id):
    return await Cli(env).token("issue-add-credential", "--user", str(user_id))


def options_body(token, display_name=None):
    body = {"enrollment_token": token}
    if display_name is not None:
        body["display_name"] = display_name
    return body


async def register(env, browser, token, *, auth=None, display_name=None, label=None, **create_kw):
    """Full registration; returns (authenticator, verify response, options response)."""
    auth = auth or FakeAuthenticator()
    options = await browser.post(
        "/v1/auth/registration/options", json=options_body(token, display_name)
    )
    assert options.status_code == 200, options.text
    data = options.json()
    credential = auth.create(data["public_key"], **create_kw)
    body = {"ceremony_id": data["ceremony_id"], "credential": credential}
    if label is not None:
        body["label"] = label
    verify = await browser.post("/v1/auth/registration/verify", json=body)
    return auth, verify, options


async def login_options(browser):
    response = await browser.post("/v1/auth/login/options", json={})
    assert response.status_code == 200, response.text
    return response.json()


async def sign_in(browser, auth, user_id, **get_kw):
    data = await login_options(browser)
    credential = auth.get(data["public_key"], UUID(str(user_id)).bytes, **get_kw)
    return await browser.post(
        "/v1/auth/login/verify", json={"ceremony_id": data["ceremony_id"], "credential": credential}
    )


async def reauth(browser, auth, user_id, **get_kw):
    options = await browser.post("/v1/auth/reauth/options")
    assert options.status_code == 200, options.text
    data = options.json()
    credential = auth.get(data["public_key"], UUID(str(user_id)).bytes, **get_kw)
    return await browser.post(
        "/v1/auth/reauth/verify",
        json={"ceremony_id": data["ceremony_id"], "credential": credential},
    )


async def add_passkey(browser, auth):
    options = await browser.post("/v1/me/passkeys/options")
    if options.status_code != 200:
        return options, None
    data = options.json()
    credential = auth.create(data["public_key"])
    verify = await browser.post(
        "/v1/me/passkeys/verify",
        json={"ceremony_id": data["ceremony_id"], "credential": credential},
    )
    return options, verify


async def owner_with_passkey(env, **auth_kw):
    """Bootstrap the owner and register a passkey in a fresh browser; returns (browser, auth)."""
    browser = env["browser"]()
    token = await bootstrap(env)
    auth, verify, _ = await register(env, browser, token, auth=FakeAuthenticator(**auth_kw))
    assert verify.status_code == 200, verify.text
    return browser, auth


async def one(env, query, params=()):
    async with env["super"].transaction() as db:
        return await db.one(query, params)


async def rows(env, query, params=()):
    async with env["super"].transaction() as db:
        return await db.all(query, params)


async def execute(env, query, params=()):
    async with env["super"].transaction() as db:
        await db.execute(query, params)


def set_gate(env, value):
    """Open or close non-owner admission for the running app (the API reads it per request)."""
    env["app"].state.settings = replace(env["settings"], non_owner_admission=value)


def cli_settings(env, gate=None):
    if gate is None:
        return env["settings"]
    return replace(env["settings"], non_owner_admission=gate)


async def invite(env):
    return await Cli(env).token("issue-invite", settings=cli_settings(env, True))


async def invited_user(env, name="Mei"):
    """Register a non-owner through a real invite; returns (browser, authenticator, user_id)."""
    set_gate(env, True)
    browser = env["browser"]()
    token = await invite(env)
    auth, verify, _ = await register(env, browser, token, display_name=name)
    assert verify.status_code == 200, verify.text
    return browser, auth, UUID(verify.json()["user_id"])


async def seed_credential(env, user_id, auth, *, label=""):
    """Insert a passkey straight into the database (a second user's device, for example)."""
    from app.authdb import insert_credential
    from app.webauthn_wrapper import RegisteredCredential

    credential = RegisteredCredential(
        credential_id=auth.credential_id,
        public_key=auth.cose_key(),
        sign_count=0,
        aaguid=None,
        backup_eligible=auth.backup_eligible,
        backup_state=auth.backup_state,
        transports=[],
    )
    from app import clock

    async with env["super"].transaction() as db:
        return await insert_credential(db, user_id, credential, label, clock.now())
