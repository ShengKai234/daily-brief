"""EventBridge handler and reusable async tick. Scheduling has no HTTP route."""

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app import jobs, profiles, slots, subscriptions
from app.config import Settings
from app.database import Database
from app.dispatch import configured_dispatcher
from app.jobs import PostgresJobQueue


async def tick(database, settings, dispatcher, now=None):
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Scheduler time must be timezone aware")
    created = 0
    async with database.transaction() as db:
        await jobs.recover_leases(db, now)
        for user in await profiles.scheduler_users(db):
            if await jobs.has_active_job(db, user["user_id"], manual_only=True):
                continue
            zone = ZoneInfo(user["timezone"])
            local = now.astimezone(zone)
            start, end = jobs.daily_window(user["timezone"], now)
            count = await jobs.daily_count(db, user["user_id"], start, end)
            if not await subscriptions.brief_topics(db, user["user_id"]):
                continue
            for slot in await slots.enabled_slots(db, user["user_id"]):
                due = datetime.combine(local.date(), slot["local_time"], zone)
                if due > local or count >= user["daily_run_limit"]:
                    continue
                if await jobs.scheduled_today(db, user["user_id"], slot["id"], start, end):
                    continue
                # Skip nonexistent DST wall times; fold=0 runs repeated times only once.
                if due.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != due.replace(
                    tzinfo=None
                ):
                    continue
                row = await jobs.enqueue(db, settings, user, slot["id"], due)
                if row:
                    count += 1
                    created += 1
        pending = await jobs.has_pending(db, now)
    # Commit before dispatch: failed dispatch remains queued and is retried next tick,
    # including when no NEW jobs are inserted. Worker claim handles duplicate dispatches.
    if pending:
        await dispatcher.dispatch()
    return {"created": created, "dispatched": pending}


async def configured_tick():
    settings = Settings.from_env()
    database = Database(settings)
    queue = PostgresJobQueue(database, settings)
    dispatcher = configured_dispatcher(settings, queue)
    return await tick(database, settings, dispatcher)


def handler(event, context):
    return asyncio.run(configured_tick())


if __name__ == "__main__":
    asyncio.run(configured_tick())
