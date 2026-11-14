import unicodedata
from uuid import UUID

from fastapi import APIRouter, Request, Response

from app.contract import body, fields
from app.problems import Problem
from app.profiles import User

router = APIRouter(prefix="/api/v1/topics")
TOPIC_FIELDS = "id title normalized_key visibility created_at updated_at"


def normalize(title):
    key = " ".join(unicodedata.normalize("NFKC", title).casefold().split())
    if not key:
        raise Problem(422, "validation_error", "Topic title must contain text")
    return key


async def owned_topic(db, user_id, topic_id):
    row = await db.one("SELECT * FROM topics WHERE id=%s AND owner_id=%s", (topic_id, user_id))
    if not row:
        raise Problem(404, "not_found")
    return fields(row, TOPIC_FIELDS)


async def owned_ids(db, user_id, topic_ids):
    # Keep referenced topics alive through report insertion, independently of
    # current subscriptions. Deletion may clear historical references afterward.
    rows = await db.all(
        "SELECT id FROM topics WHERE owner_id=%s AND id=ANY(%s::uuid[]) FOR KEY SHARE",
        (user_id, topic_ids),
    )
    return {row["id"] for row in rows}


async def by_title(db, user_id, title):
    row = await db.one(
        "SELECT * FROM topics WHERE owner_id=%s AND normalized_key=%s", (user_id, normalize(title))
    )
    return fields(row, TOPIC_FIELDS) if row else await create(db, user_id, {"title": title})


async def create(db, user_id, data):
    if data.get("visibility", "private") != "private":
        raise Problem(422, "visibility_not_supported")
    row = await db.one(
        "INSERT INTO topics (owner_id, title, normalized_key) VALUES (%s, %s, %s) "
        "ON CONFLICT (owner_id, normalized_key) DO NOTHING RETURNING *",
        (user_id, data["title"], normalize(data["title"])),
    )
    if not row:
        raise Problem(409, "topic_exists")
    return fields(row, TOPIC_FIELDS)


@router.get("", operation_id="listTopics")
async def list_topics(request: Request, user: User):
    async with request.app.state.database.transaction() as db:
        rows = await db.all(
            "SELECT * FROM topics WHERE owner_id=%s ORDER BY created_at, id", (user["user_id"],)
        )
    return {"topics": [fields(row, TOPIC_FIELDS) for row in rows]}


@router.post("", status_code=201, operation_id="createTopic")
async def create_topic(request: Request, user: User):
    data = await body(request, "createTopic")
    async with request.app.state.database.transaction() as db:
        return await create(db, user["user_id"], data)


@router.patch("/{topic_id}", operation_id="updateTopic")
async def update_topic(topic_id: UUID, request: Request, user: User):
    data = await body(request, "updateTopic")
    if data.get("visibility", "private") != "private":
        raise Problem(422, "visibility_not_supported")
    if "title" in data:
        data["normalized_key"] = normalize(data["title"])
    async with request.app.state.database.transaction() as db:
        row = await db.update("topics", data, "id=%s AND owner_id=%s", (topic_id, user["user_id"]))
        if not row:
            raise Problem(404, "not_found")
    return fields(row, TOPIC_FIELDS)


@router.delete("/{topic_id}", status_code=204, operation_id="deleteTopic")
async def delete_topic(topic_id: UUID, request: Request, user: User):
    async with request.app.state.database.transaction() as db:
        row = await db.one(
            "DELETE FROM topics WHERE id=%s AND owner_id=%s RETURNING id",
            (topic_id, user["user_id"]),
        )
        if not row:
            raise Problem(404, "not_found")
    return Response(status_code=204)
