import json

import pytest

from brief_worker.errors import OutputExhaustedError, OutputInvalidError, OwnerOnlyError
from brief_worker.main import select_runner
from brief_worker.pipeline import generate_validated
from brief_worker.runners.claude_subscription import ClaudeSubscriptionRunner

from .conftest import OTHER, OWNER


async def test_non_owner_is_refused_without_spawning(request_dict, monkeypatch):
    spawned = []

    async def boom(*a, **k):
        spawned.append(a)
        raise AssertionError("must not spawn claude")

    monkeypatch.setattr("asyncio.create_subprocess_exec", boom)
    request_dict["user_id"] = OTHER
    runner = ClaudeSubscriptionRunner(OWNER)
    with pytest.raises(OwnerOnlyError) as exc:
        await generate_validated(runner, request_dict)
    assert exc.value.retryable is False
    assert not spawned


async def test_unconfigured_owner_refuses_everyone(request_dict):
    with pytest.raises(OwnerOnlyError):
        await ClaudeSubscriptionRunner(None).run(request_dict)
    with pytest.raises(OwnerOnlyError):
        await ClaudeSubscriptionRunner("").run(request_dict)


def test_owner_passes_check(request_dict):
    ClaudeSubscriptionRunner(OWNER).check_owner(request_dict)


def test_selection_keeps_owner_for_subscription():
    runner = select_runner("claude_subscription", None, OWNER)
    assert isinstance(runner, ClaudeSubscriptionRunner) and runner.owner_user_id == OWNER


def test_command_has_only_search_and_read_tools(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    runner = ClaudeSubscriptionRunner(OWNER)
    cmd = runner.build_command()
    assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch"
    flags = cmd[: cmd.index("--system-prompt")]
    assert not {"Bash", "Write", "Edit"} & set(flags)
    assert "dontAsk" in cmd
    env = runner.build_env()
    assert "INTERNAL_SERVICE_TOKEN" not in env and env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"


def _walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_cli_schema_is_cli_compatible_and_derived_from_contract():
    from brief_worker.validation import cli_schema, load_schema

    schema = cli_schema()
    banned = {"$schema", "$id", "$comment", "prefixItems", "if", "then", "else"}
    found = {k for n in _walk(schema) if isinstance(n, dict) for k in n} & banned
    assert not found
    assert schema["$defs"]["Section"]["properties"]["items"]["items"] == {"$ref": "#/$defs/Item"}
    # still carries the real constraints
    assert schema["$defs"]["Item"] == load_schema()["$defs"]["Item"]
    assert schema["required"] == load_schema()["required"]
    # the contract schema itself is untouched (validation keeps using the full one)
    assert "prefixItems" in {k for n in _walk(load_schema()) if isinstance(n, dict) for k in n}


def test_command_sends_cli_schema_not_contract_schema():
    from brief_worker.validation import cli_schema

    cmd = ClaudeSubscriptionRunner(OWNER).build_command()
    sent = json.loads(cmd[cmd.index("--json-schema") + 1])
    assert sent == cli_schema()
    keys = {k for n in _walk(sent) if isinstance(n, dict) for k in n}
    assert not keys & {
        "$schema",
        "prefixItems",
        "if",
        "then",
    }  # keys only; descriptions may mention them


def test_env_passes_user_and_nothing_else(monkeypatch):
    for k in ("PATH", "HOME", "USER", "LANG", "TMPDIR", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.setenv(k, "x")
    for k in ("INTERNAL_SERVICE_TOKEN", "OWNER_USER_ID", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN"):
        monkeypatch.setenv(k, "leak")
    env = ClaudeSubscriptionRunner(OWNER).build_env()
    assert set(env) == {"PATH", "HOME", "USER", "LANG", "TMPDIR", "CLAUDE_CODE_OAUTH_TOKEN"}


def _envelope(**extra):
    return json.dumps({"is_error": False, "structured_output": {"sections": []}, **extra})


def test_search_count_from_server_tool_use():
    r = ClaudeSubscriptionRunner(OWNER)
    out = r.parse_output(
        _envelope(
            usage={
                "input_tokens": 5,
                "output_tokens": 7,
                "server_tool_use": {"web_search_requests": 4},
            }
        ),
        10,
    )
    assert out["usage"]["search_count"] == 4


def test_search_count_from_model_usage_when_top_level_misses_it():
    r = ClaudeSubscriptionRunner(OWNER)
    out = r.parse_output(
        _envelope(
            usage={"input_tokens": 1, "output_tokens": 2},
            modelUsage={
                "claude-sonnet": {"inputTokens": 900, "outputTokens": 300, "webSearchRequests": 6}
            },
        ),
        10,
    )
    assert out["usage"]["search_count"] == 6
    assert out["usage"]["input_tokens"] == 900 and out["usage"]["output_tokens"] == 300


@pytest.mark.parametrize("bad", [None, "x", -3, True, 2.5])
def test_search_count_ignores_garbage(bad):
    r = ClaudeSubscriptionRunner(OWNER)
    out = r.parse_output(_envelope(usage={"server_tool_use": {"web_search_requests": bad}}), 1)
    assert out["usage"]["search_count"] == 0


def test_missing_usage_gives_null_tokens_and_zero_searches():
    out = ClaudeSubscriptionRunner(OWNER).parse_output(_envelope(), 1)
    assert out["usage"]["search_count"] == 0
    assert out["usage"]["input_tokens"] is None and out["usage"]["output_tokens"] is None


@pytest.mark.parametrize(
    "phrase",
    [
        "within the last 48 hours",
        "RFC 3339",
        "not a homepage, section, category, tag, index or search-results page",
        "publisher of that exact URL",
        "Do not take all items of a topic from one roundup, digest or newsletter page",
    ],
)
def test_prompt_has_quality_rules(phrase):
    from brief_worker.prompt import SYSTEM_PROMPT

    assert phrase in " ".join(SYSTEM_PROMPT.split())


def _stream(tools=(), sections=None, **result_extra):
    """JSON lines shaped like `claude -p --output-format stream-json --verbose` (unverified)."""
    lines = [{"type": "system", "subtype": "init"}]
    for name in tools:
        lines.append(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "x"},
                        {"type": "tool_use", "name": name, "input": {}},
                    ]
                },
            }
        )
        lines.append({"type": "user", "message": {"content": []}})
    lines.append(
        {
            "type": "result",
            "is_error": False,
            "structured_output": {"sections": sections if sections is not None else []},
            "usage": {"input_tokens": 3, "output_tokens": 4},
            **result_extra,
        }
    )
    return "\n".join(json.dumps(x) for x in lines)


