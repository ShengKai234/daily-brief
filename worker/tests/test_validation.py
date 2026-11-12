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
        lambda r: r["sections"][0]["items"][0].__setitem__("rank", 0),
        lambda r: r["sections"][0]["items"][2].__setitem__("rank", 4),  # gap
        lambda r: r["sections"][0]["items"][1].__setitem__("rank", 1),  # duplicate
        lambda r: r["sections"][0]["items"].reverse(),  # ranks 3,2,1 by position
        lambda r: r["sections"][0]["items"][0].__setitem__("rank", 11),
        lambda r: r["sections"][0]["items"][0].__setitem__("rank", "1"),
        lambda r: r["sections"][0]["items"][0].__setitem__("published_at", "2026-11-12"),
        lambda r: r["sections"][0]["items"][0].__setitem__("published_at", "2026-11-12T07:00:00"),
        lambda r: r["sections"][0]["items"][0].__setitem__("published_at", "2026-11-12 07:00:00Z"),
        lambda r: r["sections"][0]["items"][0].__setitem__("published_at", "2026-13-40T07:00:00Z"),
        lambda r: r["sections"][0]["items"][0].__setitem__("title", "t" * 201),
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


@pytest.mark.parametrize(
    "stamp", ["2026-11-12T07:00:00Z", "2026-11-12T07:00:00+08:00", "2026-11-12T07:00:00.5-05:00"]
)
async def test_rfc3339_with_offset_is_accepted(request_dict, stamp):
    res = await valid(request_dict)
    res["sections"][0]["items"][0]["published_at"] = stamp
    validate_result(res, request_dict)


async def test_error_message_has_no_report_content(request_dict):
    res = await valid(request_dict)
    res["sections"][0]["items"][0]["source_url"] = "javascript:SECRET-CONTENT"
    res["sections"][0]["items"][0]["summary"] = "SECRET-SUMMARY" * 100
    res["sections"][0]["topic_id"] = "SECRET-TOPIC"
    with pytest.raises(OutputInvalidError) as exc:
        validate_result(res, request_dict)
    assert "SECRET" not in str(exc.value)
    assert "sections/0/items/0/source_url: pattern" in str(exc.value)


async def test_stub_titles_stay_within_limit(request_dict):
    request_dict["topics"][0]["title"] = "T" * 200
    res = await valid(request_dict)
    validate_result(res, request_dict)
