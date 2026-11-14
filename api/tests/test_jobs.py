import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from conftest import claim, queued_job, result_for, subscribe, token
from psycopg import sql

from app import reports
from app.problems import Problem


async def test_report_pipeline_and_persistent_marks(env):
    await subscribe(env)
    job = await queued_job(env)
    claimed = await claim(env)
    assert claimed["job"]["id"] == str(job["id"])
    assert claimed["job"]["attempts"] == 1
    assert (
        await env["client"].post(
            "/internal/jobs/claim", headers=env["internal"], json={"worker_id": "second"}
        )
    ).status_code == 204
    response = await env["client"].post(
        f"/internal/jobs/{job['id']}/result",
        headers=env["internal"],
        json=result_for(claimed),
        params={"attempt": claimed["job"]["attempts"]},
    )
    assert response.status_code == 200
    report_id = response.json()["report_id"]
    client = env["client"]
    report = (await client.get(f"/v1/reports/{report_id}")).json()
    item = report["sections"][0]["items"][0]
    assert item["mark"] is None
    listing = (await client.get("/v1/reports?date=" + claimed["request"]["report_date"])).json()[
        "reports"
    ]
    assert listing[0]["unread_count"] == 1
    marked = await client.put(
        f"/v1/items/{item['id']}/mark", json={"read": True, "note": "<script>plain text</script>"}
    )
    assert marked.json()["read_at"] is not None
    assert (
        await client.put(f"/v1/items/{item['id']}/mark", json={"read": True, "note": "edited"})
    ).json()["read_at"] == marked.json()["read_at"]
    # Each HTTP call creates fresh DB connections; marks are not process state.
    assert (await client.get(f"/v1/reports/{report_id}")).json()["sections"][0]["items"][0]["mark"][
        "note"
    ] == "edited"
    assert (await client.get("/v1/reports")).json()["reports"][0]["unread_count"] == 0
    unmarked = await client.put(f"/v1/items/{item['id']}/mark", json={"read": False, "note": None})
    assert unmarked.json()["read_at"] is None
    # Replay cannot create a second report or usage record.
    assert (
        await client.post(
            f"/internal/jobs/{job['id']}/result",
            headers=env["internal"],
            json=result_for(claimed),
            params={"attempt": claimed["job"]["attempts"]},
        )
    ).json()["code"] == "lease_lost"
    async with env["database"].transaction() as db:
        assert (await db.one("SELECT status FROM jobs WHERE id=%s", (job["id"],)))[
            "status"
        ] == "succeeded"
        assert (await db.one("SELECT count(*) AS n FROM usage_logs"))["n"] == 1
    other = uuid4()
    async with env["database"].transaction() as db:
        await db.execute("INSERT INTO allowed_emails (email) VALUES ('other@example.com')")
    headers = {"Authorization": "Bearer " + token(env["settings"], other, "other@example.com")}
    assert (await client.get("/v1/reports", headers=headers)).json()["reports"] == []
    assert (await client.get(f"/v1/reports/{report_id}", headers=headers)).status_code == 404
    assert (
        await client.put(
            f"/v1/items/{item['id']}/mark", headers=headers, json={"read": True, "note": None}
        )
    ).status_code == 404


@pytest.mark.parametrize(
    "mutation",
    [
        "url",
        "empty_host",
        "userinfo_url",
        "length",
        "extra",
        "date",
        "locale",
        "topics",
        "duplicate",
        "ranks",
        "rank_zero",
        "rank_noninteger",
        "count",
        "runner",
        "empty_ok",
    ],
)
async def test_invalid_result_is_blocked_without_writes(env, mutation):
    await subscribe(env, item_count=1)
    job = await queued_job(env)
    claimed = await claim(env)
    result = result_for(claimed)
    section = result["sections"][0]
    item = section["items"][0]
    if mutation == "url":
        item["source_url"] = "javascript:alert(1)"
    if mutation == "empty_host":
        item["source_url"] = "https:///path"
    if mutation == "userinfo_url":
        item["source_url"] = "https://user@example.com/news"
    if mutation == "length":
        item["summary"] = "x" * 601
    if mutation == "extra":
        item["untrusted"] = True
    if mutation == "date":
        result["report_date"] = "not-a-date"
    if mutation == "locale":
        result["locale"] = "x" * 36
    if mutation == "topics":
        section["topic_id"] = str(uuid4())
    if mutation == "duplicate":
        result["sections"].append(deepcopy(section))
    if mutation == "ranks":
        item["rank"] = 2
    if mutation == "rank_zero":
        item["rank"] = 0
    if mutation == "rank_noninteger":
        item["rank"] = "1"
    if mutation == "count":
        section["items"] = [{**item, "rank": rank} for rank in range(1, 12)]
    if mutation == "runner":
        result["usage"]["runner"] = "anthropic_api"
    if mutation == "empty_ok":
        section["items"] = []
    response = await env["client"].post(
        f"/internal/jobs/{job['id']}/result",
        headers=env["internal"],
        json=result,
        params={"attempt": claimed["job"]["attempts"]},
    )
    assert response.status_code == 422
    if mutation in {"ranks", "rank_zero", "rank_noninteger"}:
        assert response.json()["code"] == "invalid_rank"
    async with env["database"].transaction() as db:
        assert (await db.one("SELECT count(*) AS n FROM reports"))["n"] == 0
        assert (await db.one("SELECT status FROM jobs WHERE id=%s", (job["id"],)))[
            "status"
        ] == "running"


