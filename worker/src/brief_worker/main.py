from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import time

from .client import JobClient
from .errors import DeadlineError, IdentityMismatchError, OwnerOnlyError, WorkerError
from .pipeline import ATTEMPTS, generate_validated
from .runners.anthropic_api import AnthropicApiRunner
from .runners.base import AgentRunner
from .runners.claude_subscription import ClaudeSubscriptionRunner
from .runners.stub import StubRunner

log = logging.getLogger("brief_worker")


# Time budget. The workflow job timeout (60 min) must exceed WORKER_BUDGET_S so there is always
# time left to report a failure. A job is claimed only if a worst-case run still fits.
DEFAULT_ATTEMPT_TIMEOUT_S = 600
DEFAULT_BUDGET_S = 3000
SAFETY_MARGIN_S = 60
# The API re-queues a running job after 35 min (api handoff); never post after that.
LEASE_CAP_S = 30 * 60


def worst_case_job_s(attempt_timeout_s: float) -> float:
    return ATTEMPTS * attempt_timeout_s + SAFETY_MARGIN_S


def select_runner(
    job_runner: str,
    override: str | None,
    owner_user_id: str | None,
    attempt_timeout_s: float = DEFAULT_ATTEMPT_TIMEOUT_S,
) -> AgentRunner:
    """AGENT_RUNNER=stub (local/tests) wins; otherwise the job's own runner is used."""
    name = override or job_runner
    if name == "stub":
        return StubRunner()
    if name == "claude_subscription":
        return ClaudeSubscriptionRunner(owner_user_id, timeout_s=attempt_timeout_s)
    if name == "anthropic_api":
        return AnthropicApiRunner()
    raise ValueError(f"unknown runner {name!r}")


def check_identity(job: dict, request: dict, effective_runner: str, owner_user_id: str | None):
    """Fail closed before any runner is built or spawned (DESIGN 3.1)."""
    if not job.get("user_id") or job["user_id"] != request.get("user_id"):
        raise IdentityMismatchError("job and request name different users")
    if effective_runner == "claude_subscription" and (
        not owner_user_id or job["user_id"] != owner_user_id
    ):
        raise OwnerOnlyError("claude_subscription runner is restricted to the owner")


async def process_one(
    client: JobClient,
    worker_id: str,
    *,
    override: str | None,
    owner_user_id: str | None,
    attempt_timeout_s: float = DEFAULT_ATTEMPT_TIMEOUT_S,
    time_left_s: float | None = None,
) -> bool:
    """Claim and process one job. Returns False when the queue is empty."""
    claimed = await client.claim(worker_id)
    if claimed is None:
        return False
    job, request = claimed["job"], claimed["request"]
    try:
        check_identity(job, request, override or job["runner"], owner_user_id)
        runner = select_runner(job["runner"], override, owner_user_id, attempt_timeout_s)
        try:
            limit = LEASE_CAP_S if time_left_s is None else min(time_left_s, LEASE_CAP_S)
            async with asyncio.timeout(limit):
                result = await generate_validated(runner, request)
        except TimeoutError:
            raise DeadlineError("worker time budget exhausted") from None
        await client.submit(job["id"], result)
    except WorkerError as exc:
        log.error("job %s failed: %s", job["id"], exc)
        await client.fail(job["id"], str(exc), exc.retryable)
    except Exception as exc:  # unexpected: report the type only, never raw content
        log.exception("job %s crashed", job["id"])
        await client.fail(job["id"], f"worker error: {type(exc).__name__}", True)
    return True


async def run(max_jobs: int = 10) -> int:
    token = os.environ["INTERNAL_SERVICE_TOKEN"]
    client = JobClient(os.environ["API_BASE_URL"], token)
    worker_id = os.environ.get("GITHUB_RUN_ID") or socket.gethostname()
    attempt_s = float(os.environ.get("WORKER_ATTEMPT_TIMEOUT_S", DEFAULT_ATTEMPT_TIMEOUT_S))
    budget_s = float(os.environ.get("WORKER_BUDGET_S", DEFAULT_BUDGET_S))
    started = time.monotonic()
    done = 0
    try:
        while done < max_jobs:
            left = budget_s - (time.monotonic() - started)
            if left < worst_case_job_s(attempt_s):  # not enough time for a worst-case job
                log.info("stopping: %.0fs left is below the worst-case job time", left)
                break
            if not await process_one(
                client,
                worker_id,
                override=os.environ.get("AGENT_RUNNER") or None,
                owner_user_id=os.environ.get("OWNER_USER_ID") or None,
                attempt_timeout_s=attempt_s,
                time_left_s=left,
            ):
                break
            done += 1
    finally:
        await client.aclose()
    log.info("processed %d job(s)", done)
    return 0


def cli() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper())
    sys.exit(asyncio.run(run(int(os.environ.get("WORKER_MAX_JOBS", "10")))))
