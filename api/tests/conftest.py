import re
import secrets
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import jwt
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.encoders import jsonable_encoder

from app.config import Settings
from app.contract import SPEC, validate
from app.database import Database
from app.main import create_app

OWNER = UUID("00000000-0000-4000-8000-000000000001")


@pytest.fixture(scope="session")
def postgres():
    """Only disposable local Postgres: ambient DATABASE_URL is never used."""
    docker = shutil.which("docker")
    if not docker:
        pytest.fail("Docker is required for API integration tests")
    container = subprocess.check_output(  # noqa: S603 - fixed Docker args, no request input
        [
            docker,
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
        address = subprocess.check_output(  # noqa: S603 - ID returned by Docker
            [docker, "port", container, "5432/tcp"], text=True
        )
        port = int(address.strip().rsplit(":", 1)[1])
        url = f"postgresql://postgres@127.0.0.1:{port}/postgres"
        deadline = time.monotonic() + 30
        while True:
            try:
                with psycopg.connect(url, connect_timeout=1):
                    break
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        yield url
    finally:
        subprocess.run([docker, "stop", container], check=True, capture_output=True)  # noqa: S603


@pytest.fixture
def database_url(postgres, monkeypatch):
    name = "api_test_" + uuid4().hex
    with psycopg.connect(postgres, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name))
        )
    url = postgres.rsplit("/", 1)[0] + "/" + name
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        config = Config(str(Path(__file__).resolve().parents[2] / "db/alembic.ini"))
        command.upgrade(config, "head")
        yield url
    finally:
        with psycopg.connect(postgres, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    psycopg.sql.Identifier(name)
                )
            )


def token(settings, user_id=OWNER, email="owner@example.com", **overrides):
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "email": email,
        "role": "authenticated",
        "aud": "authenticated",
        "iss": "daily-brief-local",
        "iat": now,
        "exp": now + timedelta(minutes=10),
        **overrides,
    }
    return jwt.encode(claims, settings.dev_jwt_secret, algorithm="HS256")


class Client:
    """Assert EVERY integration HTTP response against the authoritative contract."""

    def __init__(self, raw, settings):
        self.raw = raw
        self.settings = settings
        self.operations = set()

    async def request(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {"Authorization": "Bearer " + token(self.settings)})
        response = await self.raw.request(method, "/api" + path, headers=headers, **kwargs)
        clean_path = path.split("?", 1)[0]
        for template, methods in SPEC["paths"].items():
            if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), clean_path):
                operation = methods[method.lower()]
                self.operations.add(operation["operationId"])
                declared = operation["responses"].get(str(response.status_code))
                assert declared is not None, (method, path, response.status_code, response.text)
                if "$ref" in declared:
                    declared = SPEC["components"]["responses"][declared["$ref"].split("/")[-1]]
                content = declared.get("content")
                if content:
                    media, description = next(iter(content.items()))
                    assert response.headers["content-type"].split(";")[0] == media
                    validate(response.json(), description["schema"])
                else:
                    assert response.content == b""
                return response
        raise AssertionError(f"No contract route: {method} {path}")

    async def get(self, path, **kwargs):
        return await self.request("GET", path, **kwargs)

    async def post(self, path, **kwargs):
        return await self.request("POST", path, **kwargs)

    async def patch(self, path, **kwargs):
        return await self.request("PATCH", path, **kwargs)

    async def put(self, path, **kwargs):
        return await self.request("PUT", path, **kwargs)

    async def delete(self, path, **kwargs):
        return await self.request("DELETE", path, **kwargs)


@pytest.fixture
async def env(database_url):
    settings = Settings(
        database_url=database_url,
        dev_jwt_secret=secrets.token_urlsafe(32),
        internal_service_token=secrets.token_urlsafe(32),
        owner_user_id=OWNER,
    )
    app = create_app(settings)
    database = Database(settings)
    async with database.transaction() as db:
        await db.execute("INSERT INTO allowed_emails (email) VALUES ('owner@example.com')")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as raw:
        client = Client(raw, settings)
        assert (await client.get("/v1/me")).status_code == 200
        yield {
            "settings": settings,
            "app": app,
            "database": database,
            "client": client,
            "internal": {"X-Internal-Token": settings.internal_service_token},
        }


async def subscribe(env, title="Technology", **kwargs):
    response = await env["client"].post("/v1/subscriptions", json={"title": title, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


async def queued_job(env, user_id=OWNER, runner="claude_subscription", **values):
    async with env["database"].transaction() as db:
        row = await db.one(
            "INSERT INTO jobs (user_id, scheduled_for, runner) "
            "VALUES (%s, now()-interval '1 minute', %s) RETURNING *",
            (user_id, runner),
        )
        if values:
            row = await db.update("jobs", values, "id=%s", (row["id"],))
        return row


async def claim(env):
    response = await env["client"].post(
        "/internal/jobs/claim", headers=env["internal"], json={"worker_id": "test-worker"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def result_for(claimed):
    request = jsonable_encoder(claimed["request"])
    return {
        "report_date": request["report_date"],
        "locale": request["locale"],
        "sections": [
            {
                "topic_id": topic["topic_id"],
                "title": topic["title"],
                "status": "ok",
                "items": [
                    {
                        "rank": 1,
                        "title": "Example headline",
                        "summary": "A summary.",
                        "source_name": "Example",
                        "source_url": "https://example.com/",
                        "published_at": None,
                    }
                ],
            }
            for topic in request["topics"]
        ],
        "usage": {
            "runner": "stub",
            "model": "stub",
            "duration_ms": 0,
            "search_count": 0,
            "input_tokens": None,
            "output_tokens": None,
        },
    }
