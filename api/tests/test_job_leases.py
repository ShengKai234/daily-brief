import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import claim, queued_job, result_for, subscribe

from app.jobs import recover_leases


def payload(action, claimed):
    return (
        result_for(claimed)
        if action == "result"
        else {
            "error": "Example worker failure",
            "retryable": False,
        }
    )


async def snapshot(env, job_id):
    async with env["database"].transaction() as db:
        return {
            "job": await db.one("SELECT * FROM jobs WHERE id=%s", (job_id,)),
            "reports": (await db.one("SELECT count(*) AS n FROM reports"))["n"],
            "usage": (await db.one("SELECT count(*) AS n FROM usage_logs"))["n"],
        }


@pytest.mark.parametrize("action", ["result", "fail"])
@pytest.mark.parametrize("attempt", [None, "0", "-1", "1.5", "invalid"])
async def test_attempt_is_required_positive_integer(env, action, attempt):
    await subscribe(env)
    job = await queued_job(env)
    claimed = await claim(env)
    before = await snapshot(env, job["id"])
    response = await env["client"].post(
        f"/internal/jobs/{job['id']}/{action}",
        headers=env["internal"],
        json=payload(action, claimed),
        params={} if attempt is None else {"attempt": attempt},
    )
    assert response.status_code == 422
    assert await snapshot(env, job["id"]) == before


@pytest.mark.parametrize("action", ["result", "fail"])
async def test_stale_worker_cannot_write_after_requeue_or_reclaim(env, action):
    await subscribe(env)
    job = await queued_job(env)
    first = await claim(env)
    async with env["database"].transaction() as db:
        await recover_leases(db, datetime.now(UTC) + timedelta(minutes=36))
    queued = await snapshot(env, job["id"])
    assert queued["job"]["status"] == "queued"
    path = f"/internal/jobs/{job['id']}/{action}"
    response = await env["client"].post(
        path,
        headers=env["internal"],
        json=payload(action, first),
        params={"attempt": first["job"]["attempts"]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "lease_lost"
    assert await snapshot(env, job["id"]) == queued

    second = await claim(env)
    assert second["job"]["attempts"] == 2
    running = await snapshot(env, job["id"])
    response = await env["client"].post(
        path,
        headers=env["internal"],
        json=payload(action, first),
        params={"attempt": first["job"]["attempts"]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "lease_lost"
    assert await snapshot(env, job["id"]) == running

    response = await env["client"].post(
        path,
        headers=env["internal"],
        json=payload(action, second),
        params={"attempt": second["job"]["attempts"]},
    )
    assert response.status_code == 200
    finished = await snapshot(env, job["id"])
    assert finished["job"]["status"] == ("succeeded" if action == "result" else "failed")
    assert finished["reports"] == finished["usage"] == (1 if action == "result" else 0)
    # Neither endpoint can mutate a terminal job, even with the latest attempt.
    for late_action in ["result", "fail"]:
        response = await env["client"].post(
            f"/internal/jobs/{job['id']}/{late_action}",
            headers=env["internal"],
            json=payload(late_action, second),
            params={"attempt": second["job"]["attempts"]},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "lease_lost"
        assert await snapshot(env, job["id"]) == finished


async def test_concurrent_result_and_failure_have_one_winner(env):
    await subscribe(env)
    job = await queued_job(env)
    claimed = await claim(env)
    responses = await asyncio.gather(
        *(
            env["client"].post(
                f"/internal/jobs/{job['id']}/{action}",
                headers=env["internal"],
                json=payload(action, claimed),
                params={"attempt": claimed["job"]["attempts"]},
            )
            for action in ["result", "fail"]
        )
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json()["code"] == "lease_lost"
    state = await snapshot(env, job["id"])
    expected = 1 if state["job"]["status"] == "succeeded" else 0
    assert state["reports"] == state["usage"] == expected


@pytest.mark.parametrize("action", ["result", "fail"])
async def test_unknown_job_remains_not_found(env, action):
    await subscribe(env)
    await queued_job(env)
    claimed = await claim(env)
    response = await env["client"].post(
        f"/internal/jobs/{uuid4()}/{action}",
        headers=env["internal"],
        json=payload(action, claimed),
        params={"attempt": 1},
    )
    assert response.status_code == 404
