from __future__ import annotations

import logging

from .errors import OutputInvalidError, WorkerError
from .runners.base import AgentRunner, BriefRequest, BriefResult
from .validation import validate_result

log = logging.getLogger("brief_worker")
ATTEMPTS = 2  # first try plus one retry (DESIGN 7)


async def generate_validated(runner: AgentRunner, request: BriefRequest) -> BriefResult:
    """Run and validate. Invalid output is retried once, then OutputInvalidError is raised.
    Other WorkerErrors (e.g. owner-only refusal) are never retried."""
    last: OutputInvalidError | str = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return validate_result(await runner.run(request), request)
        except OutputInvalidError as exc:
            last = exc
            log.warning("invalid output on attempt %d: %s", attempt, exc)
        except WorkerError:
            raise
    raise OutputInvalidError(f"invalid output after {ATTEMPTS} attempts: {last}")
