"""Validate a runner's output against contracts/brief-result.schema.json plus request checks."""

from __future__ import annotations

import copy
import json
import os
import re
import uuid
from collections import Counter
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


def cli_schema() -> dict[str, Any]:
    """Looser copy of the contract schema for `claude --json-schema`.

    The CLI checks the schema with strict draft-07 Ajv, which rejects `$schema` (2020-12),
    `prefixItems`, and `if/then`. This copy only steers the model; `validate_result` still
    enforces the full contract schema on the output. Derived from the contract, never edited
    by hand, so it cannot drift.
    """
    schema = copy.deepcopy(load_schema())

    def loosen(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("$schema", "$id", "$comment", "if", "then", "else"):
                node.pop(key, None)
            if "prefixItems" in node:
                node.pop("prefixItems")
                node["items"] = {"$ref": "#/$defs/Item"}
            for value in node.values():
                loosen(value)
        elif isinstance(node, list):
            for value in node:
                loosen(value)

    loosen(schema)
    return schema


def _path(parts) -> str:
    return "/".join(str(p) for p in parts) or "<root>"


_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def _is_rfc3339(value: str) -> bool:
    """Date-time with a required offset (rejects date-only and offset-less timestamps)."""
    if not _RFC3339.match(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


MAX_ITEMS_PER_HOST = 2  # per section: one site must not supply a whole topic


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.")


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
    # Field path and rule name only: rejected values are report content and must not be logged
    # or sent in the fail payload (the repo and its Actions logs are public).
    errors = [
        f"{_path(e.absolute_path)}: {e.validator}"
        for e in sorted(
            validator.iter_errors(result), key=lambda e: [str(p) for p in e.absolute_path]
        )
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
    topic_titles = {t["topic_id"]: t["title"] for t in request["topics"]}
    seen: set[str] = set()
    for section in result["sections"]:
        tid = section["topic_id"]
        if not _is_uuid(tid) or tid not in wanted:
            problems.append("section with a topic_id that is not in the request")
            continue
        if tid in seen:
            problems.append(f"duplicate section for topic {tid}")
        seen.add(tid)
        wanted_title = topic_titles[tid]
        if section["title"] != wanted_title:
            problems.append(f"topic {tid}: section title must equal the request's topic title")
        items = section["items"]
        per_host = Counter(_host(i["source_url"]) for i in items)
        if per_host and max(per_host.values()) > MAX_ITEMS_PER_HOST:
            problems.append(
                f"topic {tid}: more than {MAX_ITEMS_PER_HOST} items from one source host"
            )
        if len(items) > wanted[tid]:
            problems.append(f"topic {tid}: {len(items)} items, limit {wanted[tid]}")
        for position, item in enumerate(items, start=1):
            if item["rank"] != position:
                problems.append(f"topic {tid}: rank must equal 1-based position (item {position})")
        for item in items:
            if not _is_http_url(item["source_url"]):
                problems.append(f"topic {tid}: source_url is not an http(s) URL")
            published = item["published_at"]
            if published is None:
                # Stricter than the contract (which allows null): the date must come from the
                # fetched page so freshness can be judged. Items without a date are dropped.
                problems.append(f"topic {tid}: published_at is required")
            elif not _is_rfc3339(published):
                problems.append(f"topic {tid}: published_at is not an RFC 3339 date-time")
    missing = set(wanted) - seen
    if missing:
        problems.append(f"missing sections for {len(missing)} topic(s)")
    if problems:
        raise OutputInvalidError("; ".join(problems[:10]))
    return result
