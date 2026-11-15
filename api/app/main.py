from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from mangum import Mangum
from psycopg import IntegrityError, OperationalError
from starlette.exceptions import HTTPException

from app import jobs, marks, passkeys, profiles, reports, slots, subscriptions, topics
from app.auth import SessionAuthenticator
from app.config import Settings
from app.contract import SPEC
from app.database import Database
from app.dispatch import configured_dispatcher
from app.guard import RequestGuard
from app.problems import Problem, problem_response


def create_app(settings=None):
    settings = settings or Settings.from_env()
    app = FastAPI(title="Daily Brief API", version="0.1.0", docs_url=None, redoc_url=None)
    app.openapi = lambda: SPEC
    app.state.settings = settings
    app.state.database = Database(settings)
    app.state.authenticator = SessionAuthenticator(settings, app.state.database)
    app.add_middleware(RequestGuard, settings=settings)
    app.state.queue = jobs.PostgresJobQueue(app.state.database, settings)
    app.state.dispatcher = configured_dispatcher(settings, app.state.queue)

    @app.exception_handler(Problem)
    async def handle_problem(request, exc):
        return problem_response(exc.status, exc.code, exc.detail, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request, exc):
        if any(error["loc"][0] == "path" for error in exc.errors()):
            return problem_response(400, "bad_request")
        return problem_response(422, "validation_error")

    @app.exception_handler(IntegrityError)
    async def handle_integrity(request, exc):
        return problem_response(409, "conflict")

    @app.exception_handler(OperationalError)
    async def handle_database(request, exc):
        return problem_response(503, "service_unavailable")

    @app.exception_handler(HTTPException)
    async def handle_http(request, exc):
        return problem_response(
            exc.status_code, "not_found" if exc.status_code == 404 else "bad_request"
        )

    @app.get("/api/health", operation_id="getHealth")
    async def health():
        return {"status": "ok"}

    for router in (
        profiles.router,
        passkeys.router,
        topics.router,
        subscriptions.router,
        slots.router,
        reports.router,
        marks.router,
        jobs.router,
        jobs.public_router,
    ):
        app.include_router(router)
    return app


_app = None
_handler = None


def get_app():
    """Built on first use: importing this module must not require a full environment."""
    global _app
    if _app is None:
        _app = create_app()
    return _app


def __getattr__(name):
    # `uvicorn app.main:app` resolves the ASGI app lazily.
    if name == "app":
        return get_app()
    raise AttributeError(name)


def handler(event, context):
    global _handler
    if _handler is None:
        _handler = Mangum(get_app(), lifespan="off")
    return _handler(event, context)
