import asyncio
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from alembic.config import Config

from alembic import command


def alembic_config():
    return Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


@pytest.fixture(scope="session")
def postgres():
    """Always use a disposable local container, never an ambient/cloud DATABASE_URL."""
    container = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "-p",
            "127.0.0.1::5432",
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "postgres:16",
        ],
        text=True,
    ).strip()
    try:
        address = subprocess.check_output(
            ["docker", "port", container, "5432/tcp"], text=True
        ).strip()
        port = int(address.rsplit(":", 1)[1])
        url = f"postgresql://postgres@127.0.0.1:{port}/postgres"
        deadline = time.monotonic() + 30

        async def ready():
            while time.monotonic() < deadline:
                try:
                    connection = await asyncpg.connect(url, timeout=1)
                    await connection.close()
                    return
                except (OSError, asyncpg.PostgresError):
                    await asyncio.sleep(0.2)
            raise RuntimeError("Disposable Postgres did not become ready")

        asyncio.run(ready())

        async def roles():
            connection = await asyncpg.connect(url)
            try:
                for role in ("anon", "authenticated", "rls_probe"):
                    await connection.execute(f"CREATE ROLE {role} NOLOGIN")
            finally:
                await connection.close()

        asyncio.run(roles())
        yield url
    finally:
        subprocess.run(["docker", "stop", container], check=True, capture_output=True)


@pytest.fixture
def database(postgres, monkeypatch):
    name = "test_" + uuid4().hex

    async def create():
        connection = await asyncpg.connect(postgres)
        try:
            await connection.execute(f'CREATE DATABASE "{name}"')
        finally:
            await connection.close()

    async def drop():
        connection = await asyncpg.connect(postgres)
        try:
            await connection.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        finally:
            await connection.close()

    asyncio.run(create())
    url = postgres.rsplit("/", 1)[0] + "/" + name
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        # Simulate the default table grants present on hosted Supabase databases.
        async def default_grants():
            connection = await asyncpg.connect(url)
            try:
                await connection.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT ALL ON TABLES TO anon, authenticated"
                )
            finally:
                await connection.close()

        asyncio.run(default_grants())
        command.upgrade(alembic_config(), "head")
        yield url
    finally:
        asyncio.run(drop())


@pytest_asyncio.fixture
async def connection(database):
    connection = await asyncpg.connect(database)
    try:
        yield connection
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def owner(connection):
    user_id = uuid4()
    await connection.execute(
        "INSERT INTO profiles (user_id, email) VALUES ($1, 'owner@example.com')", user_id
    )
    return user_id
