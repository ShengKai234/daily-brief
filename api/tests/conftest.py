import re
import secrets
import shutil
import subprocess
import time
from http.cookiejar import DefaultCookiePolicy
from pathlib import Path
from uuid import UUID, uuid4

import httpx
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
        with psycopg.connect(url, autocommit=True) as connection:
            # Cluster-wide roles; the migration only creates ones that are missing.
            connection.execute("CREATE ROLE brief_migrator NOLOGIN NOBYPASSRLS")
            connection.execute("CREATE ROLE brief_runtime LOGIN NOBYPASSRLS")
            connection.execute("CREATE ROLE brief_admin LOGIN NOBYPASSRLS")
        yield url
    finally:
        subprocess.run([docker, "stop", container], check=True, capture_output=True)  # noqa: S603


def role_url(url, role):
    return url.replace("postgres@", role + "@", 1)


def super_url(url):
    return url


def make_settings(superuser_url, **overrides):
    values = {
        "app_env": "local",
        "database_url": role_url(superuser_url, "brief_runtime"),
        "admin_database_url": role_url(superuser_url, "brief_admin"),
        "internal_service_token": secrets.token_urlsafe(32),
        "owner_user_id": OWNER,
        "webauthn_rp_id": "localhost",
        "webauthn_origins": ("http://localhost:5173",),
        "rate_limit_key": secrets.token_urlsafe(32),
        "compose_project_name": "test",
        **overrides,
    }
    return Settings(**values)


@pytest.fixture
def database_url(postgres, monkeypatch):
    name = "api_test_" + uuid4().hex
    with psycopg.connect(postgres, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name))
        )
    url = postgres.rsplit("/", 1)[0] + "/" + name
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
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


DEFAULT = object()


class Client:
    """A browser: keeps its own cookies and asserts every response against the contract."""

    def __init__(self, raw, settings, cookies=None):
        self.raw = raw
        self.settings = settings
        self.cookies = dict(cookies or {})
        self.operations = set()

    def remember(self, response):
        for header in response.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            value, _, attrs = rest.partition(";")
            if "max-age=0" in attrs.lower().replace(" ", ""):
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value

    async def request(self, method, path, *, headers=None, origin=DEFAULT, contract=True, **kwargs):
        headers = dict(headers or {})
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and origin is not None:
            headers["Origin"] = self.settings.webauthn_origins[0] if origin is DEFAULT else origin
        # The worker is not a browser: it never carries cookies to /internal.
        if self.cookies and "Cookie" not in headers and not path.startswith("/internal"):
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        response = await self.raw.request(method, "/api" + path, headers=headers, **kwargs)
        self.remember(response)
        assert response.headers.get("cache-control") == "no-store", (method, path)
        clean_path = path.split("?", 1)[0]
        for template, methods in SPEC["paths"].items():
            if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), clean_path):
                operation = methods.get(method.lower())
                if operation is None:
                    break
                self.operations.add(operation["operationId"])
                if not contract:
                    return response
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
        if not contract:
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
async def env(database_url, monkeypatch):
    # Tests share one client address; the rate-limit scenarios set the real limit explicitly.
    monkeypatch.setattr("app.security.RATE_LIMIT", 100_000)
    settings = make_settings(database_url)
    app = create_app(settings)
    database = Database(settings)
    superdb = Database(settings, super_url(database_url))
    admindb = Database(settings, settings.admin_database_url)
    async with superdb.transaction() as db:
        await db.execute(
            "INSERT INTO profiles (user_id, display_name) VALUES (%s, 'Owner')", (OWNER,)
        )
        await db.execute("INSERT INTO agent_settings (user_id) VALUES (%s)", (OWNER,))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as raw:
        # Each Client keeps its own cookies; the shared transport must never store any.
        raw.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))

        async def as_user(user_id):
            """A client signed in with a local dev session (no passkey ceremony)."""
            cookie = await dev_session(superdb, settings, user_id)
            return Client(raw, settings, {settings.session_cookie: cookie})

        def browser():
            return Client(raw, settings)

        client = await as_user(OWNER)
        assert (await client.get("/v1/me")).status_code == 200
        yield {
            "settings": settings,
            "app": app,
            "database": database,
            "super": superdb,
            "admin": admindb,
            "client": client,
            "as_user": as_user,
            "browser": browser,
            "raw": raw,
            "internal": {"X-Internal-Token": settings.internal_service_token},
        }


async def dev_session(database, settings, user_id):
    from app import clock
    from app.authdb import create_session, lock_sessions

    async with database.transaction() as db:
        await lock_sessions(db, user_id)
        value, _id, _age = await create_session(db, user_id, None, clock.now(), kind="dev")
    return value


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
