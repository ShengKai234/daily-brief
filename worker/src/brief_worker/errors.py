class WorkerError(Exception):
    """Base class. `retryable` tells the API whether the job may be re-queued."""

    retryable = True


class OutputInvalidError(WorkerError):
    """Runner output is not valid JSON or fails the BriefResult checks."""


class OwnerOnlyError(WorkerError):
    """The claude_subscription runner refused a job for a non-owner (DESIGN 3.1)."""

    retryable = False


class RunnerUnavailableError(WorkerError):
    """The runner is not implemented or not configured."""

    retryable = False
