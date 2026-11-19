from __future__ import annotations

from ..errors import RunnerUnavailableError
from .base import BriefRequest, BriefResult


class AnthropicApiRunner:
    """Placeholder for the pay-as-you-go runner (DESIGN 7). Must be completed before a second
    user is onboarded (DESIGN 3.1). Until then it refuses every job."""

    async def run(self, request: BriefRequest) -> BriefResult:
        raise RunnerUnavailableError("anthropic_api runner is not implemented yet")
