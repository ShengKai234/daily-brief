import logging
from datetime import datetime, time, timedelta
from typing import Annotated, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response

from app import profiles, reports, subscriptions, topics
from app.auth import internal_auth
from app.contract import body, fields
from app.problems import Problem
from app.profiles import User

router = APIRouter(prefix="/api/internal/jobs", dependencies=[Depends(internal_auth)])
public_router = APIRouter(prefix="/api/v1/jobs")
JOB_FIELDS = "id user_id slot_id scheduled_for attempts runner"
MAX_ATTEMPTS = 2
LEASE = timedelta(minutes=35)
ERROR_CODES = {"worker_failed", "owner_only", "worker_lease_expired", "job_not_eligible"}


class JobQueue(Protocol):
    async def claim(self, worker_id: str): ...
    async def complete(self, job_id: UUID, result: dict, attempt: int): ...
    async def fail(self, job_id: UUID, retryable: bool, attempt: int): ...
    async def generate(self, user_id: UUID): ...
    async def get_user_job(self, job_id: UUID, user_id: UUID): ...


def owner_allowed(settings, job):
    return job["runner"] != "claude_subscription" or job["user_id"] == settings.owner_user_id


async def request_for(db, job):
    user = await profiles.job_profile(db, job["user_id"])
    if not user or user["status"] != "active":
        return None
    topics = await subscriptions.brief_topics(db, job["user_id"])
    if not topics:
        return None
    return {
        "job_id": job["id"],
        "user_id": job["user_id"],
        "report_date": job["scheduled_for"].astimezone(ZoneInfo(user["timezone"])).date(),
        "timezone": user["timezone"],
        "locale": user["locale"],
        "topics": topics,
    }


async def recover_leases(db, now):
    await db.execute(
        "UPDATE jobs SET status=CASE WHEN attempts<%s THEN 'queued' ELSE 'failed' END, "
        "locked_at=NULL, locked_by=NULL, error='worker_lease_expired' "
        "WHERE status='running' AND locked_at<%s",
        (MAX_ATTEMPTS, now - LEASE),
    )


async def daily_count(db, user_id, start, end):
    row = await db.one(
        "SELECT count(*) AS n FROM jobs WHERE user_id=%s AND scheduled_for>=%s "
        "AND scheduled_for<%s",
        (user_id, start, end),
    )
    return row["n"]


def daily_window(timezone, now):
    zone = ZoneInfo(timezone)
    date = now.astimezone(zone).date()
    return datetime.combine(date, time.min, zone), datetime.combine(
        date + timedelta(days=1), time.min, zone
    )


async def has_active_job(db, user_id, *, manual_only=False):
    row = await db.one(
        "SELECT EXISTS(SELECT 1 FROM jobs WHERE user_id=%s "
        "AND status IN ('queued','running') AND (%s IS FALSE OR slot_id IS NULL)) AS active",
        (user_id, manual_only),
    )
    return row["active"]


async def user_job(db, job):
    failed = job["status"] == "failed"
    return {
        **fields(job, "id status created_at"),
        "trigger": "manual" if job["slot_id"] is None else "schedule",
        "report_id": await reports.report_for_job(db, job["id"], job["user_id"])
        if job["status"] == "succeeded"
        else None,
        "error_code": (job["error"] if job["error"] in ERROR_CODES else "worker_failed")
        if failed
        else None,
    }


async def scheduled_today(db, user_id, slot_id, start, end):
    row = await db.one(
        "SELECT EXISTS(SELECT 1 FROM jobs WHERE user_id=%s AND slot_id=%s "
        "AND scheduled_for>=%s AND scheduled_for<%s) AS present",
        (user_id, slot_id, start, end),
    )
    return row["present"]


async def enqueue(db, settings, user, slot_id, due):
    allowed = owner_allowed(settings, user)
    return await db.one(
        "INSERT INTO jobs (user_id,slot_id,scheduled_for,runner,status,error) "
        "VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (user_id,slot_id,scheduled_for) DO NOTHING RETURNING id",
        (
            user["user_id"],
            slot_id,
            due,
            user["runner"],
            "queued" if allowed else "failed",
            None if allowed else "owner_only",
        ),
    )


