from uuid import UUID

from fastapi import APIRouter, Request

from app.contract import body
from app.problems import Problem
from app.profiles import User
from app.reports import owned_item

router = APIRouter(prefix="/api/v1/items")


async def marks_for_items(db, user_id, ids):
    rows = await db.all(
        "SELECT item_id, read_at, note, updated_at FROM item_marks "
        "WHERE user_id=%s AND item_id=ANY(%s::uuid[])",
        (user_id, ids),
    )
    return {row["item_id"]: row for row in rows}


@router.put("/{item_id}/mark", operation_id="setItemMark")
async def mark(item_id: UUID, request: Request, user: User):
    data = await body(request, "setItemMark")
    async with request.app.state.database.transaction() as db:
        if not await owned_item(db, item_id, user["user_id"]):
            raise Problem(404, "not_found")
        return await db.one(
            "INSERT INTO item_marks (user_id, item_id, read_at, note) "
            "VALUES (%s,%s,CASE WHEN %s THEN now() ELSE NULL END,%s) "
            "ON CONFLICT (user_id,item_id) DO UPDATE SET "
            "read_at=CASE WHEN %s THEN COALESCE(item_marks.read_at,now()) ELSE NULL END, "
            "note=excluded.note RETURNING item_id, read_at, note, updated_at",
            (user["user_id"], item_id, data["read"], data["note"], data["read"]),
        )
