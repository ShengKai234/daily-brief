from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request

from app.auth import Authenticated
from app.contract import body, fields
from app.problems import Problem

router = APIRouter(prefix="/api/v1")
PROFILE_FIELDS = "user_id display_name avatar_url bio timezone locale status created_at updated_at"


async def profile(request: Request, me: Authenticated):
    """The signed-in user's profile. Profiles are never created here (AUTH-1)."""
    async with request.app.state.database.transaction() as db:
        row = await db.one("SELECT * FROM profiles WHERE user_id=%s", (me.user_id,))
    if row is None or row["status"] != "active":
        raise Problem(401, "unauthorized")
    return fields(row, PROFILE_FIELDS)


User = Annotated[dict, Depends(profile)]
SignedIn = User


async def lock_user(db, user_id):
    return await db.one("SELECT * FROM profiles WHERE user_id=%s FOR UPDATE", (user_id,))


async def scheduler_users(db, *, local=False):
    """Active profiles with a valid passkey (AUTH-16); APP_ENV=local also serves the seed owner."""
    return await db.all(
        "SELECT p.*, a.runner, a.daily_run_limit FROM profiles p "
        "JOIN agent_settings a USING (user_id) WHERE p.status='active' "
        "AND (%s OR EXISTS (SELECT 1 FROM passkey_credentials c "
        "WHERE c.user_id = p.user_id AND c.revoked_at IS NULL)) "
        "ORDER BY p.user_id FOR UPDATE OF p SKIP LOCKED",
        (local,),
    )


async def job_profile(db, user_id):
    return await db.one("SELECT * FROM profiles WHERE user_id=%s", (user_id,))


async def job_settings(db, user_id):
    return await db.one(
        "SELECT runner, model, daily_run_limit FROM agent_settings WHERE user_id=%s", (user_id,)
    )


@router.get("/me", operation_id="getMe")
async def get_me(user: SignedIn):
    return user


@router.patch("/me", operation_id="updateMe")
async def update_me(request: Request, user: User):
    data = await body(request, "updateMe")
    if "timezone" in data:
        try:
            ZoneInfo(data["timezone"])
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise Problem(422, "validation_error", "Unknown IANA timezone") from exc
    if "locale" in data and not 2 <= len(data["locale"]) <= 35:
        raise Problem(422, "validation_error", "Locale must contain 2 to 35 characters")
    async with request.app.state.database.transaction() as db:
        row = await db.update("profiles", data, "user_id=%s", (user["user_id"],))
    return fields(row, PROFILE_FIELDS)


@router.get("/agent-settings", operation_id="getAgentSettings")
async def get_settings(request: Request, user: User):
    async with request.app.state.database.transaction() as db:
        return await job_settings(db, user["user_id"])


@router.patch("/agent-settings", operation_id="updateAgentSettings")
async def update_settings(request: Request, user: User):
    await body(request, "updateAgentSettings")
    raise Problem(403, "agent_settings_locked")
