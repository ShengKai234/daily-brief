"""Validate a runner's output against contracts/brief-result.schema.json plus request checks."""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator

from .errors import OutputInvalidError

SCHEMA_ENV = "BRIEF_RESULT_SCHEMA"
DEFAULT_SCHEMA = Path(__file__).resolve().parents[3] / "contracts" / "brief-result.schema.json"


def schema_path() -> Path:
    return Path(os.environ.get(SCHEMA_ENV) or DEFAULT_SCHEMA)


@lru_cache
def load_schema() -> dict[str, Any]:
    return json.loads(schema_path().read_text(encoding="utf-8"))


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and " " not in value


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def validate_result(result: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Return `result` if valid, else raise OutputInvalidError listing the problems."""
    if not isinstance(result, dict):
        raise OutputInvalidError("result is not a JSON object")
    validator = Draft202012Validator(load_schema())
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message[:200]}"
        for e in sorted(validator.iter_errors(result), key=lambda e: list(e.absolute_path))
    ]
    if errors:
        raise OutputInvalidError("schema: " + "; ".join(errors[:10]))

    problems: list[str] = []
    try:
        date.fromisoformat(result["report_date"])
    except ValueError:
        problems.append("report_date is not a date")
    if result["report_date"] != request["report_date"]:
        problems.append("report_date differs from the request")
    if result["locale"] != request["locale"]:
        problems.append("locale differs from the request")

    wanted = {t["topic_id"]: t["item_count"] for t in request["topics"]}
    seen: set[str] = set()
    for section in result["sections"]:
        tid = section["topic_id"]
        if not _is_uuid(tid) or tid not in wanted:
            problems.append(f"unknown topic_id {tid}")
            continue
        if tid in seen:
            problems.append(f"duplicate section for topic {tid}")
        seen.add(tid)
        items = section["items"]
        if len(items) > wanted[tid]:
            problems.append(f"topic {tid}: {len(items)} items, limit {wanted[tid]}")
        if [i["rank"] for i in items] != list(range(1, len(items) + 1)):
            problems.append(f"topic {tid}: ranks must run 1..n in order")
        for item in items:
            if not _is_http_url(item["source_url"]):
                problems.append(f"topic {tid}: source_url is not an http(s) URL")
            published = item["published_at"]
            if published is not None:
                try:
                    datetime.fromisoformat(published.replace("Z", "+00:00"))
                except ValueError:
                    problems.append(f"topic {tid}: published_at is not a date-time")
    missing = set(wanted) - seen
    if missing:
        problems.append(f"missing sections for {len(missing)} topic(s)")
    if problems:
        raise OutputInvalidError("; ".join(problems[:10]))
    return result
