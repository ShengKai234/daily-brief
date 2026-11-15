import os

from sqlalchemy.engine import URL, make_url


def _parse(value: str, name: str) -> URL:
    url = make_url(value)
    if url.drivername not in {"postgresql", "postgresql+asyncpg"}:
        raise ValueError(f"{name} must use PostgreSQL with asyncpg")
    return url.set(drivername="postgresql+asyncpg")


def database_url() -> URL:
    """URL for migrations and seeds: MIGRATION_DATABASE_URL (brief_migrator) when set.

    Falls back to DATABASE_URL for host development and disposable test databases.
    Never log the URL; credentials are supplied only through the environment.
    """
    for name in ("MIGRATION_DATABASE_URL", "DATABASE_URL"):
        value = os.environ.get(name)
        if value:
            return _parse(value, name)
    raise ValueError("MIGRATION_DATABASE_URL or DATABASE_URL is required")


def role_url(name: str) -> URL:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is required")
    return _parse(value, name)
