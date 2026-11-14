import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from conftest import OWNER, claim, queued_job, subscribe, token

from app.dispatch import LocalDispatcher
from app.jobs import daily_window
from app.scheduler import tick


@pytest.fixture
def dispatcher(env):
    dispatcher = AsyncMock()
    env["app"].state.dispatcher = dispatcher
    return dispatcher


async def job_count(env):
    async with env["database"].transaction() as db:
        return (await db.one("SELECT count(*) AS n FROM jobs"))["n"]


async def other_user(env, status="active"):
    user_id = uuid4()
    async with env["database"].transaction() as db:
        await db.execute(
            "INSERT INTO profiles (user_id,email,status) VALUES (%s,'other@example.com',%s)",
            (user_id, status),
        )
    return user_id, {
        "Authorization": "Bearer " + token(env["settings"], user_id, "other@example.com")
    }


async def test_generate_commits_before_dispatch_and_returns_only_user_fields(env, dispatcher):
    await subscribe(env)

    async def inspect_commit():
        # A separate connection can see the accepted job before dispatch starts.
        async with env["database"].transaction() as db:
            row = await db.one("SELECT * FROM jobs")
            assert row["status"] == "queued"
            assert row["slot_id"] is None
            assert row["runner"] == "claude_subscription"
            assert row["scheduled_for"] == row["created_at"]

    dispatcher.dispatch.side_effect = inspect_commit
    response = await env["client"].post("/v1/jobs")
    assert response.status_code == 202
    assert set(response.json()) == {
        "id",
        "status",
        "trigger",
        "created_at",
        "report_id",
        "error_code",
    }
    assert response.json()["status"] == "queued"
    assert response.json()["trigger"] == "manual"
    assert response.json()["report_id"] is response.json()["error_code"] is None
    dispatcher.dispatch.assert_awaited_once()
    assert (await env["client"].get("/v1/jobs/" + response.json()["id"])).json() == response.json()


async def test_stub_generate_completes_and_report_has_no_slot(env):
    await subscribe(env)
    response = await env["client"].post("/v1/jobs")
    assert response.status_code == 202
    status = (await env["client"].get("/v1/jobs/" + response.json()["id"])).json()
    assert status["status"] == "succeeded"
    assert status["trigger"] == "manual"
    assert status["error_code"] is None
    report = (await env["client"].get("/v1/reports/" + status["report_id"])).json()
    assert report["slot_id"] is None
    listing = (await env["client"].get("/v1/reports?date=" + report["report_date"])).json()[
        "reports"
    ]
    assert len(listing) == 1
    assert listing[0]["slot_id"] is listing[0]["local_time"] is None


@pytest.mark.parametrize("enabled", [None, False])
async def test_generate_requires_enabled_topics(env, dispatcher, enabled):
    if enabled is not None:
        await subscribe(env, enabled=enabled)
    response = await env["client"].post("/v1/jobs")
    assert response.status_code == 422
    assert response.json()["code"] == "no_topics"
    assert await job_count(env) == 0
    dispatcher.dispatch.assert_not_awaited()


@pytest.mark.parametrize("status", ["queued", "running"])
@pytest.mark.parametrize("scheduled", [False, True])
async def test_active_job_prevents_generate_for_any_trigger_or_date(
    env, dispatcher, status, scheduled
):
    await subscribe(env)
    slot_id = None
    if scheduled:
        slots = await env["client"].put(
            "/v1/delivery-slots", json={"slots": [{"local_time": "00:00", "enabled": True}]}
        )
        slot_id = UUID(slots.json()["slots"][0]["id"])
    job = await queued_job(
        env, status=status, slot_id=slot_id, scheduled_for=datetime.now(UTC) - timedelta(days=2)
    )
    response = await env["client"].post("/v1/jobs")
    assert response.status_code == 409
    assert response.json()["code"] == "job_in_progress"
    assert await job_count(env) == 1
    polled = (await env["client"].get(f"/v1/jobs/{job['id']}")).json()
    assert polled["trigger"] == ("schedule" if scheduled else "manual")
    dispatcher.dispatch.assert_not_awaited()


@pytest.mark.parametrize("scheduled", [False, True])
async def test_daily_limit_counts_manual_and_scheduled_jobs(env, dispatcher, scheduled):
    await subscribe(env)
    async with env["database"].transaction() as db:
        await db.execute("UPDATE agent_settings SET daily_run_limit=1 WHERE user_id=%s", (OWNER,))
        now = (await db.one("SELECT now() AS current_time"))["current_time"]
    start, _ = daily_window("Asia/Taipei", now)
    slot_id = None
    if scheduled:
        slots = await env["client"].put(
            "/v1/delivery-slots", json={"slots": [{"local_time": "00:00", "enabled": True}]}
        )
        slot_id = UUID(slots.json()["slots"][0]["id"])
    await queued_job(
        env, status="failed", scheduled_for=start + timedelta(hours=1), slot_id=slot_id
    )
    response = await env["client"].post("/v1/jobs")
    assert response.status_code == 429
    assert response.json()["code"] == "daily_limit_reached"
    assert "Retry-After" not in response.headers
    assert await job_count(env) == 1
    dispatcher.dispatch.assert_not_awaited()


