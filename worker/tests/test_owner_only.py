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
