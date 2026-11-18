"""Pure-ASGI request guard for /api (docs/auth-passkey-design.md AUTH-9).

Order on /v1: route family (401) -> body size (413) -> Origin (403) -> media type (415).
Rate limit (429), authentication (401), reauth (403), validation and conflicts run in the
routes' dependencies afterwards. Adds `Cache-Control: no-store` to every /api response.
/internal is not checked for Origin.
"""

import logging
import re

from app.problems import problem_response

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
PUBLIC_AUTH = re.compile(r"^/api/v1/auth/(registration|login)/(options|verify)$")
PUBLIC_LIMIT = 64 * 1024
OTHER_LIMIT = 256 * 1024


class RequestGuard:
    def __init__(self, app, settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api"):
            return await self.app(scope, receive, send)

        async def send_no_store(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k != b"cache-control"]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        if path == "/api/v1" or path.startswith("/api/v1/"):
            rejected, receive = await self.check_v1(scope, receive, path)
            if rejected is not None:
                return await rejected(scope, receive, send_no_store)
        started = False

        async def tracking_send(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send_no_store(message)

        try:
            return await self.app(scope, receive, tracking_send)
        except Exception as exc:
            # Contain unexpected failures here: the adapter would log the original text, which can
            # carry database diagnostics (for example a row with a challenge). Log only the type.
            logging.getLogger(__name__).error("Unhandled error (%s)", type(exc).__name__)
            if started:
                return None
            return await problem_response(500, "internal_error", "Internal error")(
                scope, receive, send_no_store
            )

    async def check_v1(self, scope, receive, path):
        headers = scope["headers"]
        names = [k for k, _ in headers]
        if b"x-internal-token" in names or b"authorization" in names:
            return problem_response(401, "unauthorized"), receive
        method = scope["method"]
        body = b""
        if method in UNSAFE or any(k == b"content-length" for k in names):
            limit = PUBLIC_LIMIT if PUBLIC_AUTH.match(path) else OTHER_LIMIT
            declared = [v for k, v in headers if k == b"content-length"]
            if declared and (not declared[0].isdigit() or int(declared[0]) > limit):
                return problem_response(413, "payload_too_large"), receive
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    break
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > limit:
                    return problem_response(413, "payload_too_large"), receive
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            body = b"".join(chunks)
        if method in UNSAFE:
            origins = [v for k, v in headers if k == b"origin"]
            if (
                len(origins) != 1
                or origins[0].decode("latin-1") not in self.settings.webauthn_origins
            ):
                return problem_response(403, "origin_mismatch"), receive
            if body:
                types = [v.decode("latin-1") for k, v in headers if k == b"content-type"]
                media = types[0].split(";")[0].strip().lower() if len(types) == 1 else ""
                if media != "application/json":
                    return problem_response(415, "unsupported_media_type"), receive

        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return None, replay
