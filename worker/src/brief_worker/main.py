from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys

from .client import JobClient
from .errors import WorkerError
from .pipeline import generate_validated
from .runners.anthropic_api import AnthropicApiRunner
from .runners.base import AgentRunner
from .runners.claude_subscription import ClaudeSubscriptionRunner
from .runners.stub import StubRunner

log = logging.getLogger("brief_worker")


def select_runner(job_runner: str, override: str | None, owner_user_id: str | None) -> AgentRunner:
    """AGENT_RUNNER=stub (local/tests) wins; otherwise the job's own runner is used."""
    name = override or job_runner
    if name == "stub":
        return StubRunner()
    if name == "claude_subscription":
        return ClaudeSubscriptionRunner(owner_user_id)
    if name == "anthropic_api":
        return AnthropicApiRunner()
    raise ValueError(f"unknown runner {name!r}")


async def process_one(
    client: JobClient, worker_id: str, *, override: str | None, owner_user_id: str | None
) -> bool:
    """Claim and process one job. Returns False when the queue is empty."""
    claimed = await client.claim(worker_id)
    if claimed is None:
        return False
    job, request = claimed["job"], claimed["request"]
    try:
        runner = select_runner(job["runner"], override, owner_user_id)
        result = await generate_validated(runner, request)
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
    done = 0
    try:
        while done < max_jobs and await process_one(
            client,
            worker_id,
            override=os.environ.get("AGENT_RUNNER") or None,
            owner_user_id=os.environ.get("OWNER_USER_ID") or None,
        ):
            done += 1
    finally:
        await client.aclose()
    log.info("processed %d job(s)", done)
    return 0


def cli() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper())
    sys.exit(asyncio.run(run(int(os.environ.get("WORKER_MAX_JOBS", "10")))))
