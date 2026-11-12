"""Explicit local-only helper; emits a short-lived dev token to stdout, never a file."""

import os
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings


def main():
    settings = Settings.from_env()
    if settings.app_env != "local" or settings.auth_verifier != "dev":
        raise ValueError("Dev token issuance requires APP_ENV=local and AUTH_VERIFIER=dev")
    if not settings.dev_jwt_secret or not settings.owner_user_id:
        raise ValueError("DEV_JWT_SECRET and OWNER_USER_ID are required")
    now = datetime.now(UTC)
    claims = {
        "sub": str(settings.owner_user_id),
        "email": os.environ["OWNER_EMAIL"],
        "role": "authenticated",
        "aud": "authenticated",
        "iss": "daily-brief-local",
        "iat": now,
        "exp": now + timedelta(minutes=30),
    }
    print(jwt.encode(claims, settings.dev_jwt_secret, algorithm="HS256"))


if __name__ == "__main__":
    main()
