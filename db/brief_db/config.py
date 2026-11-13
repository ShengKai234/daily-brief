import os

from sqlalchemy.engine import URL, make_url


def database_url() -> URL:
    """Never log the URL; credentials are supplied only through the environment."""
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise ValueError("DATABASE_URL is required")
    url = make_url(value)
    if url.drivername not in {"postgresql", "postgresql+asyncpg"}:
        raise ValueError("DATABASE_URL must use PostgreSQL with asyncpg")
    return url.set(drivername="postgresql+asyncpg")
