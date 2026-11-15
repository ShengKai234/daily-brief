from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import OWNER, token


async def test_profile_gate_and_invite(env):
    client = env["client"]
    pending = uuid4()
    headers = {"Authorization": "Bearer " + token(env["settings"], pending, "pending@example.com")}
    response = await client.get("/v1/me", headers=headers)
    assert response.json()["status"] == "pending"
    for method, path, data in [("GET", "/v1/topics", None), ("PATCH", "/v1/me", {"bio": "test"})]:
        response = await client.request(
            method, path, headers=headers, **({"json": data} if data else {})
        )
        assert response.status_code == 403
        assert response.json()["code"] == "account_pending"
    async with env["database"].transaction() as db:
        await db.execute("INSERT INTO invite_codes (code,max_uses) VALUES ('EXAMPLE-CODE',1)")
    response = await client.post(
        "/v1/auth/redeem-invite", headers=headers, json={"code": "EXAMPLE-CODE"}
    )
    assert response.json()["status"] == "active"
    response = await client.post(
        "/v1/auth/redeem-invite", headers=headers, json={"code": "EXAMPLE-CODE"}
    )
    assert response.json()["code"] == "already_active"
    async with env["database"].transaction() as db:
        assert (await db.one("SELECT used_count FROM invite_codes"))["used_count"] == 1
        await db.execute("UPDATE profiles SET status='disabled' WHERE user_id=%s", (pending,))
    for path in ["/v1/me", "/v1/topics"]:
        assert (await client.get(path, headers=headers)).json()["code"] == "account_disabled"


async def test_crud_and_stable_slot_replacement(env):
    client = env["client"]
    response = await client.patch(
        "/v1/me",
        json={
            "display_name": "Owner",
            "bio": "Example bio",
            "avatar_url": None,
            "timezone": "UTC",
            "locale": "en",
        },
    )
    assert response.json()["timezone"] == "UTC"
    assert (await client.patch("/v1/me", json={"timezone": "Invalid/Zone"})).status_code == 422
    topic = (await client.post("/v1/topics", json={"title": "  ＡＩ  News "})).json()
    assert topic["normalized_key"] == "ai news"
    assert len((await client.get("/v1/topics")).json()["topics"]) == 1
    assert (await client.post("/v1/topics", json={"title": "ai news"})).status_code == 409
    assert (
        await client.post("/v1/topics", json={"title": "Example", "visibility": "public"})
    ).json()["code"] == "visibility_not_supported"
    updated = await client.patch(f"/v1/topics/{topic['id']}", json={"title": "AI"})
    assert updated.json()["normalized_key"] == "ai"
    sub = (
        await client.post("/v1/subscriptions", json={"topic_id": topic["id"], "item_count": 2})
    ).json()
    assert sub["topic"]["title"] == "AI"
    assert (await client.post("/v1/subscriptions", json={"title": "AI"})).json()[
        "code"
    ] == "already_subscribed"
    assert (
        await client.patch(f"/v1/subscriptions/{sub['id']}", json={"enabled": False, "position": 2})
    ).json()["position"] == 2
    assert len((await client.get("/v1/subscriptions")).json()["subscriptions"]) == 1
    original = (
        await client.put(
            "/v1/delivery-slots",
            json={
                "slots": [
                    {"local_time": "07:00", "enabled": True},
                    {"local_time": "12:30", "enabled": True},
                ]
            },
        )
    ).json()
    replaced = (
        await client.put(
            "/v1/delivery-slots",
            json={
                "slots": [
                    {"local_time": "12:30", "enabled": True},
                    {"local_time": "07:00", "enabled": False},
                ]
            },
        )
    ).json()
    assert [slot["id"] for slot in replaced["slots"]] == [slot["id"] for slot in original["slots"]]
    assert (await client.get("/v1/delivery-slots")).json() == replaced
    for values, code in [
        (["07:00"] * 4, "slot_limit_exceeded"),
        (["07:00"] * 2, "duplicate_slot"),
        (["07:15"], "validation_error"),
    ]:
        response = await client.put(
            "/v1/delivery-slots",
            json={"slots": [{"local_time": value, "enabled": True} for value in values]},
        )
        assert response.status_code == 422
        assert response.json()["code"] == code
    assert (await client.put("/v1/delivery-slots", json={"slots": []})).json()["slots"] == []
    assert (await client.get("/v1/agent-settings")).json()["daily_run_limit"] == 3
    assert (await client.patch("/v1/agent-settings", json={"runner": "anthropic_api"})).json()[
        "code"
    ] == "agent_settings_locked"
    assert (await client.delete(f"/v1/subscriptions/{sub['id']}")).status_code == 204
    assert (await client.delete(f"/v1/topics/{topic['id']}")).status_code == 204


@pytest.mark.parametrize(
    "payload",
    [{}, {"title": ""}, {"title": None}, {"title": "ok", "owner_id": str(OWNER)}, {"title": 123}],
)
async def test_strict_request_validation(env, payload):
    assert (await env["client"].post("/v1/topics", json=payload)).status_code == 422


async def test_wrong_credentials_and_expired_jwt(env):
    client = env["client"]
    for headers in [
        {},
        env["internal"],
        {"Authorization": "Bearer invalid"},
        {
            "Authorization": "Bearer "
            + token(env["settings"], exp=datetime.now(UTC) - timedelta(seconds=1))
        },
    ]:
        assert (await client.get("/v1/me", headers=headers)).status_code == 401
    for headers in [
        {},
        {"Authorization": "Bearer " + token(env["settings"])},
        {**env["internal"], "Authorization": "Bearer " + token(env["settings"])},
    ]:
        assert (
            await client.post(
                "/internal/jobs/claim", headers=headers, json={"worker_id": "example"}
            )
        ).status_code == 401
    response = await client.post(
        "/v1/topics",
        content="{",
        headers={
            "Authorization": "Bearer " + token(env["settings"]),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400


async def test_topics_subscriptions_are_private(env):
    client = env["client"]
    topic = (await client.post("/v1/topics", json={"title": "Owner topic"})).json()
    sub = (await client.post("/v1/subscriptions", json={"topic_id": topic["id"]})).json()
    other = uuid4()
    async with env["database"].transaction() as db:
        await db.execute("INSERT INTO allowed_emails (email) VALUES ('other@example.com')")
    headers = {"Authorization": "Bearer " + token(env["settings"], other, "other@example.com")}
    assert (await client.get("/v1/topics", headers=headers)).json()["topics"] == []
    assert (await client.get("/v1/subscriptions", headers=headers)).json()["subscriptions"] == []
    for method, path, data in [
        ("PATCH", f"/v1/topics/{topic['id']}", {"title": "hijack"}),
        ("DELETE", f"/v1/topics/{topic['id']}", None),
        ("POST", "/v1/subscriptions", {"topic_id": topic["id"]}),
        ("PATCH", f"/v1/subscriptions/{sub['id']}", {"enabled": False}),
        ("DELETE", f"/v1/subscriptions/{sub['id']}", None),
    ]:
        assert (
            await client.request(method, path, headers=headers, **({"json": data} if data else {}))
        ).status_code == 404