async def test_zero_daily_limit_rejects_without_inserting(env, dispatcher):
    await subscribe(env)
    async with env["database"].transaction() as db:
        await db.execute("UPDATE agent_settings SET daily_run_limit=0 WHERE user_id=%s", (OWNER,))
    response = await env["client"].post("/v1/jobs")
    assert response.status_code == 429
    assert response.json()["code"] == "daily_limit_reached"
    assert await job_count(env) == 0


async def test_daily_window_uses_timezone_and_excludes_day_boundaries(env, dispatcher):
    await subscribe(env)
    await env["client"].patch("/v1/me", json={"timezone": "Pacific/Kiritimati"})
    async with env["database"].transaction() as db:
        await db.execute("UPDATE agent_settings SET daily_run_limit=1 WHERE user_id=%s", (OWNER,))
        now = (await db.one("SELECT now() AS current_time"))["current_time"]
    start, end = daily_window("Pacific/Kiritimati", now)
    # Both are outside the local day; the lower boundary is on the prior UTC date.
    assert start.astimezone(UTC).date() != start.date()
    await queued_job(env, status="failed", scheduled_for=start - timedelta(microseconds=1))
    await queued_job(env, status="succeeded", scheduled_for=end)
    response = await env["client"].post("/v1/jobs")
    assert response.status_code == 202
    async with env["database"].transaction() as db:
        job = await db.one(
            "SELECT scheduled_for FROM jobs WHERE id=%s", (UUID(response.json()["id"]),)
        )
    assert start <= job["scheduled_for"] < end


async def test_manual_run_consumes_scheduler_daily_quota(env):
    await subscribe(env)
    await env["client"].put(
        "/v1/delivery-slots", json={"slots": [{"local_time": "00:00", "enabled": True}]}
    )
    async with env["database"].transaction() as db:
        await db.execute("UPDATE agent_settings SET daily_run_limit=1 WHERE user_id=%s", (OWNER,))
    assert (await env["client"].post("/v1/jobs")).status_code == 202
    dispatcher = AsyncMock()
    assert (await tick(env["database"], env["settings"], dispatcher))["created"] == 0
    dispatcher.dispatch.assert_not_awaited()
    response = await env["client"].post("/v1/jobs")
    assert response.status_code == 429
    assert await job_count(env) == 1


