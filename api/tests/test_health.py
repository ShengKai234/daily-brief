import httpx
from conftest import make_settings

from app.main import create_app


async def test_health_ok() -> None:
    app = create_app(make_settings("postgresql://postgres@127.0.0.1:1/unused"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
