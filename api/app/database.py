from contextlib import asynccontextmanager

from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row


class Session:
    def __init__(self, connection):
        self.connection = connection

    async def all(self, query, params=()):
        return await (await self.connection.execute(query, params)).fetchall()

    async def one(self, query, params=()):
        return await (await self.connection.execute(query, params)).fetchone()

    async def execute(self, query, params=()):
        return await self.connection.execute(query, params)

    async def update(self, table, values, where, params):
        # Identifiers originate only in module code, never in request values.
        query = sql.SQL("UPDATE {} SET {} WHERE {} RETURNING *").format(
            sql.Identifier(table),
            sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(key)) for key in values),
            sql.SQL(where),
        )
        return await self.one(query, (*values.values(), *params))


class Database:
    def __init__(self, settings):
        self.settings = settings

    @asynccontextmanager
    async def transaction(self):
        url = self.settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        url = url.replace("postgresql+psycopg://", "postgresql://", 1)
        if not url.startswith("postgresql://"):
            raise ValueError("DATABASE_URL must be a PostgreSQL URL")
        options = {} if self.settings.app_env == "local" else {"sslmode": "require"}
        # No process-level pool or prepared statements: safe for transaction pooling
        # and Lambda freeze/thaw. Connections close at the end of every transaction.
        async with await AsyncConnection.connect(
            url, row_factory=dict_row, prepare_threshold=None, connect_timeout=10, **options
        ) as connection:
            yield Session(connection)
