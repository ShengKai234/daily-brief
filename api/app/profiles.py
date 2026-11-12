from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request

from app.auth import Identity, principal
from app.contract import body, fields
from app.problems import Problem

router = APIRouter(prefix="/api/v1")
PROFILE_FIELDS = (
    "user_id email display_name avatar_url bio timezone locale status created_at updated_at"
)


async def profile(request: Request, identity: Annotated[Identity, Depends(principal)]):
    async with request.app.state.database.transaction() as db:
        await db.execute(
            "INSERT INTO profiles (user_id, email, status) "
            "VALUES (%s, %s, CASE WHEN EXISTS (SELECT 1 FROM allowed_emails WHERE email=%s) "
            "THEN 'active' ELSE 'pending' END) ON CONFLICT (user_id) DO NOTHING",
            (identity.user_id, identity.email, identity.email),
        )
        row = await db.one("SELECT * FROM profiles WHERE user_id=%s", (identity.user_id,))
        await db.execute(
            "INSERT INTO agent_settings (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (identity.user_id,),
        )
    if row["status"] == "disabled":
        raise Problem(403, "account_disabled")
    return fields(row, PROFILE_FIELDS)


async def active(user: Annotated[dict, Depends(profile)]):
    if user["status"] != "active":
        raise Problem(403, "account_pending")
    return user


User = Annotated[dict, Depends(active)]
SignedIn = Annotated[dict, Depends(profile)]


async def lock_user(db, user_id):
    return await db.one("SELECT * FROM profiles WHERE user_id=%s FOR UPDATE", (user_id,))


async def scheduler_users(db):
    return await db.all(
        "SELECT p.*, a.runner, a.daily_run_limit FROM profiles p "
        "JOIN agent_settings a USING (user_id) WHERE p.status='active' "
        "ORDER BY p.user_id FOR UPDATE OF p SKIP LOCKED"
    )


async def job_profile(db, user_id):
    return await db.one("SELECT * FROM profiles WHERE user_id=%s", (user_id,))


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


@router.post("/auth/redeem-invite", operation_id="redeemInvite")
async def redeem_invite(request: Request, user: SignedIn):
    data = await body(request, "redeemInvite")
    async with request.app.state.database.transaction() as db:
        row = await db.one("SELECT * FROM profiles WHERE user_id=%s FOR UPDATE", (user["user_id"],))
        if row["status"] == "active":
            raise Problem(409, "already_active")
        if row["status"] == "disabled":
            raise Problem(403, "account_disabled")
        code = await db.one(
            "SELECT *, now() AS current_time FROM invite_codes WHERE code=%s FOR UPDATE",
            (data["code"],),
        )
        if not code:
            raise Problem(404, "invite_not_found")
        if code["expires_at"] is not None and code["expires_at"] <= code["current_time"]:
            raise Problem(409, "invite_expired")
        if code["used_count"] >= code["max_uses"]:
            raise Problem(409, "invite_exhausted")
        await db.execute(
            "UPDATE invite_codes SET used_count=used_count+1 WHERE code=%s", (data["code"],)
        )
        await db.execute(
            "INSERT INTO invite_redemptions (code, user_id) VALUES (%s, %s)",
            (data["code"], user["user_id"]),
        )
        row = await db.update("profiles", {"status": "active"}, "user_id=%s", (user["user_id"],))
    return fields(row, PROFILE_FIELDS)


@router.get("/agent-settings", operation_id="getAgentSettings")
async def get_settings(request: Request, user: User):
    async with request.app.state.database.transaction() as db:
        return await db.one(
            "SELECT runner, model, daily_run_limit FROM agent_settings WHERE user_id=%s",
            (user["user_id"],),
        )


@router.patch("/agent-settings", operation_id="updateAgentSettings")
async def update_settings(request: Request, user: User):
    await body(request, "updateAgentSettings")
    raise Problem(403, "agent_settings_locked")
