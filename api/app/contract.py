"""Packaged contract snapshot; tests check exact equality with contracts/.

Keeping a generated JSON snapshot under app/ avoids shipping docs or requiring
the repository at runtime. External BriefResult refs are resolved during export.
"""

import json
from pathlib import Path

from fastapi import Request
from jsonschema import Draft202012Validator, FormatChecker

from app.problems import Problem

SPEC = json.loads(Path(__file__).with_name("openapi.json").read_text())
OPERATIONS = {
    operation["operationId"]: operation
    for path in SPEC["paths"].values()
    for method, operation in path.items()
    if method in {"get", "post", "put", "patch", "delete"}
}


def validate(value, schema):
    document = {**SPEC, **schema}
    Draft202012Validator(document, format_checker=FormatChecker()).validate(value)


async def body(request: Request, operation_id: str):
    try:
        value = await request.json()
    except (ValueError, UnicodeError) as exc:
        raise Problem(400, "bad_request") from exc
    from jsonschema import ValidationError

    schema = OPERATIONS[operation_id]["requestBody"]["content"]["application/json"]["schema"]
    if operation_id == "replaceDeliverySlots" and isinstance(value, dict):
        slots = value.get("slots")
        if isinstance(slots, list) and len(slots) > 3:
            raise Problem(422, "slot_limit_exceeded")
    try:
        validate(value, schema)
    except ValidationError as exc:
        # Do not echo request values (credentials and personal content) in errors.
        if operation_id == "submitJobResult" and list(exc.absolute_path)[-1:] == ["rank"]:
            raise Problem(422, "invalid_rank") from exc
        raise Problem(422, "validation_error") from exc
    return value


def fields(row, names):
    return {key: row[key] for key in names.split()}
