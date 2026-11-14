import json

import httpx

from brief_worker.client import JobClient
from brief_worker.main import process_one

from .conftest import OTHER, OWNER


def make_client(
    request_dict, job_runner, user_id, calls, request_user_id=None, queries=None, conflict=None
):
    request_dict["user_id"] = request_user_id or user_id
    job = {
        "id": "44444444-4444-4444-8444-444444444444",
        "user_id": user_id,
        "slot_id": None,
        "scheduled_for": "2026-11-12T07:00:00Z",
        "attempts": 3,
        "runner": job_runner,
    }

    def handler(req: httpx.Request):
        calls.append((req.url.path, req.headers.get("x-internal-token"), req.content))
        if queries is not None:
            queries.append(dict(req.url.params))
        if conflict and not req.url.path.endswith("/claim"):
            return httpx.Response(
                409,
                headers={"content-type": "application/problem+json"},
                json={"code": conflict, "status": 409, "title": "Conflict"},
            )
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


def test_job_cap_is_below_api_lease():
    from brief_worker.main import LEASE_CAP_S, worst_case_job_s

    assert worst_case_job_s(600) <= LEASE_CAP_S < 35 * 60


async def test_attempt_is_sent_on_result(request_dict):
    calls, queries = [], []
    client = make_client(request_dict, "claude_subscription", OWNER, calls, queries=queries)
    await process_one(client, "w", override="stub", owner_user_id=OWNER)
    assert calls[-1][0].endswith("/result") and queries[-1] == {"attempt": "3"}


async def test_attempt_is_sent_on_fail(request_dict):
    calls, queries = [], []
    client = make_client(request_dict, "claude_subscription", OTHER, calls, queries=queries)
    await process_one(client, "w", override=None, owner_user_id=OWNER)
    assert calls[-1][0].endswith("/fail") and queries[-1] == {"attempt": "3"}


async def test_lease_lost_on_result_is_terminal_no_fail_posted(request_dict):
    calls = []
    client = make_client(request_dict, "claude_subscription", OWNER, calls, conflict="lease_lost")
    assert await process_one(client, "w", override="stub", owner_user_id=OWNER)
    paths = [c[0].rsplit("/", 1)[1] for c in calls]
    assert paths == ["claim", "result"]  # no /fail after lease_lost


async def test_lease_lost_on_fail_is_dropped(request_dict):
    calls = []
    client = make_client(request_dict, "claude_subscription", OTHER, calls, conflict="lease_lost")
    assert await process_one(client, "w", override=None, owner_user_id=OWNER)
    assert [c[0].rsplit("/", 1)[1] for c in calls] == ["claim", "fail"]


async def test_other_conflict_is_not_lease_lost(request_dict):
    calls = []
    client = make_client(request_dict, "claude_subscription", OWNER, calls, conflict="topic_exists")
    await process_one(client, "w", override="stub", owner_user_id=OWNER)
    # generic error path: reported as a retryable crash, not silently dropped as lease loss
    assert [c[0].rsplit("/", 1)[1] for c in calls][:2] == ["claim", "result"]
    assert calls[-1][0].endswith("/fail")
