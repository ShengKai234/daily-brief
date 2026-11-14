import asyncio
import hmac
import time
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx
import jwt
from fastapi import Request

from app.problems import Problem


@dataclass(frozen=True)
class Identity:
    user_id: UUID
    email: str


class AuthVerifier(Protocol):
    async def verify(self, token: str) -> Identity: ...


def identity(claims):
    if claims.get("role") != "authenticated":
        raise ValueError("Invalid role")
    email = claims["email"]
    if not isinstance(email, str) or "@" not in email:
        raise ValueError("Missing email")
    return Identity(UUID(claims["sub"]), email.lower())


REQUIRED = ["sub", "email", "exp", "iat", "aud", "iss", "role"]


class DevVerifier:
    def __init__(self, settings):
        if settings.app_env != "local":
            raise ValueError("Dev authentication requires local environment")
        self.secret = settings.dev_jwt_secret

    async def verify(self, token):
        if not self.secret:
            raise ValueError("Dev key is not configured")
        return identity(
            jwt.decode(
                token,
                self.secret,
                algorithms=["HS256"],
                audience="authenticated",
                issuer="daily-brief-local",
                options={"require": REQUIRED},
            )
        )


class SupabaseVerifier:
    def __init__(self, settings):
        self.issuer = settings.supabase_url.rstrip("/") + "/auth/v1"
        self.url = settings.supabase_jwks_url or self.issuer + "/.well-known/jwks.json"
        if not self.url.startswith("https://"):
            raise ValueError("JWKS requires HTTPS")
        self.keys = {}
        self.expires = 0
        self.last_fetch = -30
        self.lock = asyncio.Lock()

    async def verify(self, token):
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in {"RS256", "ES256"} or not header.get("kid"):
            raise ValueError("Unsupported signing algorithm")
        now = time.monotonic()
        if now >= self.expires or header["kid"] not in self.keys:
            async with self.lock:
                if time.monotonic() - self.last_fetch >= 30:
                    self.last_fetch = time.monotonic()
                    async with httpx.AsyncClient(timeout=5) as client:
                        response = await client.get(self.url)
                        response.raise_for_status()
                        self.keys = {
                            key["kid"]: jwt.PyJWK.from_dict(key)
                            for key in response.json()["keys"]
                            if key.get("alg") in {"RS256", "ES256"}
                        }
                    self.expires = time.monotonic() + 300
        key = self.keys[header["kid"]]
        if key.algorithm_name != header["alg"]:
            raise ValueError("Signing key mismatch")
        return identity(
            jwt.decode(
                token,
                key.key,
                algorithms=[key.algorithm_name],
                audience="authenticated",
                issuer=self.issuer,
                options={"require": REQUIRED},
            )
        )


async def principal(request: Request):
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or request.headers.get("X-Internal-Token"):
        raise Problem(401, "unauthorized")
    try:
        return await request.app.state.verifier.verify(token)
    except (jwt.PyJWTError, ValueError, KeyError, TypeError, httpx.HTTPError) as exc:
        raise Problem(401, "unauthorized") from exc


async def internal_auth(request: Request):
    expected = request.app.state.settings.internal_service_token
    actual = request.headers.get("X-Internal-Token", "")
    if (
        request.headers.get("Authorization")
        or not expected
        or not hmac.compare_digest(actual.encode(), expected.encode())
    ):
        raise Problem(401, "unauthorized")
