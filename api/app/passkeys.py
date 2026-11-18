"""Passkey sign-in, registration, reauthentication and passkey/session management.

Implements docs/auth-passkey-design.md sections 3 to 5. All locking follows authdb's order.
Nothing in this module logs request bodies, cookies, tokens, challenges or binding values.
"""

import secrets
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response

from app import clock
from app.auth import REAUTH_WINDOW, Authenticated, raw_cookie_headers
from app.authdb import (
    MAX_PASSKEYS,
    OPTIONS_BUDGET,
    Fail,
    active_credentials,
    ceremony_usable,
    consume_ceremony,
    create_ceremony,
    create_session,
    credential_exists,
    insert_credential,
    lock_ceremony,
    lock_credentials,
    lock_profile,
    lock_sessions,
    lock_token,
    record_event,
    revoke_sessions,
)
from app.contract import body, fields
from app.problems import Problem
from app.security import (
    BINDING_RE,
    b64url_decode,
    clear_cookie,
    cookie_values,
    first_in_window,
    new_secret,
    rate_limit,
    same_secret,
    set_cookie,
    token_hash,
)
from app.webauthn_wrapper import (
    WebAuthnFailure,
    apply_counter_rule,
    creation_options,
    request_options,
    verify_assertion,
    verify_registration,
)

router = APIRouter(prefix="/api/v1")
PROFILE_FIELDS = "user_id display_name avatar_url bio timezone locale status created_at updated_at"
REGISTER_PURPOSES = {
    "invite": "register_invite",
    "recovery": "register_recovery",
    "add_credential": "register_add",
}
BINDING_TTL = 600


def authentication_failed():
    return Problem(401, "authentication_failed", "Authentication failed")


def ceremony_invalid():
    return Problem(400, "ceremony_invalid")


# ---------- shared helpers ----------


def binding_value(request: Request, settings):
    values = cookie_values(raw_cookie_headers(request), settings.binding_cookie)
    if len(values) == 1 and BINDING_RE.fullmatch(values[0]):
        return values[0]
    return None


def issue_binding(request, response, settings):
    value = binding_value(request, settings) or new_secret()
    set_cookie(response, settings, settings.binding_cookie, value, BINDING_TTL)
    return token_hash(value)


def presented_binding(request, settings):
    value = binding_value(request, settings)
    if value is None:
        raise ceremony_invalid()
    return token_hash(value)


def app_host(settings):
    return urlsplit(settings.app_origin).hostname


async def check_gate(settings, token, user_id=None):
    """AUTH-14: non-owner admission and recovery stay closed (DESIGN 3.1)."""
    if settings.non_owner_admission:
        return
    target = token["user_id"] if token else user_id
    if token and token["purpose"] == "invite":
        raise Problem(403, "admission_disabled")
    if target is None or target != settings.owner_user_id:
        raise Problem(403, "admission_disabled")


async def load_ceremony_for(db, request, settings, ceremony_id, purposes, now):
    """Unlocked read. Wrong browser/purpose never consumes the ceremony (AUTH-4 step 1)."""
    binding = presented_binding(request, settings)
    row = await db.one("SELECT * FROM webauthn_ceremonies WHERE id = %s", (ceremony_id,))
    if (
        row is None
        or row["purpose"] not in purposes
        or not same_secret(bytes(row["browser_binding_hash"]), binding)
        or not ceremony_usable(row, now)
    ):
        raise ceremony_invalid()
    return row


async def burn(database, ceremony_id, events, now):
    """Consume a ceremony after a controlled failure and record failure events."""
    async with database.transaction() as db:
        await consume_ceremony(db, ceremony_id, now)
        for event in events:
            await record_event(db, now=now, **event)


async def finish_failure(database, ceremony_id, fail: Fail, now):
    await burn(database, ceremony_id, fail.events, now)
    return fail.problem


def profile_body(row):
    return fields(row, PROFILE_FIELDS)


