from datetime import time

from fastapi import APIRouter, Request

from app.contract import body
from app.problems import Problem
from app.profiles import User, lock_user

router = APIRouter(prefix="/api/v1/delivery-slots")


async def enabled_slots(db, user_id):
    return await db.all(
        "SELECT * FROM delivery_slots WHERE user_id=%s AND enabled ORDER BY local_time", (user_id,)
    )


async def get_set(db, user):
    rows = await db.all(
        "SELECT id, local_time, enabled, position FROM delivery_slots "
        "WHERE user_id=%s ORDER BY position",
        (user["user_id"],),
    )
    for row in rows:
        row["local_time"] = row["local_time"].strftime("%H:%M")
    return {"slots": rows, "timezone": user["timezone"]}


@router.get("", operation_id="getDeliverySlots")
async def get_slots(request: Request, user: User):
    async with request.app.state.database.transaction() as db:
        return await get_set(db, user)


@router.put("", operation_id="replaceDeliverySlots")
async def replace_slots(request: Request, user: User):
    slots = (await body(request, "replaceDeliverySlots"))["slots"]
    if len({slot["local_time"] for slot in slots}) != len(slots):
        raise Problem(422, "duplicate_slot")
    async with request.app.state.database.transaction() as db:
        # Serialize full-set replacements and scheduler decisions for this user.
        await lock_user(db, user["user_id"])
        existing = await db.all(
            "SELECT id, position FROM delivery_slots WHERE user_id=%s", (user["user_id"],)
        )
        by_position = {row["position"]: row["id"] for row in existing}
        for position, slot in enumerate(slots, 1):
            values = {
                "local_time": time.fromisoformat(slot["local_time"]),
                "enabled": slot["enabled"],
            }
            if position in by_position:
                await db.update("delivery_slots", values, "id=%s", (by_position[position],))
            else:
                await db.execute(
                    "INSERT INTO delivery_slots (user_id, local_time, enabled, position) "
                    "VALUES (%s,%s,%s,%s)",
                    (user["user_id"], values["local_time"], values["enabled"], position),
                )
        await db.execute(
            "DELETE FROM delivery_slots WHERE user_id=%s AND position>%s",
            (user["user_id"], len(slots)),
        )
        result = await get_set(db, user)
    # Deferred uniqueness is checked before this response leaves the endpoint.
    return result
