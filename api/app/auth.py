"""Request authentication: browser sessions on /v1 and the service token on /internal."""

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request

from app import clock
from app.problems import Problem
from app.security import TOKEN_RE, cookie_values, token_hash

IDLE = timedelta(days=30)
ABSOLUTE = timedelta(days=90)
TOUCH_AFTER = timedelta(hours=1)
REAUTH_WINDOW = timedelta(minutes=5)


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    session_id: UUID
    credential_id: UUID | None  # passkey_credentials.id the session was created with
    kind: str
    reauth_at: datetime | None
    absolute_expires_at: datetime

    def recently_reauthenticated(self, now) -> bool:
        return self.reauth_at is not None and now - self.reauth_at <= REAUTH_WINDOW


def raw_cookie_headers(request: Request):
    return [
        value.decode("latin-1") for name, value in request.scope["headers"] if name == b"cookie"
    ]


def session_cookie_value(request: Request, settings):
    values = cookie_values(raw_cookie_headers(request), settings.session_cookie)
    if len(values) != 1 or not TOKEN_RE.fullmatch(values[0]):
        raise Problem(401, "unauthorized")
    return values[0]


class SessionAuthenticator:
    """AUTH-10: every request checks the session, its passkey and the profile in the database."""

    def __init__(self, settings, database):
        self.settings = settings
        self.database = database

    async def authenticate(self, request: Request) -> Principal:
        value = session_cookie_value(request, self.settings)
        now = clock.now()
        async with self.database.transaction() as db:
            row = await db.one(
                "SELECT s.id, s.user_id, s.kind, s.credential_id, s.reauth_at, s.last_seen_at, "
                "s.absolute_expires_at FROM auth_sessions s "
                "JOIN profiles p ON p.user_id = s.user_id "
                "LEFT JOIN passkey_credentials c ON c.id = s.credential_id "
                "WHERE s.token_hash = %s AND s.revoked_at IS NULL "
                "AND s.idle_expires_at > %s AND s.absolute_expires_at > %s "
                "AND p.status = 'active' "
                "AND ((s.kind = 'passkey' AND c.user_id = s.user_id AND c.revoked_at IS NULL) "
                "     OR (s.kind = 'dev' AND %s))",
                (token_hash(value), now, now, self.settings.local),
            )
            if row is None:
                raise Problem(401, "unauthorized")
            if now - row["last_seen_at"] > TOUCH_AFTER:
                await db.execute(
                    "UPDATE auth_sessions SET last_seen_at = %s, "
                    "idle_expires_at = least(%s, absolute_expires_at) "
                    "WHERE id = %s AND revoked_at IS NULL AND idle_expires_at > %s",
                    (now, now + IDLE, row["id"], now),
                )
        return Principal(
            user_id=row["user_id"],
            session_id=row["id"],
            credential_id=row["credential_id"],
            kind=row["kind"],
            reauth_at=row["reauth_at"],
            absolute_expires_at=row["absolute_expires_at"],
        )


async def principal(request: Request) -> Principal:
    return await request.app.state.authenticator.authenticate(request)


Authenticated = Annotated[Principal, Depends(principal)]


async def internal_auth(request: Request):
    settings = request.app.state.settings
    expected = settings.internal_service_token
    actual = request.headers.get("X-Internal-Token", "")
    has_session = bool(
        cookie_values(raw_cookie_headers(request), settings.session_cookie)
        or cookie_values(raw_cookie_headers(request), settings.binding_cookie)
    )
    if (
        request.headers.get("Authorization")
        or has_session
        or not expected
        or not hmac.compare_digest(actual.encode(), expected.encode())
    ):
        raise Problem(401, "unauthorized")
