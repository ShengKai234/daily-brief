import asyncio
import sys
from pathlib import Path

from alembic.config import Config

from alembic import command
from brief_db.seed import seed


def main():
    action = sys.argv[1:]
    if action not in ([], ["migrate"], ["seed"], ["init"]):
        raise SystemExit("Usage: python -m brief_db [migrate|seed|init]")
    try:
        if action != ["seed"]:
            command.upgrade(
                Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
            )
        if action in (["seed"], ["init"]):
            asyncio.run(seed())
    except Exception:
        # Driver exceptions can contain connection details and seed parameter values.
        raise SystemExit(
            "Database command failed; verify configuration and database state."
        ) from None
    print("Database command completed.")


if __name__ == "__main__":
    main()
