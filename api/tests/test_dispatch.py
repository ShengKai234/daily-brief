import secrets
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import Settings
from app.dispatch import GitHubDispatcher


@pytest.mark.parametrize("status", [200, 204])
async def test_github_dispatch_request(status):
    settings = Settings(
        github_repository="example/brief", github_dispatch_token=secrets.token_urlsafe(32)
    )
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.github.com"))
    post = AsyncMock(return_value=response)
    with patch("app.dispatch.httpx.AsyncClient.post", post):
        await GitHubDispatcher(settings).dispatch()
    args, kwargs = post.call_args
    assert (
        args[0]
        == "https://api.github.com/repos/example/brief/actions/workflows/agent-worker.yml/dispatches"
    )
    assert kwargs["json"] == {"ref": "main"}
    assert kwargs["headers"]["X-GitHub-Api-Version"] == "2026-03-10"
    assert kwargs["headers"]["Authorization"] == "Bearer " + settings.github_dispatch_token


async def test_github_dispatch_failure_does_not_echo_credentials():
    settings = Settings(
        github_repository="example/brief", github_dispatch_token=secrets.token_urlsafe(32)
    )
    response = httpx.Response(
        403,
        text=settings.github_dispatch_token,
        request=httpx.Request("POST", "https://api.github.com"),
    )
    with patch("app.dispatch.httpx.AsyncClient.post", AsyncMock(return_value=response)):
        with pytest.raises(RuntimeError, match="^Worker dispatch failed$"):
            await GitHubDispatcher(settings).dispatch()
