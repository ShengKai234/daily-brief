import json

import pytest

from brief_worker.errors import OwnerOnlyError
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
