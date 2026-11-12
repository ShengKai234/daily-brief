from __future__ import annotations

from typing import Any

import httpx

from .errors import LeaseLostError


class JobClient:
    """Thin client for /internal/jobs/*. The token is sent only in X-Internal-Token."""

    def __init__(self, base_url: str, token: str, *, http: httpx.AsyncClient | None = None):
        self._http = http or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers={"X-Internal-Token": token}, timeout=30
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def claim(self, worker_id: str) -> dict[str, Any] | None:
        r = await self._http.post("/api/internal/jobs/claim", json={"worker_id": worker_id})
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _check(r: httpx.Response) -> None:
        if r.status_code == 409:
            try:
                code = r.json().get("code")
            except ValueError:
                code = None
            if code == "lease_lost":
                raise LeaseLostError("lease lost: attempt no longer owns the job")
        r.raise_for_status()

    async def submit(self, job_id: str, attempt: int, result: dict[str, Any]) -> str:
        """`attempt` is job.attempts from the claim response (lease fencing)."""
        r = await self._http.post(
            f"/api/internal/jobs/{job_id}/result", params={"attempt": attempt}, json=result
        )
        self._check(r)
        return r.json()["report_id"]

    async def fail(self, job_id: str, attempt: int, error: str, retryable: bool) -> None:
        r = await self._http.post(
            f"/api/internal/jobs/{job_id}/fail",
            params={"attempt": attempt},
            json={"error": error[:2000], "retryable": retryable},
        )
        self._check(r)
