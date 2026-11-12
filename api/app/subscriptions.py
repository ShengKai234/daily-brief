from uuid import UUID

from fastapi import APIRouter, Request, Response

from app import topics
from app.contract import body, fields
from app.problems import Problem
from app.profiles import User

router = APIRouter(prefix="/api/v1/subscriptions")
SUB_FIELDS = "id topic_id item_count position enabled"


async def render(db, row, user_id):
    return {
        **fields(row, SUB_FIELDS),
        "topic": await topics.owned_topic(db, user_id, row["topic_id"]),
    }


async def brief_topics(db, user_id):
    """Service interface used by jobs; modules do not read subscription tables."""
    rows = await db.all(
        "SELECT * FROM subscriptions WHERE user_id=%s AND enabled ORDER BY position, id", (user_id,)
    )
    result = []
    for row in rows:
        topic = await topics.owned_topic(db, user_id, row["topic_id"])
        result.append(
            {"topic_id": row["topic_id"], "title": topic["title"], "item_count": row["item_count"]}
        )
    return result


@router.get("", operation_id="listSubscriptions")
async def list_subscriptions(request: Request, user: User):
    async with request.app.state.database.transaction() as db:
        rows = await db.all(
            "SELECT * FROM subscriptions WHERE user_id=%s ORDER BY position, id", (user["user_id"],)
        )
        return {"subscriptions": [await render(db, row, user["user_id"]) for row in rows]}


@router.post("", status_code=201, operation_id="createSubscription")
async def create_subscription(request: Request, user: User):
    data = await body(request, "createSubscription")
    async with request.app.state.database.transaction() as db:
        if "title" in data:
            # Reuse an owned normalized topic when subscribing by title.
            topic = await topics.by_title(db, user["user_id"], data["title"])
            topic_id = topic["id"]
        else:
            topic_id = UUID(data["topic_id"])
            await topics.owned_topic(db, user["user_id"], topic_id)
        row = await db.one(
            "INSERT INTO subscriptions (user_id, topic_id, item_count, position, enabled) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (user_id,topic_id) DO NOTHING RETURNING *",
            (
                user["user_id"],
                topic_id,
                data.get("item_count", 3),
                data.get("position", 0),
                data.get("enabled", True),
            ),
        )
        if not row:
            raise Problem(409, "already_subscribed")
        return await render(db, row, user["user_id"])


@router.patch("/{subscription_id}", operation_id="updateSubscription")
async def update_subscription(subscription_id: UUID, request: Request, user: User):
    data = await body(request, "updateSubscription")
    async with request.app.state.database.transaction() as db:
        row = await db.update(
            "subscriptions", data, "id=%s AND user_id=%s", (subscription_id, user["user_id"])
        )
        if not row:
            raise Problem(404, "not_found")
        return await render(db, row, user["user_id"])


@router.delete("/{subscription_id}", status_code=204, operation_id="deleteSubscription")
async def delete_subscription(subscription_id: UUID, request: Request, user: User):
    async with request.app.state.database.transaction() as db:
        row = await db.one(
            "DELETE FROM subscriptions WHERE id=%s AND user_id=%s RETURNING id",
            (subscription_id, user["user_id"]),
        )
        if not row:
            raise Problem(404, "not_found")
    return Response(status_code=204)
