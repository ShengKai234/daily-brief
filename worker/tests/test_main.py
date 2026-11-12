import httpx

from brief_worker.client import JobClient
from brief_worker.main import process_one

from .conftest import OTHER, OWNER


def make_client(request_dict, job_runner, user_id, calls):
    request_dict["user_id"] = user_id
    job = {
        "id": "44444444-4444-4444-8444-444444444444",
        "user_id": user_id,
        "slot_id": None,
        "scheduled_for": "2026-11-12T07:00:00Z",
        "attempts": 1,
        "runner": job_runner,
    }

    def handler(req: httpx.Request):
        calls.append((req.url.path, req.headers.get("x-internal-token")))
        if req.url.path.endswith("/claim"):
            return httpx.Response(200, json={"job": job, "request": request_dict})
        if req.url.path.endswith("/result"):
            return httpx.Response(200, json={"report_id": "55555555-5555-4555-8555-555555555555"})
        return httpx.Response(200, json={"status": "failed", "attempts": 1})

    http = httpx.AsyncClient(
        base_url="http://api",
        headers={"X-Internal-Token": "t"},
        transport=httpx.MockTransport(handler),
    )
    return JobClient("http://api", "t", http=http)


async def test_stub_job_succeeds(request_dict):
    calls = []
    client = make_client(request_dict, "claude_subscription", OWNER, calls)
    assert await process_one(client, "w", override="stub", owner_user_id=OWNER)
    assert calls[-1][0].endswith("/result") and calls[-1][1] == "t"


async def test_non_owner_subscription_job_is_failed_not_retried(request_dict):
    calls = []
    client = make_client(request_dict, "claude_subscription", OTHER, calls)
    assert await process_one(client, "w", override=None, owner_user_id=OWNER)
    assert calls[-1][0].endswith("/fail")


async def test_empty_queue(request_dict):
    http = httpx.AsyncClient(
        base_url="http://api", transport=httpx.MockTransport(lambda r: httpx.Response(204))
    )
    assert not await process_one(
        JobClient("http://api", "t", http=http), "w", override=None, owner_user_id=OWNER
    )
