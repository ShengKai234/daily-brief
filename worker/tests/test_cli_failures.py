"""CLI failures become short safe codes; sign-in and usage-limit failures are not retried."""

import json
import logging

import httpx
import pytest

from brief_worker.client import JobClient
from brief_worker.errors import (
    ClaudeNotLoggedInError,
    ClaudeUsageLimitError,
    OutputExhaustedError,
)
from brief_worker.main import Outcome, process_one
from brief_worker.pipeline import generate_validated
from brief_worker.runners.claude_subscription import ClaudeSubscriptionRunner

from .conftest import OWNER


def result_line(text, is_error=True):
    return json.dumps({"type": "result", "is_error": is_error, "result": text})


def fake_cli(monkeypatch, stdout="", stderr="", returncode=1):
    spawned = []

    class Proc:
        def __init__(self):
            self.returncode = returncode

        async def communicate(self, data=None):
            return stdout.encode(), stderr.encode()

    async def fake_exec(*a, **k):
        spawned.append(a)
        return Proc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    return spawned


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        (result_line("Not logged in · Please run /login"), ""),
        ("", "Error: Invalid API key"),
        ("", "OAuth token has expired. Please run /login"),
        (
            json.dumps(
                {"type": "system", "subtype": "api_retry", "error": "authentication_failed"}
            ),
            "",
        ),
    ],
)
@pytest.mark.parametrize("returncode", [0, 1])
async def test_sign_in_failure_is_classified_and_not_retried(
    request_dict, monkeypatch, stdout, stderr, returncode
):
    spawned = fake_cli(monkeypatch, stdout, stderr, returncode)
    with pytest.raises(ClaudeNotLoggedInError) as exc:
        await generate_validated(ClaudeSubscriptionRunner(OWNER), request_dict)
    assert exc.value.retryable is False
    assert str(exc.value) == "claude_not_logged_in"
    assert len(spawned) == 1  # a sign-in failure cannot succeed on retry


@pytest.mark.parametrize(
    "text",
    [
        "Claude usage limit reached. Your limit will reset at 5pm.",
        "5-hour limit reached",
        "rate limit exceeded",
    ],
)
async def test_usage_limit_is_classified_and_not_retried(request_dict, monkeypatch, text):
    spawned = fake_cli(monkeypatch, result_line(text))
    with pytest.raises(ClaudeUsageLimitError) as exc:
        await generate_validated(ClaudeSubscriptionRunner(OWNER), request_dict)
    assert str(exc.value) == "claude_usage_limit" and exc.value.retryable is False
    assert len(spawned) == 1


async def test_unknown_nonzero_exit_is_retried_once_with_a_code(request_dict, monkeypatch):
    spawned = fake_cli(monkeypatch, "", "something odd happened", 1)
    with pytest.raises(OutputExhaustedError) as exc:
        await generate_validated(ClaudeSubscriptionRunner(OWNER), request_dict)
    assert "claude_exit_nonzero" in str(exc.value)
    assert len(spawned) == 2


async def test_raw_cli_output_never_reaches_errors_or_logs(request_dict, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    fake_cli(monkeypatch, result_line("Not logged in SECRET-TOKEN-xyz"), "SECRET-STDERR", 1)
    with pytest.raises(ClaudeNotLoggedInError) as exc:
        await generate_validated(ClaudeSubscriptionRunner(OWNER), request_dict)
    assert "SECRET" not in str(exc.value) and "SECRET" not in caplog.text


async def test_unknown_error_text_never_reaches_the_error(request_dict, monkeypatch):
    fake_cli(monkeypatch, result_line("weird failure SECRET-DETAIL"), "", 1)
    with pytest.raises(OutputExhaustedError) as exc:
        await generate_validated(ClaudeSubscriptionRunner(OWNER), request_dict)
    assert "SECRET" not in str(exc.value)


def job_client(request_dict, calls, claims=1):
    job = {
        "id": "44444444-4444-4444-8444-444444444444",
        "user_id": OWNER,
        "slot_id": None,
        "scheduled_for": "2026-11-12T07:00:00Z",
        "attempts": 1,
        "runner": "claude_subscription",
    }
    request_dict["user_id"] = OWNER
    left = [claims]

    def handler(req: httpx.Request):
        calls.append((req.url.path, req.content))
        if req.url.path.endswith("/claim"):
            if left[0] == 0:
                return httpx.Response(204)
            left[0] -= 1
            return httpx.Response(200, json={"job": job, "request": request_dict})
        return httpx.Response(200, json={"status": "failed", "attempts": 1})

    http = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))
    return JobClient("http://api", "t", http=http)


async def test_fail_payload_carries_only_the_code(request_dict, monkeypatch):
    fake_cli(monkeypatch, result_line("Not logged in"), "", 1)
    calls = []
    out = await process_one(
        job_client(request_dict, calls), "w", override=None, owner_user_id=OWNER
    )
    path, body = calls[-1]
    assert path.endswith("/fail")
    assert json.loads(body) == {"error": "claude_not_logged_in", "retryable": False}
    assert out == Outcome.HALT  # every further job would fail the same way


async def test_run_stops_claiming_after_a_sign_in_failure(request_dict, monkeypatch):
    from brief_worker import main as m

    fake_cli(monkeypatch, result_line("Not logged in"), "", 1)
    calls = []
    client = job_client(request_dict, calls, claims=3)
    monkeypatch.setattr(m, "JobClient", lambda *a, **k: client)
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "t")
    monkeypatch.setenv("API_BASE_URL", "http://api")
    monkeypatch.setenv("OWNER_USER_ID", OWNER)
    await m.run()
    assert sum(p.endswith("/claim") for p, _ in calls) == 1


async def test_a_normal_failure_does_not_halt(request_dict, monkeypatch):
    fake_cli(monkeypatch, "", "boom", 1)
    calls = []
    out = await process_one(
        job_client(request_dict, calls), "w", override=None, owner_user_id=OWNER
    )
    assert out == Outcome.DONE