def parse_credential_id(credential):
    try:
        return b64url_decode(credential["rawId"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WebAuthnFailure from exc


def session_response(response, settings, token, max_age):
    set_cookie(response, settings, settings.session_cookie, token, max_age)


async def reauth_ok(session_row, now):
    reauth = session_row["reauth_at"]
    return reauth is not None and now - reauth <= REAUTH_WINDOW


# ---------- registration ----------


def live_token_state(token, now):
    if token is None or token["revoked_at"] is not None:
        raise Problem(400, "enrollment_token_invalid")
    if token["used_at"] is not None:
        raise Problem(409, "enrollment_token_used")
    if token["expires_at"] <= now:
        raise Problem(409, "enrollment_token_expired")


@router.post("/auth/registration/options", operation_id="registerOptions")
async def registration_options(request: Request, response: Response):
    app = request.app.state
    settings, database = app.settings, app.database
    await rate_limit(request, settings, database)
    data = await body(request, "registerOptions")
    now = clock.now()
    thash = token_hash(data["enrollment_token"])
    async with database.transaction() as db:
        token = await db.one("SELECT * FROM enrollment_tokens WHERE token_hash = %s", (thash,))
        live_token_state(token, now)
        if token["purpose"] != "invite" and "display_name" in data:
            raise Problem(422, "validation_error")
        await check_gate(settings, token)
        user = None
        if token["user_id"]:
            user = await db.one("SELECT * FROM profiles WHERE user_id = %s", (token["user_id"],))
            if user is None:
                raise Problem(400, "enrollment_token_invalid")
            if user["status"] != "active":
                raise Problem(403, "account_disabled")
    # Lifetime attempt budget: atomic, committed even if a later step fails.
    async with database.transaction() as db:
        counted = await db.one(
            "UPDATE enrollment_tokens SET options_count = options_count + 1 "
            "WHERE id = %s AND options_count < %s AND used_at IS NULL AND revoked_at IS NULL "
            "AND expires_at > %s RETURNING id",
            (token["id"], OPTIONS_BUDGET, now),
        )
    if counted is None:
        async with database.transaction() as db:
            fresh = await db.one("SELECT * FROM enrollment_tokens WHERE id = %s", (token["id"],))
        live_token_state(fresh, now)
        raise Problem(429, "rate_limited")

    purpose = REGISTER_PURPOSES[token["purpose"]]
    challenge = secrets.token_bytes(32)
    binding = issue_binding(request, response, settings)
    display = data.get("display_name")
    async with database.transaction() as db:
        exclude = []
        if purpose == "register_invite":
            pending, user_handle = uuid4(), None
            user_handle = pending.bytes
            shown = display or "Daily Brief"
            ceremony_id = await create_ceremony(
                db,
                now,
                purpose=purpose,
                challenge=challenge,
                binding_hash=binding,
                pending_user_id=pending,
                token_id=token["id"],
                display_name=display,
            )
        else:
            user_handle = token["user_id"].bytes
            shown = user["display_name"] or "Daily Brief"
            if purpose == "register_add":
                creds = await db.all(
                    "SELECT credential_id FROM passkey_credentials "
                    "WHERE user_id = %s AND revoked_at IS NULL",
                    (token["user_id"],),
                )
                exclude = [bytes(c["credential_id"]) for c in creds]
            ceremony_id = await create_ceremony(
                db,
                now,
                purpose=purpose,
                challenge=challenge,
                binding_hash=binding,
                user_id=token["user_id"],
                token_id=token["id"],
            )
    options = creation_options(
        settings,
        challenge=challenge,
        user_id=user_handle,
        user_name=f"{shown} · {app_host(settings)}",
        display_name=shown,
        exclude=exclude,
    )
    return {"ceremony_id": ceremony_id, "public_key": options}


async def registration_checks(settings, token, profile, credentials, now, purpose):
    """Re-validate everything after locking (AUTH-4 step 3). Raises Fail."""
    try:
        live_token_state(token, now)
        if purpose == "invite":
            await check_gate(settings, token)
        else:
            if profile is None or profile["status"] != "active":
                raise Problem(403, "account_disabled")
            await check_gate(settings, token)
    except Problem as problem:
        raise Fail(problem) from problem
    if purpose == "add_credential" and len(active_credentials(credentials)) >= MAX_PASSKEYS:
        raise Fail(Problem(409, "passkey_limit"))


@router.post("/auth/registration/verify", operation_id="registerVerify")
async def registration_verify(request: Request, response: Response):
    app = request.app.state
    settings, database = app.settings, app.database
    await rate_limit(request, settings, database)
    data = await body(request, "registerVerify")
    ceremony_id = UUID(data["ceremony_id"])
    now = clock.now()
    async with database.transaction() as db:
        ceremony = await load_ceremony_for(
            db, request, settings, ceremony_id, set(REGISTER_PURPOSES.values()), now
        )
    try:
        registered = verify_registration(settings, data["credential"], bytes(ceremony["challenge"]))
    except WebAuthnFailure:
        raise await finish_failure(
            database, ceremony_id, Fail(Problem(400, "registration_failed")), now
        ) from None
    label = data.get("label", "")
    try:
        profile, session = await commit_registration(
            database, settings, ceremony_id, registered, label, now
        )
    except Fail as fail:
        raise await finish_failure(database, ceremony_id, fail, now) from None
    token, _session_id, max_age = session
    session_response(response, settings, token, max_age)
    return profile_body(profile)


async def commit_registration(database, settings, ceremony_id, registered, label, now):
    async with database.transaction() as db:
        # Unlocked read decides which chain to lock; every value is re-read under lock.
        first = await db.one("SELECT * FROM webauthn_ceremonies WHERE id = %s", (ceremony_id,))
        token_id = first["enrollment_token_id"]
        purpose = {v: k for k, v in REGISTER_PURPOSES.items()}[first["purpose"]]
        profile = credentials = None
        if purpose != "invite":
            profile = await lock_profile(db, first["user_id"])
        token = await lock_token(db, token_id)
        if purpose != "invite":
            await lock_sessions(db, first["user_id"])
            credentials = await lock_credentials(db, first["user_id"])
        ceremony = await lock_ceremony(db, ceremony_id)
        if not ceremony_usable(ceremony, now):
            raise Problem(400, "ceremony_invalid")
        await registration_checks(settings, token, profile, credentials or [], now, purpose)
        if await credential_exists(db, registered.credential_id):
            raise Fail(Problem(409, "credential_exists"))

        if purpose == "invite":
            user_id = ceremony["pending_user_id"]
            profile = await db.one(
                "INSERT INTO profiles (user_id, display_name, status, created_at) "
                "VALUES (%s, %s, 'active', %s) RETURNING *",
                (user_id, ceremony["display_name"] or "", now),
            )
            # Non-owner profiles never use the subscription runner (AUTH-14, DESIGN 3.1).
            await db.execute(
                "INSERT INTO agent_settings (user_id, runner) VALUES (%s, 'anthropic_api')",
                (user_id,),
            )
        else:
            user_id = profile["user_id"]

        revoked_sessions = []
        if purpose == "recovery":
            await db.execute(
                "UPDATE passkey_credentials SET revoked_at = %s "
                "WHERE user_id = %s AND revoked_at IS NULL",
                (now, user_id),
            )
            revoked_sessions = await revoke_sessions(db, user_id, "recovery", now)
        if purpose in {"recovery", "add_credential"}:
            replace_all = purpose == "recovery"
            await db.execute(
                "UPDATE enrollment_tokens SET revoked_at = %s WHERE user_id = %s AND id <> %s "
                "AND used_at IS NULL AND revoked_at IS NULL AND (%s OR purpose = 'add_credential')",
                (now, user_id, token_id, replace_all),
            )
            await db.execute(
                "UPDATE webauthn_ceremonies SET consumed_at = %s WHERE user_id = %s AND id <> %s "
                "AND consumed_at IS NULL AND (%s OR enrollment_token_id IN ("
                "SELECT id FROM enrollment_tokens WHERE purpose = 'add_credential'))",
                (now, user_id, ceremony_id, replace_all),
            )

        credential_row_id = await insert_credential(db, user_id, registered, label, now)
        await db.execute(
            "UPDATE enrollment_tokens SET used_at = %s, used_by = %s WHERE id = %s",
            (now, user_id, token_id),
        )
        session = await create_session(db, user_id, credential_row_id, now)
        await consume_ceremony(db, ceremony_id, now)
        main = "recovery_completed" if purpose == "recovery" else "registration_succeeded"
        await record_event(
            db,
            main,
            user_id=user_id,
            credential_id=credential_row_id,
            session_id=session[1],
            now=now,
        )
        await record_event(db, "token_used", user_id=user_id, token_id=token_id, now=now)
        for _ in revoked_sessions:
            await record_event(db, "session_revoked", user_id=user_id, now=now)
    return profile, session


# ---------- sign in ----------


@router.post("/auth/login/options", operation_id="loginOptions")
async def login_options(request: Request, response: Response):
    app = request.app.state
    settings, database = app.settings, app.database
    await rate_limit(request, settings, database)
    await body(request, "loginOptions")
    now = clock.now()
    challenge = secrets.token_bytes(32)
    binding = issue_binding(request, response, settings)
    async with database.transaction() as db:
        ceremony_id = await create_ceremony(
            db, now, purpose="login", challenge=challenge, binding_hash=binding
        )
    return {
        "ceremony_id": ceremony_id,
        "public_key": request_options(settings, challenge=challenge, allow=[]),
    }


async def stored_credential(db, credential_id: bytes):
    return await db.one(
        "SELECT * FROM passkey_credentials WHERE credential_id = %s", (credential_id,)
    )


def prove_assertion(settings, credential, challenge, stored, *, owner_id=None, require_handle=True):
    """Raise WebAuthnFailure unless the assertion is valid for exactly this stored credential."""
    if stored is None or stored["revoked_at"] is not None:
        raise WebAuthnFailure
    if owner_id is not None and stored["user_id"] != owner_id:
        raise WebAuthnFailure
    assertion = verify_assertion(settings, credential, challenge, bytes(stored["public_key"]))
    if (
        assertion.credential_id != bytes(stored["credential_id"])
        # Discoverable login must carry the handle; with allowCredentials (reauth) it may be
        # omitted, but a supplied handle must still match.
        or (assertion.user_handle is None and require_handle)
        or (assertion.user_handle is not None and assertion.user_handle != stored["user_id"].bytes)
        or assertion.backup_eligible != stored["backup_eligible"]
    ):
        raise WebAuthnFailure
    return assertion


def counter_events(stored, assertion, user_id):
    verdict = apply_counter_rule(
        int(stored["sign_count"]), assertion.sign_count, stored["backup_eligible"]
    )
    events = []
    if verdict != "ok":
        events.append(
            {"event": "sign_count_regression", "user_id": user_id, "credential_id": stored["id"]}
        )
    return verdict, events


async def write_counter(db, stored, assertion, now):
    await db.execute(
        "UPDATE passkey_credentials SET sign_count = greatest(sign_count, %s), "
        "backup_state = %s, last_used_at = %s WHERE id = %s",
        (assertion.sign_count, assertion.backup_state, now, stored["id"]),
    )


@router.post("/auth/login/verify", operation_id="loginVerify")
async def login_verify(request: Request, response: Response):
    app = request.app.state
    settings, database = app.settings, app.database
    key, window = await rate_limit(request, settings, database)
    data = await body(request, "loginVerify")
    ceremony_id = UUID(data["ceremony_id"])
    now = clock.now()
    credential = data["credential"]
    async with database.transaction() as db:
        ceremony = await load_ceremony_for(db, request, settings, ceremony_id, {"login"}, now)
        try:
            stored = await stored_credential(db, parse_credential_id(credential))
        except WebAuthnFailure:
            stored = None
    try:
        assertion = prove_assertion(settings, credential, bytes(ceremony["challenge"]), stored)
    except WebAuthnFailure:
        events = []
        if await first_in_window(database, key, window):
            events = [{"event": "login_failed", "user_id": stored["user_id"] if stored else None}]
        raise await finish_failure(
            database, ceremony_id, Fail(authentication_failed(), events), now
        ) from None
    try:
        profile, session = await commit_login(
            database, settings, ceremony_id, stored, assertion, now
        )
    except Fail as fail:
        raise await finish_failure(database, ceremony_id, fail, now) from None
    token, _session_id, max_age = session
    session_response(response, settings, token, max_age)
    return profile_body(profile)


async def commit_login(database, settings, ceremony_id, stored, assertion, now):
    user_id = stored["user_id"]
    async with database.transaction() as db:
        profile = await lock_profile(db, user_id)
        await lock_sessions(db, user_id)
        credentials = await lock_credentials(db, user_id)
        ceremony = await lock_ceremony(db, ceremony_id)
        if not ceremony_usable(ceremony, now):
            raise Problem(400, "ceremony_invalid")
        current = next((c for c in credentials if c["id"] == stored["id"]), None)
        if (
            current is None
            or current["revoked_at"] is not None
            or bytes(current["public_key"]) != bytes(stored["public_key"])
        ):
            raise Fail(authentication_failed())
        if profile is None or profile["status"] != "active":
            raise Fail(Problem(403, "account_disabled"))
        verdict, events = counter_events(current, assertion, user_id)
        if verdict == "regression_denied":
            raise Fail(authentication_failed(), events)
        for event in events:
            await record_event(db, now=now, **event)
        await write_counter(db, current, assertion, now)
        session = await create_session(db, user_id, current["id"], now)
        await consume_ceremony(db, ceremony_id, now)
        await record_event(
            db,
            "login_succeeded",
            user_id=user_id,
            credential_id=current["id"],
            session_id=session[1],
            now=now,
        )
    return profile, session


# ---------- reauthentication ----------


@router.post("/auth/reauth/options", operation_id="reauthOptions")
async def reauth_options(request: Request, response: Response):
    app = request.app.state
    settings, database = app.settings, app.database
    await rate_limit(request, settings, database)
    me = await app.authenticator.authenticate(request)
    now = clock.now()
    challenge = secrets.token_bytes(32)
    binding = issue_binding(request, response, settings)
    async with database.transaction() as db:
        creds = await db.all(
            "SELECT credential_id FROM passkey_credentials "
            "WHERE user_id = %s AND revoked_at IS NULL ORDER BY created_at, id",
            (me.user_id,),
        )
        ceremony_id = await create_ceremony(
            db,
            now,
            purpose="reauth",
            challenge=challenge,
            binding_hash=binding,
            user_id=me.user_id,
            session_id=me.session_id,
        )
    allow = [bytes(c["credential_id"]) for c in creds]
    return {
        "ceremony_id": ceremony_id,
        "public_key": request_options(settings, challenge=challenge, allow=allow),
    }


@router.post("/auth/reauth/verify", operation_id="reauthVerify", status_code=204)
async def reauth_verify(request: Request):
    app = request.app.state
    settings, database = app.settings, app.database
    me = await app.authenticator.authenticate(request)
    data = await body(request, "reauthVerify")
    ceremony_id = UUID(data["ceremony_id"])
    now = clock.now()
    credential = data["credential"]
    async with database.transaction() as db:
        ceremony = await load_ceremony_for(db, request, settings, ceremony_id, {"reauth"}, now)
        if ceremony["session_id"] != me.session_id or ceremony["user_id"] != me.user_id:
            raise ceremony_invalid()
        try:
            stored = await stored_credential(db, parse_credential_id(credential))
        except WebAuthnFailure:
            stored = None
    try:
        assertion = prove_assertion(
            settings,
            credential,
            bytes(ceremony["challenge"]),
            stored,
            owner_id=me.user_id,
            require_handle=False,
        )
    except WebAuthnFailure:
        raise await finish_failure(
            database, ceremony_id, Fail(authentication_failed()), now
        ) from None
    try:
        await commit_reauth(database, ceremony_id, me, stored, assertion, now)
    except Fail as fail:
        raise await finish_failure(database, ceremony_id, fail, now) from None
    return Response(status_code=204)


async def commit_reauth(database, ceremony_id, me, stored, assertion, now):
    async with database.transaction() as db:
        profile = await lock_profile(db, me.user_id)
        sessions = await lock_sessions(db, me.user_id)
        credentials = await lock_credentials(db, me.user_id)
        ceremony = await lock_ceremony(db, ceremony_id)
        if not ceremony_usable(ceremony, now):
            raise Problem(400, "ceremony_invalid")
        session = next(
            (
                s
                for s in sessions
                if s["id"] == me.session_id
                and s["idle_expires_at"] > now
                and s["absolute_expires_at"] > now
            ),
            None,
        )
        if profile is None or profile["status"] != "active" or session is None:
            raise Fail(Problem(401, "unauthorized"))
        current = next((c for c in credentials if c["id"] == stored["id"]), None)
        if (
            current is None
            or current["revoked_at"] is not None
            or bytes(current["public_key"]) != bytes(stored["public_key"])
        ):
            raise Fail(authentication_failed())
        verdict, events = counter_events(current, assertion, me.user_id)
        if verdict == "regression_denied":
            raise Fail(authentication_failed(), events)
        for event in events:
            await record_event(db, now=now, **event)
        await write_counter(db, current, assertion, now)
        await db.execute(
            "UPDATE auth_sessions SET reauth_at = %s WHERE id = %s", (now, me.session_id)
        )
        await consume_ceremony(db, ceremony_id, now)
        await record_event(
            db,
            "reauth_succeeded",
            user_id=me.user_id,
            session_id=me.session_id,
            credential_id=current["id"],
            now=now,
        )


# ---------- logout and sessions ----------


async def revoke_one_session(request, me, session_id, reason):
    database = request.app.state.database
    now = clock.now()
    async with database.transaction() as db:
        row = await db.one(
            "UPDATE auth_sessions SET revoked_at = %s, revoke_reason = %s "
            "WHERE id = %s AND user_id = %s AND revoked_at IS NULL RETURNING id",
            (now, reason, session_id, me.user_id),
        )
        if row is None:
            return False
        await record_event(
            db, "session_revoked", user_id=me.user_id, session_id=session_id, now=now
        )
    return True


@router.post("/auth/logout", operation_id="logout", status_code=204)
async def logout(request: Request, me: Authenticated):
    settings = request.app.state.settings
    await revoke_one_session(request, me, me.session_id, "logout")
    response = Response(status_code=204)
    clear_cookie(response, settings, settings.session_cookie)
    return response


@router.get("/me/sessions", operation_id="listSessions")
async def list_sessions(request: Request, me: Authenticated):
    now = clock.now()
    async with request.app.state.database.transaction() as db:
        rows = await db.all(
            "SELECT id, created_at, last_seen_at FROM auth_sessions WHERE user_id = %s "
            "AND revoked_at IS NULL AND idle_expires_at > %s AND absolute_expires_at > %s "
            "ORDER BY created_at DESC, id LIMIT 20",
            (me.user_id, now, now),
        )
    return {
        "sessions": [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "last_seen_at": r["last_seen_at"],
                "is_current": r["id"] == me.session_id,
            }
            for r in rows
        ]
    }


@router.delete("/me/sessions/{session_id}", operation_id="deleteSession", status_code=204)
async def delete_session(session_id: UUID, request: Request, me: Authenticated):
    settings = request.app.state.settings
    current = session_id == me.session_id
    if not await revoke_one_session(
        request, me, session_id, "logout" if current else "user_revoked"
    ):
        raise Problem(404, "not_found")
    response = Response(status_code=204)
    if current:
        clear_cookie(response, settings, settings.session_cookie)
    return response


# ---------- passkey management ----------


def passkey_body(row, current_credential_id):
    return {
        "id": row["id"],
        "label": row["label"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "backup_eligible": row["backup_eligible"],
        "backup_state": row["backup_state"],
        "is_current": row["id"] == current_credential_id,
    }


@router.get("/me/passkeys", operation_id="listPasskeys")
async def list_passkeys(request: Request, me: Authenticated):
    async with request.app.state.database.transaction() as db:
        rows = await db.all(
            "SELECT * FROM passkey_credentials WHERE user_id = %s AND revoked_at IS NULL "
            "ORDER BY created_at, id LIMIT %s",
            (me.user_id, MAX_PASSKEYS),
        )
    return {"passkeys": [passkey_body(r, me.credential_id) for r in rows]}


@router.post("/me/passkeys/options", operation_id="addPasskeyOptions")
async def add_passkey_options(request: Request, response: Response, me: Authenticated):
    app = request.app.state
    settings, database = app.settings, app.database
    now = clock.now()
    if not me.recently_reauthenticated(now):
        raise Problem(403, "reauth_required")
    challenge = secrets.token_bytes(32)
    binding = issue_binding(request, response, settings)
    async with database.transaction() as db:
        user = await db.one("SELECT * FROM profiles WHERE user_id = %s", (me.user_id,))
        creds = await db.all(
            "SELECT credential_id FROM passkey_credentials "
            "WHERE user_id = %s AND revoked_at IS NULL",
            (me.user_id,),
        )
        if len(creds) >= MAX_PASSKEYS:
            raise Problem(409, "passkey_limit")
        ceremony_id = await create_ceremony(
            db,
            now,
            purpose="add_passkey",
            challenge=challenge,
            binding_hash=binding,
            user_id=me.user_id,
            session_id=me.session_id,
        )
    shown = user["display_name"] or "Daily Brief"
    options = creation_options(
        settings,
        challenge=challenge,
        user_id=me.user_id.bytes,
        user_name=f"{shown} · {app_host(settings)}",
        display_name=shown,
        exclude=[bytes(c["credential_id"]) for c in creds],
    )
    return {"ceremony_id": ceremony_id, "public_key": options}


@router.post("/me/passkeys/verify", operation_id="addPasskeyVerify", status_code=201)
async def add_passkey_verify(request: Request, me: Authenticated):
    app = request.app.state
    settings, database = app.settings, app.database
    now = clock.now()
    if not me.recently_reauthenticated(now):
        raise Problem(403, "reauth_required")
    data = await body(request, "addPasskeyVerify")
    ceremony_id = UUID(data["ceremony_id"])
    async with database.transaction() as db:
        ceremony = await load_ceremony_for(db, request, settings, ceremony_id, {"add_passkey"}, now)
        if ceremony["session_id"] != me.session_id or ceremony["user_id"] != me.user_id:
            raise ceremony_invalid()
    try:
        registered = verify_registration(settings, data["credential"], bytes(ceremony["challenge"]))
    except WebAuthnFailure:
        raise await finish_failure(
            database, ceremony_id, Fail(Problem(400, "registration_failed")), now
        ) from None
    try:
        row = await commit_add_passkey(
            database, ceremony_id, me, registered, data.get("label", ""), now
        )
    except Fail as fail:
        raise await finish_failure(database, ceremony_id, fail, now) from None
    return passkey_body(row, me.credential_id)


async def commit_add_passkey(database, ceremony_id, me, registered, label, now):
    async with database.transaction() as db:
        profile = await lock_profile(db, me.user_id)
        sessions = await lock_sessions(db, me.user_id)
        credentials = await lock_credentials(db, me.user_id)
        ceremony = await lock_ceremony(db, ceremony_id)
        if not ceremony_usable(ceremony, now):
            raise Problem(400, "ceremony_invalid")
        session = next(
            (
                s
                for s in sessions
                if s["id"] == me.session_id
                and s["idle_expires_at"] > now
                and s["absolute_expires_at"] > now
            ),
            None,
        )
        if profile is None or profile["status"] != "active" or session is None:
            raise Fail(Problem(401, "unauthorized"))
        if not await reauth_ok(session, now):
            raise Fail(Problem(403, "reauth_required"))
        if len(active_credentials(credentials)) >= MAX_PASSKEYS:
            raise Fail(Problem(409, "passkey_limit"))
        if await credential_exists(db, registered.credential_id):
            raise Fail(Problem(409, "credential_exists"))
        new_id = await insert_credential(db, me.user_id, registered, label, now)
        await consume_ceremony(db, ceremony_id, now)
        await record_event(
            db,
            "passkey_added",
            user_id=me.user_id,
            credential_id=new_id,
            session_id=me.session_id,
            now=now,
        )
        return await db.one("SELECT * FROM passkey_credentials WHERE id = %s", (new_id,))


@router.delete("/me/passkeys/{passkey_id}", operation_id="deletePasskey", status_code=204)
async def delete_passkey(passkey_id: UUID, request: Request, me: Authenticated):
    settings, database = request.app.state.settings, request.app.state.database
    now = clock.now()
    if not me.recently_reauthenticated(now):
        raise Problem(403, "reauth_required")
    clear = False
    async with database.transaction() as db:
        await lock_profile(db, me.user_id)
        sessions = await lock_sessions(db, me.user_id)
        credentials = await lock_credentials(db, me.user_id)
        session = next((s for s in sessions if s["id"] == me.session_id), None)
        if session is None or session["idle_expires_at"] <= now:
            raise Problem(401, "unauthorized")
        if not await reauth_ok(session, now):
            raise Problem(403, "reauth_required")
        target = next((c for c in credentials if c["id"] == passkey_id), None)
        if target is None or target["revoked_at"] is not None:
            raise Problem(404, "not_found")
        others = [c for c in active_credentials(credentials) if c["id"] != passkey_id]
        if not others:
            raise Problem(409, "last_passkey")
        await db.execute(
            "UPDATE passkey_credentials SET revoked_at = %s WHERE id = %s", (now, passkey_id)
        )
        revoked = await revoke_sessions(
            db, me.user_id, "passkey_revoked", now, credential_id=passkey_id
        )
        clear = any(r["id"] == me.session_id for r in revoked)
        await record_event(
            db, "passkey_revoked", user_id=me.user_id, credential_id=passkey_id, now=now
        )
        for r in revoked:
            await record_event(
                db, "session_revoked", user_id=me.user_id, session_id=r["id"], now=now
            )
    response = Response(status_code=204)
    if clear:
        clear_cookie(response, settings, settings.session_cookie)
    return response
