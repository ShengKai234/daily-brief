from uuid import uuid4

import pytest
from conftest import OWNER, claim, queued_job, result_for, subscribe


@pytest.mark.parametrize(
    "edit", ["unsubscribe", "disable", "reduce_count", "add_subscription", "profile"]
)
async def test_settings_edits_during_run_do_not_reject_valid_output(env, edit):
    subscription = await subscribe(env, item_count=3)
    job = await queued_job(env)
    claimed = await claim(env)
    result = result_for(claimed)
    item = result["sections"][0]["items"][0]
    result["sections"][0]["items"] = [{**item, "rank": rank} for rank in range(1, 4)]
    client = env["client"]
    if edit == "unsubscribe":
        assert (await client.delete(f"/v1/subscriptions/{subscription['id']}")).status_code == 204
    elif edit == "disable":
        assert (
            await client.patch(f"/v1/subscriptions/{subscription['id']}", json={"enabled": False})
        ).status_code == 200
    elif edit == "reduce_count":
        assert (
            await client.patch(f"/v1/subscriptions/{subscription['id']}", json={"item_count": 1})
        ).status_code == 200
    elif edit == "add_subscription":
        await subscribe(env, "Another topic")
    else:
        assert (
            await client.patch("/v1/me", json={"timezone": "Etc/GMT+12", "locale": "en"})
        ).status_code == 200
    response = await client.post(
        f"/internal/jobs/{job['id']}/result",
        headers=env["internal"],
        json=result,
        params={"attempt": claimed["job"]["attempts"]},
    )
    assert response.status_code == 200
    report = (await client.get(f"/v1/reports/{response.json()['report_id']}")).json()
    assert report["report_date"] == claimed["request"]["report_date"]
    assert len(report["sections"]) == 1
    assert len(report["sections"][0]["items"]) == 3


async def test_result_can_reference_an_owned_unsubscribed_topic(env):
    await subscribe(env)
    extra = (
        await env["client"].post("/v1/topics", json={"title": "Owned but unsubscribed"})
    ).json()
    job = await queued_job(env)
    claimed = await claim(env)
    result = result_for(claimed)
    result["sections"][0].update(topic_id=extra["id"], title=extra["title"])
    response = await env["client"].post(
        f"/internal/jobs/{job['id']}/result",
        headers=env["internal"],
        json=result,
        params={"attempt": claimed["job"]["attempts"]},
    )
    assert response.status_code == 200


@pytest.mark.parametrize("visibility", ["private", "public"])
async def test_result_cannot_reference_another_users_topic(env, visibility):
    await subscribe(env)
    job = await queued_job(env)
    claimed = await claim(env)
    other = uuid4()
    async with env["database"].transaction() as db:
        await db.execute(
            "INSERT INTO profiles (user_id,email,status) VALUES (%s,'other@example.com','active')",
            (other,),
        )
        topic = await db.one(
            "INSERT INTO topics (owner_id,title,normalized_key,visibility) "
            "VALUES (%s,'Other topic','other',%s) RETURNING id",
            (other, visibility),
        )
    result = result_for(claimed)
    result["sections"][0]["topic_id"] = str(topic["id"])
    response = await env["client"].post(
        f"/internal/jobs/{job['id']}/result",
        headers=env["internal"],
        json=result,
        params={"attempt": claimed["job"]["attempts"]},
    )
    assert response.status_code == 422
    async with env["database"].transaction() as db:
        assert (await db.one("SELECT count(*) AS n FROM reports"))["n"] == 0
        assert (await db.one("SELECT count(*) AS n FROM usage_logs"))["n"] == 0
        row = await db.one("SELECT user_id,status FROM jobs WHERE id=%s", (job["id"],))
        assert row == {"user_id": OWNER, "status": "running"}


async def test_deleted_topic_is_rejected_before_report_storage(env):
    subscription = await subscribe(env)
    job = await queued_job(env)
    claimed = await claim(env)
    assert (await env["client"].delete(f"/v1/topics/{subscription['topic_id']}")).status_code == 204
    response = await env["client"].post(
        f"/internal/jobs/{job['id']}/result",
        headers=env["internal"],
        json=result_for(claimed),
        params={"attempt": claimed["job"]["attempts"]},
    )
    assert response.status_code == 422
