"""Token, cookie and client-address helpers. Nothing here logs; values are secrets."""

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
from datetime import timedelta

from app import clock
from app.problems import Problem

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
BINDING_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
RATE_WINDOW = timedelta(minutes=10)
RATE_LIMIT = 20


def new_secret() -> str:
    """256-bit CSPRNG value, base64url without padding (43 characters)."""
    return secrets.token_urlsafe(32)


def token_hash(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def same_secret(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)


def cookie_values(header_values, name):
    """All values of the named cookie across every Cookie header (duplicates matter)."""
    found = []
    for header in header_values:
        for part in header.split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                found.append(value.strip('"'))
    return found


def set_cookie(response, settings, name, value, max_age):
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path="/",
        secure=not settings.local,
        httponly=True,
        samesite="strict",
    )


def clear_cookie(response, settings, name):
    set_cookie(response, settings, name, "", 0)


def client_key(request, settings) -> str:
    """Rate-limit identity: HMAC of the client IP (IPv6 as /64). Never X-Forwarded-For."""
    if settings.local:
        host = request.client.host if request.client else ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = ipaddress.ip_address("127.0.0.1")
    else:
        address = parse_viewer_address(request.headers.get("cloudfront-viewer-address", ""))
    if address.version == 6:
        address = ipaddress.ip_network((address, 64), strict=False).network_address
    return hmac.new(
        settings.rate_limit_key.encode() or b"local-only", str(address).encode(), hashlib.sha256
    ).hexdigest()[:32]


def parse_viewer_address(value: str):
    """`ip:port` from CloudFront; IPv6 may arrive bracketed. Malformed means reject."""
    value = value.strip()
    try:
        if value.startswith("["):
            host, _, rest = value[1:].partition("]")
            if not rest.startswith(":") or not rest[1:].isdigit():
                raise ValueError
            return ipaddress.ip_address(host)
        host, sep, port = value.rpartition(":")
        if not sep or not port.isdigit() or not 0 < int(port) < 65536:
            raise ValueError
        return ipaddress.ip_address(host)
    except ValueError as exc:
        raise Problem(400, "bad_request") from exc


async def rate_limit(request, settings, database, scope="auth"):
    """Fixed-window per-IP counter, committed even when the request later fails."""
    key = f"ip:{client_key(request, settings)}:{scope}"
    now = clock.now()
    start = now.replace(minute=now.minute - now.minute % 10, second=0, microsecond=0)
    async with database.transaction() as db:
        row = await db.one(
            "INSERT INTO auth_rate_limits (bucket, window_start, hits) VALUES (%s, %s, 1) "
            "ON CONFLICT (bucket, window_start) DO UPDATE SET hits = auth_rate_limits.hits + 1 "
            "RETURNING hits",
            (key, start),
        )
    if row["hits"] > RATE_LIMIT:
        retry = max(1, int((start + RATE_WINDOW - now).total_seconds()))
        raise Problem(429, "rate_limited", headers={"Retry-After": str(retry)})
    return key, start


async def first_in_window(database, key, start):
    """True only for the first caller of a bucket window (limits event spam)."""
    async with database.transaction() as db:
        row = await db.one(
            "INSERT INTO auth_rate_limits (bucket, window_start, hits) VALUES (%s, %s, 1) "
            "ON CONFLICT (bucket, window_start) DO UPDATE SET hits = auth_rate_limits.hits + 1 "
            "RETURNING hits",
            (key + ":failure_event", start),
        )
    return row["hits"] == 1
