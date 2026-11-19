class WorkerError(Exception):
    """Base class. `retryable` tells the API whether the job may be re-queued."""

    retryable = True
    halts_run = False  # True: later jobs in this run would fail the same way, so stop claiming


class OutputInvalidError(WorkerError):
    """Runner output is not valid JSON or fails the BriefResult checks."""


class OwnerOnlyError(WorkerError):
    """The claude_subscription runner refused a job for a non-owner (DESIGN 3.1)."""

    retryable = False


class RunnerUnavailableError(WorkerError):
    """The runner is not implemented or not configured."""

    retryable = False


class OutputExhaustedError(OutputInvalidError):
    """Invalid output after the one allowed retry. Terminal: the job is failed, not re-queued."""

    retryable = False


class IdentityMismatchError(WorkerError):
    """Claimed job and its request name different users. Fail closed."""

    retryable = False


class DeadlineError(WorkerError):
    """The worker ran out of its time budget; the job may be re-queued."""


class LeaseLostError(WorkerError):
    """API answered 409 lease_lost: this attempt no longer owns the job. Terminal for the run:
    nothing more is posted for this job."""

    retryable = False


class ClaudeNotLoggedInError(WorkerError):
    """The Claude CLI has no valid sign-in. A retry cannot succeed; later jobs would fail too."""

    retryable = False
    halts_run = True

    def __init__(self) -> None:
        super().__init__("claude_not_logged_in")


class ClaudeUsageLimitError(WorkerError):
    """The Claude plan's usage limit is reached. Retrying now cannot succeed."""

    retryable = False
    halts_run = True

    def __init__(self) -> None:
        super().__init__("claude_usage_limit")


class ClaudeExitError(OutputInvalidError):
    """The CLI failed for an unclassified reason. Retried once like invalid output."""

    def __init__(self) -> None:
        super().__init__("claude_exit_nonzero")