INSUFFICIENT = [{"topic_id": "t", "title": "T", "status": "insufficient_news", "items": []}]
OK_SECTION = [{"topic_id": "t", "title": "T", "status": "ok", "items": [{}]}]


def test_command_uses_stream_json_with_verbose():
    cmd = ClaudeSubscriptionRunner(OWNER).build_command()
    assert cmd[cmd.index("--output-format") + 1] == "stream-json" and "--verbose" in cmd


def test_search_count_counts_websearch_tool_uses():
    out = ClaudeSubscriptionRunner(OWNER).parse_output(
        _stream(["WebSearch", "WebFetch", "WebSearch", "WebSearch"], OK_SECTION), 5
    )
    assert out["usage"]["search_count"] == 3
    assert out["usage"]["input_tokens"] == 3 and out["usage"]["output_tokens"] == 4


def test_stream_ignores_other_tools_and_garbage_blocks():
    raw = _stream(["Bash"], OK_SECTION)
    assert ClaudeSubscriptionRunner(OWNER).parse_output(raw, 1)["usage"]["search_count"] == 0


def test_all_insufficient_without_any_search_is_invalid():
    with pytest.raises(OutputInvalidError):
        ClaudeSubscriptionRunner(OWNER).parse_output(_stream([], INSUFFICIENT), 1)
    with pytest.raises(OutputInvalidError):  # single-envelope form too
        ClaudeSubscriptionRunner(OWNER).parse_output(
            json.dumps({"is_error": False, "structured_output": {"sections": INSUFFICIENT}}), 1
        )


def test_all_insufficient_after_searching_is_valid():
    out = ClaudeSubscriptionRunner(OWNER).parse_output(
        _stream(["WebSearch", "WebSearch"], INSUFFICIENT), 1
    )
    assert out["usage"]["search_count"] == 2


def test_ok_section_without_search_count_is_not_blocked_by_this_rule():
    ClaudeSubscriptionRunner(OWNER).parse_output(_stream([], OK_SECTION), 1)


def test_stream_without_result_message_is_invalid():
    with pytest.raises(OutputInvalidError):
        ClaudeSubscriptionRunner(OWNER).parse_output('{"type":"assistant","message":{}}', 1)
    with pytest.raises(OutputInvalidError):
        ClaudeSubscriptionRunner(OWNER).parse_output("not json\nat all", 1)


async def test_skipped_search_triggers_the_one_retry(request_dict, monkeypatch):
    spawned = []

    class FakeProc:
        returncode = 0

        async def communicate(self, data=None):
            return _stream([], INSUFFICIENT).encode(), b""

    async def fake_exec(*a, **k):
        spawned.append(a)
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    with pytest.raises(OutputExhaustedError):
        await generate_validated(ClaudeSubscriptionRunner(OWNER), request_dict)
    assert len(spawned) == 2


def test_prompt_requires_search_per_topic():
    from brief_worker.prompt import SYSTEM_PROMPT

    text = " ".join(SYSTEM_PROMPT.split())
    assert "at least one web search for every topic" in text
    assert "never mark a topic insufficient_news without having searched" in text
