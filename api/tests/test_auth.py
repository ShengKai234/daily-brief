import json
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import jwt
import pytest
from conftest import OWNER, token
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import DevVerifier, SupabaseVerifier
from app.config import Settings


def test_dev_auth_refuses_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_VERIFIER", "dev")
    with pytest.raises(ValueError, match="Dev authentication"):
        Settings.from_env()


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "other"},
        {"iss": "other"},
        {"role": "service_role"},
        {"sub": "invalid"},
        {"email": "invalid"},
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
    ],
)
async def test_dev_jwt_rejects_invalid_claims(changes):
    settings = Settings(dev_jwt_secret=secrets.token_urlsafe(32))
    with pytest.raises((jwt.PyJWTError, ValueError)):
        await DevVerifier(settings).verify(token(settings, **changes))


async def test_supabase_signature_claims_cache_and_key_rotation():
    settings = Settings(auth_verifier="supabase", supabase_url="https://project.example.com")
    verifier = SupabaseVerifier(settings)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    next_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwk(private, kid):
        return {
            **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key())),
            "kid": kid,
            "alg": "RS256",
        }

    keys = {"keys": [jwk(key, "first")]}
    response = httpx.Response(200, json=keys, request=httpx.Request("GET", verifier.url))
    get = AsyncMock(return_value=response)
    now = datetime.now(UTC)
    claims = {
        "sub": str(OWNER),
        "email": "owner@example.com",
        "role": "authenticated",
        "aud": "authenticated",
        "iss": "https://project.example.com/auth/v1",
        "iat": now,
        "exp": now + timedelta(minutes=10),
    }
    valid = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "first"})
    with patch("app.auth.httpx.AsyncClient.get", get):
        assert (await verifier.verify(valid)).user_id == OWNER
        assert (await verifier.verify(valid)).user_id == OWNER
        assert get.await_count == 1
        for changed in [{"aud": "other"}, {"iss": "other"}, {"exp": now - timedelta(minutes=1)}]:
            with pytest.raises(jwt.PyJWTError):
                await verifier.verify(
                    jwt.encode(
                        {**claims, **changed}, key, algorithm="RS256", headers={"kid": "first"}
                    )
                )
        with pytest.raises(jwt.PyJWTError):
            await verifier.verify(
                jwt.encode(claims, next_key, algorithm="RS256", headers={"kid": "first"})
            )
        with pytest.raises(ValueError):
            await verifier.verify(
                jwt.encode(
                    claims, secrets.token_urlsafe(32), algorithm="HS256", headers={"kid": "first"}
                )
            )
        # A key rotation refresh occurs after the bounded fetch cooldown.
        verifier.last_fetch -= 31
        get.return_value = httpx.Response(
            200,
            json={"keys": [jwk(next_key, "second")]},
            request=httpx.Request("GET", verifier.url),
        )
        rotated = jwt.encode(claims, next_key, algorithm="RS256", headers={"kid": "second"})
        assert (await verifier.verify(rotated)).user_id == OWNER
        assert get.await_count == 2
        with pytest.raises(KeyError):
            await verifier.verify(
                jwt.encode(claims, key, algorithm="RS256", headers={"kid": "unknown"})
            )
        assert get.await_count == 2


async def test_missing_owner_configuration_fails_closed(env):
    from conftest import queued_job, subscribe

    from app.jobs import PostgresJobQueue

    await subscribe(env)
    job = await queued_job(env)
    queue = PostgresJobQueue(env["database"], replace(env["settings"], owner_user_id=None))
    assert await queue.claim("example-worker") is None
    async with env["database"].transaction() as db:
        assert (await db.one("SELECT status FROM jobs WHERE id=%s", (job["id"],)))[
            "status"
        ] == "failed"
