from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request

from app.contract import fields
from app.problems import Problem
from app.profiles import User

router = APIRouter(prefix="/api/v1/reports")
REPORT_FIELDS = "id report_date slot_id generated_at visibility"
SECTION_FIELDS = "id topic_id title position status note"
ITEM_FIELDS = "id rank title summary source_name source_url published_at"


async def report_for_job(db, job_id, user_id):
    row = await db.one("SELECT id FROM reports WHERE job_id=%s AND user_id=%s", (job_id, user_id))
    return row["id"] if row else None


async def store(db, job, result):
    row = await db.one(
        "INSERT INTO reports (user_id, job_id, report_date, slot_id) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        (job["user_id"], job["id"], date.fromisoformat(result["report_date"]), job["slot_id"]),
    )
    for position, section in enumerate(result["sections"]):
        saved = await db.one(
            "INSERT INTO report_sections (report_id, topic_id, title, position, status, note) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                row["id"],
                UUID(section["topic_id"]),
                section["title"],
                position,
                section["status"],
                section.get("note"),
            ),
        )
        for item in section["items"]:
            published = item["published_at"]
            await db.execute(
                "INSERT INTO report_items (section_id, rank, title, summary, source_name, "
                "source_url, published_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    saved["id"],
                    item["rank"],
                    item["title"],
                    item["summary"],
                    item["source_name"],
                    item["source_url"],
                    datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None,
                ),
            )
    return row["id"]


async def owned_item(db, item_id, user_id):
    return await db.one(
        "SELECT i.id FROM report_items i JOIN report_sections s ON s.id=i.section_id "
        "JOIN reports r ON r.id=s.report_id WHERE i.id=%s AND r.user_id=%s",
        (item_id, user_id),
    )


@router.get("", operation_id="listReports")
async def list_reports(request: Request, user: User, date: date | None = None):
    from app.marks import marks_for_items
    from app.slots import get_set

    target = date or datetime.now(ZoneInfo(user["timezone"])).date()
    async with request.app.state.database.transaction() as db:
        rows = await db.all(
            "SELECT * FROM reports WHERE user_id=%s AND report_date=%s ORDER BY generated_at, id",
            (user["user_id"], target),
        )
        slot_map = {slot["id"]: slot["local_time"] for slot in (await get_set(db, user))["slots"]}
        result = []
        for row in rows:
            sections = await db.all(
                "SELECT id FROM report_sections WHERE report_id=%s", (row["id"],)
            )
            items = await db.all(
                "SELECT i.id FROM report_items i JOIN report_sections s "
                "ON s.id=i.section_id WHERE s.report_id=%s",
                (row["id"],),
            )
            marks = await marks_for_items(db, user["user_id"], [item["id"] for item in items])
            result.append(
                {
                    **fields(row, "id report_date slot_id generated_at"),
                    "local_time": slot_map.get(row["slot_id"]),
                    "section_count": len(sections),
                    "item_count": len(items),
                    "unread_count": len(items)
                    - sum(mark["read_at"] is not None for mark in marks.values()),
                }
            )
    return {"reports": result}


@router.get("/{report_id}", operation_id="getReport")
async def get_report(report_id: UUID, request: Request, user: User):
    from app.marks import marks_for_items

    async with request.app.state.database.transaction() as db:
        row = await db.one(
            "SELECT * FROM reports WHERE id=%s AND user_id=%s", (report_id, user["user_id"])
        )
        if not row:
            raise Problem(404, "not_found")
        result = fields(row, REPORT_FIELDS)
        result["sections"] = []
        sections = await db.all(
            "SELECT * FROM report_sections WHERE report_id=%s ORDER BY position", (report_id,)
        )
        for section in sections:
            items = await db.all(
                "SELECT * FROM report_items WHERE section_id=%s ORDER BY rank", (section["id"],)
            )
            marks = await marks_for_items(db, user["user_id"], [item["id"] for item in items])
            result["sections"].append(
                {
                    **fields(section, SECTION_FIELDS),
                    "items": [
                        {**fields(item, ITEM_FIELDS), "mark": marks.get(item["id"])}
                        for item in items
                    ],
                }
            )
    return result