async def test_non_owner_subscription_runner_is_rejected_without_a_job(env, dispatcher):
    other, headers = await other_user(env)
    response = await env["client"].post(
        "/v1/subscriptions", headers=headers, json={"title": "Other topic"}
    )
    assert response.status_code == 201
    response = await env["client"].post("/v1/jobs", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "owner_only"
    assert other != OWNER
    assert await job_count(env) == 0
    dispatcher.dispatch.assert_not_awaited()


async def test_missing_owner_fails_closed_for_manual_jobs(env, dispatcher):
    await subscribe(env)
    env["app"].state.queue.settings = replace(env["settings"], owner_user_id=None)
    response = await env["client"].post("/v1/jobs")
    assert response.status_code == 403
    assert response.json()["code"] == "owner_only"
    assert await job_count(env) == 0


async def test_api_runner_for_non_owner_is_not_blocked_by_owner_only(env, dispatcher):
    other, headers = await other_user(env)
    await env["client"].post("/v1/subscriptions", headers=headers, json={"title": "Other topic"})
    async with env["database"].transaction() as db:
        await db.execute(
            "UPDATE agent_settings SET runner='anthropic_api' WHERE user_id=%s", (other,)
        )
    response = await env["client"].post("/v1/jobs", headers=headers)
    assert response.status_code == 202
    async with env["database"].transaction() as db:
        job = await db.one("SELECT user_id,runner FROM jobs")
        assert job == {"user_id": other, "runner": "anthropic_api"}


async def test_dispatch_failure_returns_accepted_and_scheduler_retries(env, dispatcher, caplog):
    await subscribe(env)
    dispatcher.dispatch.side_effect = RuntimeError("private remote failure details")
    response = await env["client"].post("/v1/jobs")
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert "private remote failure details" not in caplog.text
    assert "queued job awaits scheduler" in caplog.text
    assert (await env["client"].get("/v1/jobs/" + response.json()["id"])).json()[
        "status"
    ] == "queued"
    local = LocalDispatcher(env["settings"], env["app"].state.queue)
    assert await tick(env["database"], env["settings"], local) == {"created": 0, "dispatched": True}
    assert (await env["client"].get("/v1/jobs/" + response.json()["id"])).json()[
        "status"
    ] == "succeeded"
    assert await job_count(env) == 1


async def test_parallel_manual_requests_create_one_active_job(env, dispatcher):
    await subscribe(env)
    responses = await asyncio.gather(*(env["client"].post("/v1/jobs") for _ in range(6)))
    assert sorted(response.status_code for response in responses) == [202, 409, 409, 409, 409, 409]
    assert await job_count(env) == 1
    dispatcher.dispatch.assert_awaited_once()


@pytest.mark.parametrize("limit", [1, 3])
async def test_manual_and_scheduler_share_atomic_daily_quota(env, dispatcher, limit):
    await subscribe(env)
    await env["client"].put(
        "/v1/delivery-slots", json={"slots": [{"local_time": "00:00", "enabled": True}]}
    )
    async with env["database"].transaction() as db:
        await db.execute(
            "UPDATE agent_settings SET daily_run_limit=%s WHERE user_id=%s", (limit, OWNER)
        )
    response, _ = await asyncio.gather(
        env["client"].post("/v1/jobs"), tick(env["database"], env["settings"], dispatcher)
    )
    assert response.status_code in {202, 409}
    assert await job_count(env) == 1


async def test_scheduler_defers_new_slot_jobs_until_manual_job_finishes(env, dispatcher):
    await subscribe(env)
    await env["client"].put(
        "/v1/delivery-slots", json={"slots": [{"local_time": "00:00", "enabled": True}]}
    )
    assert (await env["client"].post("/v1/jobs")).status_code == 202
    assert (await tick(env["database"], env["settings"], dispatcher))["created"] == 0
    assert await job_count(env) == 1
    await LocalDispatcher(env["settings"], env["app"].state.queue).dispatch()
    assert (await tick(env["database"], env["settings"], dispatcher))["created"] == 1
    assert await job_count(env) == 2


async def test_polling_shows_running_then_terminal_state(env, dispatcher):
    await subscribe(env)
    accepted = (await env["client"].post("/v1/jobs")).json()
    claimed = await claim(env)
    path = "/v1/jobs/" + accepted["id"]
    assert (await env["client"].get(path)).json()["status"] == "running"
    response = await env["client"].post(
        f"/internal/jobs/{accepted['id']}/fail",
        headers=env["internal"],
        params={"attempt": claimed["job"]["attempts"]},
        json={"error": "Worker diagnostic text", "retryable": False},
    )
    assert response.status_code == 200
    polled = (await env["client"].get(path)).json()
    assert polled["status"] == "failed"
    assert polled["error_code"] == "worker_failed"
    assert polled["report_id"] is None


@pytest.mark.parametrize(
    "error, expected",
    [
        ("owner_only", "owner_only"),
        ("worker_lease_expired", "worker_lease_expired"),
        ("job_not_eligible", "job_not_eligible"),
        (None, "worker_failed"),
        ("unknown_code", "worker_failed"),
        ("Worker diagnostic text", "worker_failed"),
        ("worker_failed\nextra", "worker_failed"),
    ],
)
async def test_error_code_uses_only_known_codes(env, dispatcher, error, expected):
    job = await queued_job(env, status="failed", error=error)
    response = await env["client"].get(f"/v1/jobs/{job['id']}")
    assert response.status_code == 200
    assert response.json()["error_code"] == expected


async def test_active_job_does_not_expose_previous_attempt_error(env, dispatcher):
    job = await queued_job(env, error="worker_failed")
    response = await env["client"].get(f"/v1/jobs/{job['id']}")
    assert response.json()["error_code"] is None


async def test_polling_hides_other_users_jobs_and_unknown_ids(env, dispatcher):
    job = await queued_job(env)
    _, headers = await other_user(env)
    assert (await env["client"].get(f"/v1/jobs/{job['id']}", headers=headers)).status_code == 404
    assert (await env["client"].get(f"/v1/jobs/{uuid4()}")).status_code == 404
    assert (await env["client"].get("/v1/jobs/invalid")).status_code == 400


@pytest.mark.parametrize("status", ["pending", "disabled"])
async def test_manual_routes_enforce_account_gate(env, dispatcher, status):
    _, headers = await other_user(env, status=status)
    for method, path in [("POST", "/v1/jobs"), ("GET", f"/v1/jobs/{uuid4()}")]:
        response = await env["client"].request(method, path, headers=headers)
        assert response.status_code == 403
        assert response.json()["code"] == "account_" + status
    assert await job_count(env) == 0


@pytest.mark.parametrize("credential", ["none", "internal"])
async def test_manual_routes_require_user_jwt(env, dispatcher, credential):
    headers = {} if credential == "none" else env["internal"]
    assert (await env["client"].post("/v1/jobs", headers=headers)).status_code == 401
    assert (await env["client"].get(f"/v1/jobs/{uuid4()}", headers=headers)).status_code == 401
