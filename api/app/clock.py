"""One clock for all auth decisions, so tests can move time without touching the database."""

from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now(UTC)
