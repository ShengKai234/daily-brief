from http import HTTPStatus

from fastapi.responses import JSONResponse


class Problem(Exception):
    def __init__(self, status: int, code: str, detail: str | None = None):
        self.status = status
        self.code = code
        self.detail = detail or code.replace("_", " ")


def problem_response(status: int, code: str, detail: str | None = None):
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": HTTPStatus(status).phrase,
            "status": status,
            "detail": detail or code.replace("_", " "),
            "code": code,
        },
    )
