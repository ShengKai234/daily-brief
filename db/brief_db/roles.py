"""Local-only bootstrap of the three backend login roles (docs/auth-passkey-design.md 4.4).

Runs as the local container superuser (POSTGRES_USER), only when APP_ENV=local and the three
role URLs are configured. Passwords come from those URLs; they are never printed or stored in
the repository. Production roles are created by Kai, outside this repository.
"""

import os

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from brief_db.config import role_url

ROLE_ENV = (
    ("brief_migrator", "MIGRATION_DATABASE_URL"),
    ("brief_runtime", "DATABASE_URL"),
    ("brief_admin", "ADMIN_DATABASE_URL"),
)


def configured() -> bool:
    return os.environ.get("APP_ENV") == "local" and all(os.environ.get(env) for _, env in ROLE_ENV)


async def bootstrap_local_roles() -> None:
    if os.environ.get("APP_ENV") != "local":
        raise ValueError("Role bootstrap only runs with APP_ENV=local")
    superuser = os.environ.get("POSTGRES_USER")
    password = os.environ.get("POSTGRES_PASSWORD")
    if not superuser or not password:
        raise ValueError("POSTGRES_USER and POSTGRES_PASSWORD are required for local roles")
    roles = {}
    for role, env in ROLE_ENV:
        url = role_url(env)
        if url.username != role or not url.password:
            raise ValueError(f"{env} must use role {role} with a password")
        roles[role] = url
    base = roles["brief_migrator"]
    admin_url = URL.create(
        "postgresql+asyncpg",
        username=superuser,
        password=password,
        host=base.host,
        port=base.port,
        database=base.database,
    )
    engine = create_async_engine(admin_url, poolclass=NullPool, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            for role, url in roles.items():
                exists = await connection.scalar(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
                )
                clause = await connection.scalar(
                    text(
                        "SELECT format('ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %L', "
                        "CAST(:r AS text), CAST(:p AS text))"
                    ),
                    {"r": role, "p": url.password},
                )
                await connection.exec_driver_sql(("ALTER " if exists else "CREATE ") + clause)
            await connection.exec_driver_sql(
                "DO $$ BEGIN EXECUTE format('GRANT CONNECT, CREATE ON DATABASE %I TO brief_migrator', "
                "current_database()); END $$"
            )
            await connection.exec_driver_sql("GRANT ALL ON SCHEMA public TO brief_migrator")
            # Databases created before the cutover were built by the container superuser.
            await connection.exec_driver_sql("""DO $$ DECLARE r record; BEGIN
                FOR r IN SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace
                         AND c.relkind IN ('r', 'p') AND pg_get_userbyid(c.relowner) <> 'brief_migrator'
                LOOP EXECUTE format('ALTER TABLE public.%I OWNER TO brief_migrator', r.relname); END LOOP;
                IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'set_updated_at'
                           AND pronamespace = 'public'::regnamespace) THEN
                    ALTER FUNCTION public.set_updated_at() OWNER TO brief_migrator;
                END IF;
            END $$""")
    finally:
        await engine.dispose()
