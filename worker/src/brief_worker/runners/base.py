from __future__ import annotations

from typing import Any, Protocol

# BriefRequest / BriefResult are plain dicts shaped by contracts/openapi.yaml and
# contracts/brief-result.schema.json, so the contract stays the single source of truth.
BriefRequest = dict[str, Any]
BriefResult = dict[str, Any]


class AgentRunner(Protocol):
    async def run(self, request: BriefRequest) -> BriefResult: ...
