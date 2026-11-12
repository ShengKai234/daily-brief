import pytest

from brief_worker.errors import OutputExhaustedError
from brief_worker.pipeline import generate_validated
from brief_worker.runners.stub import StubRunner


class Flaky:
    def __init__(self, bad_times):
        self.bad_times, self.calls, self.inner = bad_times, 0, StubRunner()

    async def run(self, request):
        self.calls += 1
        res = await self.inner.run(request)
        if self.calls <= self.bad_times:
            res["sections"][0]["items"][0]["source_url"] = "file:///etc/passwd"
        return res


async def test_retry_once_then_succeed(request_dict):
    runner = Flaky(1)
    await generate_validated(runner, request_dict)
    assert runner.calls == 2


async def test_fails_after_one_retry(request_dict):
    runner = Flaky(5)
    with pytest.raises(OutputExhaustedError) as exc:
        await generate_validated(runner, request_dict)
    assert exc.value.retryable is False
    assert runner.calls == 2
