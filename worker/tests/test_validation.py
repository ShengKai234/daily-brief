import copy

import pytest

from brief_worker.errors import OutputInvalidError
from brief_worker.runners.stub import StubRunner
from brief_worker.validation import validate_result


async def valid(req):
    return await StubRunner().run(req)


async def test_stub_output_is_valid(request_dict):
    validate_result(await valid(request_dict), request_dict)


async def test_insufficient_news_may_be_empty(request_dict):
    tid = request_dict["topics"][1]["topic_id"]
    res = await StubRunner(insufficient_topic_ids=frozenset({tid})).run(request_dict)
    validate_result(res, request_dict)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["sections"][0]["items"][0].__setitem__("source_url", "javascript:alert(1)"),
        lambda r: r["sections"][0]["items"][0].__setitem__("source_url", "ftp://example.com/x"),
        lambda r: r["sections"][0]["items"][0].__setitem__("source_url", "https://"),
        lambda r: r["sections"][0]["items"][0].__setitem__("summary", "x" * 601),
        lambda r: r["sections"][0]["items"][0].__setitem__("title", ""),
        lambda r: r["sections"][0].__setitem__("title", "t" * 201),
        lambda r: r["sections"][0].__setitem__("items", []),  # ok needs >= 1 item
        lambda r: r["sections"][0]["items"][0].__setitem__("published_at", "yesterday"),
        lambda r: r["sections"][0]["items"][0].__setitem__("rank", 2),
        lambda r: r["sections"][0]["items"][0].__setitem__("extra", 1),
        lambda r: r["sections"][0].__setitem__("topic_id", "33333333-3333-4333-8333-333333333333"),
        lambda r: r["sections"].pop(),  # missing topic
        lambda r: r["sections"][1]["items"].append(dict(r["sections"][1]["items"][0], rank=2)),
        lambda r: r.__setitem__("report_date", "2026-11-13"),
        lambda r: r.__setitem__("locale", "en"),
        lambda r: r.pop("usage"),
        lambda r: r["usage"].__setitem__("runner", "other"),
    ],
)
async def test_invalid_output_is_blocked(request_dict, mutate):
    res = copy.deepcopy(await valid(request_dict))
    mutate(res)
    with pytest.raises(OutputInvalidError):
        validate_result(res, request_dict)


def test_non_object_is_blocked(request_dict):
    with pytest.raises(OutputInvalidError):
        validate_result(["x"], request_dict)
