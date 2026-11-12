import json

import httpx

from brief_worker.client import JobClient
from brief_worker.main import process_one

from .conftest import OTHER, OWNER


def make_client(request_dict, job_runner, user_id, calls, request_user_id=None):
    request_dict["user_id"] = request_user_id or user_id
    job = {
        "id": "44444444-4444-4444-8444-444444444444",
        "user_id": user_id,
        "slot_id": None,
        "scheduled_for": "2026-11-12T07:00:00Z",
        "attempts": 1,
        "runner": job_runner,
    }

    def handler(req: httpx.Request):
        calls.append((req.url.path, req.headers.get("x-internal-token"), req.content))
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


class AlwaysInvalid:
    async def run(self, request):
        return {"report_date": "SECRET-CONTENT"}


async def test_exhausted_invalid_output_is_failed_not_requeued(request_dict, monkeypatch):
    monkeypatch.setattr("brief_worker.main.select_runner", lambda *a, **k: AlwaysInvalid())
    calls = []
    client = make_client(request_dict, "claude_subscription", OWNER, calls)
    await process_one(client, "w", override=None, owner_user_id=OWNER)
    path, _, body = calls[-1]
    payload = json.loads(body)
    assert path.endswith("/fail") and payload["retryable"] is False
    assert "SECRET-CONTENT" not in payload["error"]
    assert not any(c[0].endswith("/result") for c in calls)


async def test_job_request_identity_mismatch_fails_before_spawn(request_dict, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("runner must not be built")

    monkeypatch.setattr("brief_worker.main.select_runner", boom)
    calls = []
    # job says OWNER, request says OTHER: even the owner-named job is refused
    client = make_client(request_dict, "claude_subscription", OWNER, calls, request_user_id=OTHER)
    await process_one(client, "w", override=None, owner_user_id=OWNER)
    assert json.loads(calls[-1][2])["retryable"] is False


async def test_job_owner_checked_before_runner_is_built(request_dict, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("runner must not be built")

    monkeypatch.setattr("brief_worker.main.select_runner", boom)
    calls = []
    client = make_client(request_dict, "claude_subscription", OTHER, calls)
    await process_one(client, "w", override=None, owner_user_id=OWNER)
    assert calls[-1][0].endswith("/fail") and json.loads(calls[-1][2])["retryable"] is False


async def test_deadline_reports_failure(request_dict):
    import asyncio

    class Slow:
        async def run(self, request):
            await asyncio.sleep(5)

    calls = []
    client = make_client(request_dict, "claude_subscription", OWNER, calls)
    import brief_worker.main as m

    orig = m.select_runner
    m.select_runner = lambda *a, **k: Slow()
    try:
        await process_one(client, "w", override=None, owner_user_id=OWNER, time_left_s=0.05)
    finally:
        m.select_runner = orig
    assert calls[-1][0].endswith("/fail") and json.loads(calls[-1][2])["retryable"] is True


async def test_run_stops_claiming_when_budget_too_small(monkeypatch):
    from brief_worker import main as m

    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "t")
    monkeypatch.setenv("API_BASE_URL", "http://api")
    monkeypatch.setenv("WORKER_ATTEMPT_TIMEOUT_S", "600")
    monkeypatch.setenv("WORKER_BUDGET_S", "1000")  # < 2*600+60
    claimed = []

    async def fake(*a, **k):
        claimed.append(1)
        return True

    monkeypatch.setattr(m, "process_one", fake)
    await m.run()
    assert claimed == []


def test_worst_case_fits_workflow_timeout():
    from brief_worker.main import DEFAULT_ATTEMPT_TIMEOUT_S, DEFAULT_BUDGET_S, worst_case_job_s

    assert worst_case_job_s(DEFAULT_ATTEMPT_TIMEOUT_S) < DEFAULT_BUDGET_S < 60 * 60