async def has_pending(db, now):
    row = await db.one(
        "SELECT EXISTS(SELECT 1 FROM jobs WHERE status='queued' AND scheduled_for<=%s) AS pending",
        (now,),
    )
    return row["pending"]


class PostgresJobQueue:
    def __init__(self, database, settings):
        self.database = database
        self.settings = settings

    async def generate(self, user_id):
        async with self.database.transaction() as db:
            # Use the same per-user lock as the scheduler for active-job and
            # quota decisions. Check fresh settings and timezone under that lock.
            user = await profiles.lock_user(db, user_id)
            if user["status"] != "active":
                raise Problem(
                    403, "account_disabled" if user["status"] == "disabled" else "account_pending"
                )
            settings = await profiles.job_settings(db, user_id)
            if not owner_allowed(self.settings, {"user_id": user_id, "runner": settings["runner"]}):
                raise Problem(403, "owner_only")
            if await has_active_job(db, user_id):
                raise Problem(409, "job_in_progress")
            if not await subscriptions.brief_topics(db, user_id):
                raise Problem(422, "no_topics")
            now = (await db.one("SELECT now() AS current_time"))["current_time"]
            start, end = daily_window(user["timezone"], now)
            if await daily_count(db, user_id, start, end) >= settings["daily_run_limit"]:
                raise Problem(429, "daily_limit_reached")
            job = await db.one(
                "INSERT INTO jobs (user_id,slot_id,scheduled_for,runner) "
                "VALUES (%s,NULL,now(),%s) RETURNING *",
                (user_id, settings["runner"]),
            )
            result = await user_job(db, job)
        return result

    async def get_user_job(self, job_id, user_id):
        async with self.database.transaction() as db:
            job = await db.one("SELECT * FROM jobs WHERE id=%s AND user_id=%s", (job_id, user_id))
            if job is None:
                raise Problem(404, "not_found")
            return await user_job(db, job)

    async def claim(self, worker_id):
        async with self.database.transaction() as db:
            # SKIP LOCKED allows concurrent workers to claim different jobs.
            while True:
                job = await db.one(
                    "SELECT * FROM jobs WHERE status='queued' AND scheduled_for<=now() "
                    "ORDER BY scheduled_for, id FOR UPDATE SKIP LOCKED LIMIT 1"
                )
                if not job:
                    return None
                if not owner_allowed(self.settings, job):
                    await db.update(
                        "jobs", {"status": "failed", "error": "owner_only"}, "id=%s", (job["id"],)
                    )
                    continue
                request = await request_for(db, job)
                if request is None or job["attempts"] >= MAX_ATTEMPTS:
                    await db.update(
                        "jobs",
                        {"status": "failed", "error": "job_not_eligible"},
                        "id=%s",
                        (job["id"],),
                    )
                    continue
                job = await db.one(
                    "UPDATE jobs SET status='running', attempts=attempts+1, locked_at=now(), "
                    "locked_by=%s, error=NULL WHERE id=%s RETURNING *",
                    (worker_id, job["id"]),
                )
                return {"job": fields(job, JOB_FIELDS), "request": request}

    async def running(self, db, job_id, attempt):
        job = await db.one("SELECT * FROM jobs WHERE id=%s FOR UPDATE", (job_id,))
        if not job:
            raise Problem(404, "not_found")
        if job["status"] != "running" or job["attempts"] != attempt:
            raise Problem(409, "lease_lost")
        return job

    async def complete(self, job_id, result, attempt):
        forbidden = False
        async with self.database.transaction() as db:
            job = await self.running(db, job_id, attempt)
            if not owner_allowed(self.settings, job):
                await db.update(
                    "jobs", {"status": "failed", "error": "owner_only"}, "id=%s", (job_id,)
                )
                forbidden = True
            else:
                user = await profiles.job_profile(db, job["user_id"])
                if not user or user["status"] != "active":
                    raise Problem(409, "job_not_eligible")
                topic_ids = [UUID(section["topic_id"]) for section in result["sections"]]
                owned_ids = await topics.owned_ids(db, job["user_id"], topic_ids)
                validate_result(self.settings, job, owned_ids, result)
                report_id = await reports.store(db, job, result)
                usage = result["usage"]
                await db.execute(
                    "INSERT INTO usage_logs (job_id,user_id,runner,model,duration_ms,search_count,"
                    "input_tokens,output_tokens) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        job_id,
                        job["user_id"],
                        usage["runner"],
                        usage["model"],
                        usage["duration_ms"],
                        usage["search_count"],
                        usage["input_tokens"],
                        usage["output_tokens"],
                    ),
                )
                await db.update(
                    "jobs",
                    {"status": "succeeded", "locked_at": None, "locked_by": None},
                    "id=%s",
                    (job_id,),
                )
        if forbidden:
            # Raise after commit so the rejected non-owner job remains failed.
            raise Problem(403, "forbidden")
        return {"report_id": report_id}

    async def fail(self, job_id, retryable, attempt):
        async with self.database.transaction() as db:
            job = await self.running(db, job_id, attempt)
            status = "queued" if retryable and job["attempts"] < MAX_ATTEMPTS else "failed"
            if not owner_allowed(self.settings, job):
                status = "failed"
            # Free-form worker errors can contain credentials; store only a category.
            await db.update(
                "jobs",
                {"status": status, "error": "worker_failed", "locked_at": None, "locked_by": None},
                "id=%s",
                (job_id,),
            )
        return {"status": status, "attempts": job["attempts"]}