async def test_storage_failure_rolls_back_entire_result(env, monkeypatch):
    await subscribe(env)
    job = await queued_job(env)
    claimed = await claim(env)
    original = reports.store

    async def fail_after_writes(db, job, result):
        await original(db, job, result)
        raise Problem(422, "validation_error")

    monkeypatch.setattr(reports, "store", fail_after_writes)
    response = await env["client"].post(
        f"/internal/jobs/{job['id']}/result",
        headers=env["internal"],
        json=result_for(claimed),
        params={"attempt": claimed["job"]["attempts"]},
    )
    assert response.status_code == 422
    async with env["database"].transaction() as db:
        for table in ["reports", "report_sections", "report_items", "usage_logs"]:
            query = sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))
            assert (await db.one(query))["n"] == 0
        assert (await db.one("SELECT status FROM jobs WHERE id=%s", (job["id"],)))[
            "status"
        ] == "running"


async def test_parallel_claims_never_duplicate(env):
    await subscribe(env)
    jobs = [await queued_job(env) for _ in range(4)]

    async def take(index):
        return await env["client"].post(
            "/internal/jobs/claim", headers=env["internal"], json={"worker_id": str(index)}
        )

    responses = await asyncio.gather(*(take(i) for i in range(6)))
    claimed = [
        response.json()["job"]["id"] for response in responses if response.status_code == 200
    ]
    assert len(claimed) == len(set(claimed)) == len(jobs)


async def test_retry_limit(env):
    await subscribe(env)
    job = await queued_job(env)
    first = await claim(env)
    path = f"/internal/jobs/{job['id']}/fail"
    response = await env["client"].post(
        path,
        headers=env["internal"],
        json={"error": "Example failure", "retryable": True},
        params={"attempt": first["job"]["attempts"]},
    )
    assert response.json() == {"status": "queued", "attempts": 1}
    second = await claim(env)
    assert second["job"]["attempts"] == 2
    response = await env["client"].post(
        path,
        headers=env["internal"],
        json={"error": "Example failure", "retryable": True},
        params={"attempt": second["job"]["attempts"]},
    )
    assert response.json() == {"status": "failed", "attempts": 2}
    assert (
        await env["client"].post(
            path,
            headers=env["internal"],
            json={"error": "Example failure", "retryable": False},
            params={"attempt": second["job"]["attempts"]},
        )
    ).status_code == 409


async def test_owner_only_at_claim_and_result(env):
    await subscribe(env)
    other = uuid4()
    async with env["database"].transaction() as db:
        await db.execute(
            "INSERT INTO profiles (user_id,email,status) VALUES (%s,'other@example.com','active')",
            (other,),
        )
    job = await queued_job(env, user_id=other)
    response = await env["client"].post(
        "/internal/jobs/claim", headers=env["internal"], json={"worker_id": "worker"}
    )
    assert response.status_code == 204
    async with env["database"].transaction() as db:
        assert (await db.one("SELECT status,error FROM jobs WHERE id=%s", (job["id"],))) == {
            "status": "failed",
            "error": "owner_only",
        }
    owner_job = await queued_job(env)
    claimed = await claim(env)
    # An already-running job is checked again, guarding old/incorrectly inserted jobs.
    async with env["database"].transaction() as db:
        await db.execute("UPDATE jobs SET user_id=%s WHERE id=%s", (other, owner_job["id"]))
    response = await env["client"].post(
        f"/internal/jobs/{owner_job['id']}/result",
        headers=env["internal"],
        json=result_for(claimed),
        params={"attempt": claimed["job"]["attempts"]},
    )
    assert response.status_code == 403
    async with env["database"].transaction() as db:
        assert (await db.one("SELECT status FROM jobs WHERE id=%s", (owner_job["id"],)))[
            "status"
        ] == "failed"
        assert (await db.one("SELECT count(*) AS n FROM reports"))["n"] == 0


async def test_insufficient_news_can_have_zero_items(env):
    await subscribe(env)
    job = await queued_job(env)
    claimed = await claim(env)
    result = result_for(claimed)
    result["sections"][0].update(status="insufficient_news", note="No fresh sources.", items=[])
    assert (
        await env["client"].post(
            f"/internal/jobs/{job['id']}/result",
            headers=env["internal"],
            json=result,
            params={"attempt": claimed["job"]["attempts"]},
        )
    ).status_code == 200
