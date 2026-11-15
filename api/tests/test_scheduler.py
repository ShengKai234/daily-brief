import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from conftest import OWNER, queued_job, subscribe

from app.dispatch import LocalDispatcher
from app.scheduler import tick


async def set_slots(env, times):
    response = await env["client"].put(
        "/v1/delivery-slots",
        json={"slots": [{"local_time": value, "enabled": True} for value in times]},
    )
    assert response.status_code == 200
    return response.json()["slots"]


async def test_parallel_ticks_dedupe_and_limit(env):
    await subscribe(env)
    await set_slots(env, ["07:00", "12:00", "18:00"])
    async with env["database"].transaction() as db:
        await db.execute("UPDATE agent_settings SET daily_run_limit=2 WHERE user_id=%s", (OWNER,))
    now = datetime(2026, 11, 12, 12, tzinfo=UTC)  # 20:00 Taipei
    dispatcher = AsyncMock()
    await asyncio.gather(
        *(tick(env["database"], env["settings"], dispatcher, now) for _ in range(3))
    )
    async with env["database"].transaction() as db:
        rows = await db.all("SELECT * FROM jobs ORDER BY scheduled_for")
        assert len(rows) == 2
        assert all(row["slot_id"] is not None for row in rows)
        assert rows[0]["scheduled_for"].hour == 23  # previous UTC day
        assert rows[1]["scheduled_for"].hour == 4
    assert (await tick(env["database"], env["settings"], dispatcher, now))["created"] == 0


async def test_dispatch_failure_retries_existing_queue(env):
    await subscribe(env)
    await set_slots(env, ["00:00"])
    dispatcher = AsyncMock()
    dispatcher.dispatch.side_effect = RuntimeError("Example dispatch failure")
    with pytest.raises(RuntimeError):
        await tick(env["database"], env["settings"], dispatcher)
    async with env["database"].transaction() as db:
        assert (await db.one("SELECT count(*) AS n FROM jobs WHERE status='queued'"))["n"] == 1
    dispatcher.dispatch.side_effect = None
    result = await tick(env["database"], env["settings"], dispatcher)
    assert result == {"created": 0, "dispatched": True}


async def test_slot_time_change_does_not_schedule_twice_in_a_day(env):
    await subscribe(env)
    await set_slots(env, ["00:00"])
    now = datetime(2026, 11, 12, 12, tzinfo=UTC)
    dispatcher = AsyncMock()
    assert (await tick(env["database"], env["settings"], dispatcher, now))["created"] == 1
    await set_slots(env, ["01:00"])
    assert (await tick(env["database"], env["settings"], dispatcher, now))["created"] == 0
    result = await tick(env["database"], env["settings"], dispatcher, now + timedelta(days=1))
    assert result["created"] == 1


async def test_local_stub_pipeline(env):
    await subscribe(env)
    await set_slots(env, ["00:00"])
    dispatcher = LocalDispatcher(env["settings"], env["app"].state.queue)
    await tick(env["database"], env["settings"], dispatcher)
    report_list = (await env["client"].get("/v1/reports")).json()["reports"]
    assert len(report_list) == 1
    assert report_list[0]["item_count"] == 1
    async with env["database"].transaction() as db:
        assert (await db.one("SELECT status FROM jobs"))["status"] == "succeeded"


async def test_scheduler_rejects_non_owner_and_does_not_dispatch(env):
    await subscribe(env)
    await set_slots(env, ["00:00"])
    async with env["database"].transaction() as db:
        other = uuid4()
        await db.execute(
            "INSERT INTO profiles (user_id,email,status) VALUES (%s,'other@example.com','active')",
            (other,),
        )
        await db.execute("UPDATE agent_settings SET user_id=%s WHERE user_id=%s", (other, OWNER))
        await db.execute("UPDATE delivery_slots SET user_id=%s WHERE user_id=%s", (other, OWNER))
        await db.execute("UPDATE topics SET owner_id=%s WHERE owner_id=%s", (other, OWNER))
        await db.execute("UPDATE subscriptions SET user_id=%s WHERE user_id=%s", (other, OWNER))
    dispatcher = AsyncMock()
    await tick(env["database"], env["settings"], dispatcher)
    dispatcher.dispatch.assert_not_called()
    async with env["database"].transaction() as db:
        assert (await db.one("SELECT status,error FROM jobs")) == {
            "status": "failed",
            "error": "owner_only",
        }


async def test_stale_lease_requeues_then_exhausts(env):
    await subscribe(env)
    old = datetime.now(UTC) - timedelta(hours=1)
    job = await queued_job(env, status="running", attempts=1, locked_at=old)
    await tick(env["database"], env["settings"], AsyncMock())
    async with env["database"].transaction() as db:
        assert (await db.one("SELECT status FROM jobs WHERE id=%s", (job["id"],)))[
            "status"
        ] == "queued"
        await db.execute(
            "UPDATE jobs SET status='running', attempts=2, locked_at=%s WHERE id=%s",
            (old, job["id"]),
        )
    await tick(env["database"], env["settings"], AsyncMock())
    async with env["database"].transaction() as db:
        assert (await db.one("SELECT status FROM jobs WHERE id=%s", (job["id"],)))[
            "status"
        ] == "failed"


@pytest.mark.parametrize(
    "date, expected",
    [(datetime(2026, 3, 8, 8, tzinfo=UTC), 0), (datetime(2026, 11, 1, 8, tzinfo=UTC), 1)],
)
async def test_dst_nonexistent_and_repeated_slots(env, date, expected):
    await subscribe(env)
    await env["client"].patch("/v1/me", json={"timezone": "America/New_York"})
    await set_slots(env, ["02:30" if date.month == 3 else "01:30"])
    for _ in range(2):
        await tick(env["database"], env["settings"], AsyncMock(), date)
    async with env["database"].transaction() as db:
        assert (await db.one("SELECT count(*) AS n FROM jobs"))["n"] == expected
