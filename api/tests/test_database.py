async def test_driver_never_prepares_statements(env):
    async with env["database"].transaction() as db:
        assert db.connection.prepare_threshold is None
        for value in range(10):
            assert (await db.one("SELECT %s::integer AS n", (value,)))["n"] == value
        assert await db.all("SELECT name FROM pg_prepared_statements") == []
