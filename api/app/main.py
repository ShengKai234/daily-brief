"""Daily Brief API entry point.

Skeleton from bootstrap: only the health check.
The api agent owns everything else (DESIGN 10, agent C).
Routes live under /api because CloudFront forwards /api/* unchanged
to the Function URL (DESIGN 4.1).
"""

from fastapi import APIRouter, FastAPI

app = FastAPI(title="Daily Brief API", version="0.1.0", docs_url=None, redoc_url=None)

api = APIRouter(prefix="/api")


@api.get("/health", tags=["health"], operation_id="getHealth")
async def health() -> dict[str, str]:
    """Liveness check. Unauthenticated and returns no data."""
    return {"status": "ok"}


app.include_router(api)
