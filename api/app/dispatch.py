import asyncio
import shlex
from typing import Protocol

import httpx


class WorkerDispatcher(Protocol):
    async def dispatch(self) -> None: ...


class GitHubDispatcher:
    def __init__(self, settings):
        self.settings = settings

    async def dispatch(self):
        if not self.settings.github_dispatch_token or not self.settings.github_repository:
            raise ValueError("GitHub dispatcher is not configured")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"https://api.github.com/repos/{self.settings.github_repository}"
                "/actions/workflows/agent-worker.yml/dispatches",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.settings.github_dispatch_token}",
                    "X-GitHub-Api-Version": "2026-03-10",
                },
                json={"ref": "main"},
            )
        if response.status_code not in {200, 204}:
            # Never include remote bodies or request headers in exceptions/logs.
            raise RuntimeError("Worker dispatch failed")


class LocalDispatcher:
    def __init__(self, settings, queue):
        self.settings = settings
        self.queue = queue

    async def dispatch(self):
        if self.settings.app_env != "local":
            raise ValueError("Local dispatch requires APP_ENV=local")
        if self.settings.local_worker_command:
            # Operator-controlled command, no shell interpolation or request data.
            process = await asyncio.create_subprocess_exec(
                *shlex.split(self.settings.local_worker_command),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=1800)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise RuntimeError("Local worker timed out") from None
            if process.returncode:
                raise RuntimeError("Local worker failed")
        elif self.settings.agent_runner == "stub":
            await self.stub()
        else:
            raise ValueError("LOCAL_WORKER_COMMAND is required for a real local worker")

    async def stub(self):
        """Deterministic local adapter; never invokes a model or consumes quota."""
        while claimed := await self.queue.claim("local-stub"):
            request = claimed["request"]
            result = {
                "report_date": request["report_date"].isoformat(),
                "locale": request["locale"],
                "sections": [
                    {
                        "topic_id": str(topic["topic_id"]),
                        "title": topic["title"],
                        "status": "ok",
                        "items": [
                            {
                                "rank": 1,
                                "title": "Local demonstration",
                                "summary": "A deterministic local fixture for this topic.",
                                "source_name": "Example",
                                "source_url": "https://example.com/news",
                                "published_at": None,
                            }
                        ],
                    }
                    for topic in request["topics"]
                ],
                "usage": {
                    "runner": "stub",
                    "model": "stub",
                    "duration_ms": 0,
                    "search_count": 0,
                    "input_tokens": None,
                    "output_tokens": None,
                },
            }
            await self.queue.complete(claimed["job"]["id"], result)