def validate_result(settings, job, owned_ids, result):
    ids = [UUID(section["topic_id"]) for section in result["sections"]]
    usage_runner = result["usage"]["runner"]
    stub = (
        settings.app_env == "local" and settings.agent_runner == "stub" and usage_runner == "stub"
    )
    if usage_runner != job["runner"] and not stub:
        raise Problem(422, "validation_error", "Runner does not match job")
    if len(ids) != len(set(ids)) or set(ids) != owned_ids:
        raise Problem(422, "validation_error", "Sections must reference distinct owned topics")
    for section in result["sections"]:
        items = section["items"]
        if [item["rank"] for item in items] != list(range(1, len(items) + 1)):
            raise Problem(422, "invalid_rank", "Ranks must be consecutive from one")


def queue(request: Request) -> JobQueue:
    return request.app.state.queue


Queue = Annotated[JobQueue, Depends(queue)]
Attempt = Annotated[int, Query(ge=1)]


@public_router.post("", status_code=202, operation_id="generateNow")
async def generate_now(request: Request, user: User, jobs: Queue):
    result = await jobs.generate(user["user_id"])
    # The row is committed before dispatch. A failed dispatch is retried by the
    # scheduler and must never turn an accepted job into a failed HTTP request.
    try:
        await request.app.state.dispatcher.dispatch()
    except Exception:
        logging.getLogger(__name__).warning("Worker dispatch failed; queued job awaits scheduler")
    return result


@public_router.get("/{job_id}", operation_id="getJob")
async def get_job(job_id: UUID, user: User, jobs: Queue):
    return await jobs.get_user_job(job_id, user["user_id"])


@router.post("/claim", operation_id="claimJob")
async def claim_job(request: Request, jobs: Queue):
    data = await body(request, "claimJob")
    claimed = await jobs.claim(data["worker_id"])
    return claimed if claimed else Response(status_code=204)


@router.post("/{job_id}/result", operation_id="submitJobResult")
async def submit_result(job_id: UUID, attempt: Attempt, request: Request, jobs: Queue):
    return await jobs.complete(job_id, await body(request, "submitJobResult"), attempt)


@router.post("/{job_id}/fail", operation_id="failJob")
async def fail_job(job_id: UUID, attempt: Attempt, request: Request, jobs: Queue):
    data = await body(request, "failJob")
    return await jobs.fail(job_id, data["retryable"], attempt)
