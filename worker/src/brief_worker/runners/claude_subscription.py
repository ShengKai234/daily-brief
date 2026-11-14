"""Runs Claude Code headless on the owner's plan (DESIGN 3.1, 7).

Hard rule: only the configured owner's jobs. The check is inside `run`, so no caller can skip it.
Do not run this against the real plan in tests; use StubRunner.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time

from ..errors import OutputInvalidError, OwnerOnlyError
from ..prompt import SYSTEM_PROMPT, build_user_prompt
from ..validation import cli_schema
from .base import BriefRequest, BriefResult

ALLOWED_TOOLS = ("WebSearch", "WebFetch")
# Only these variables reach the claude process; INTERNAL_SERVICE_TOKEN and others stay out.
# USER is needed for the macOS keychain login (without it the CLI answers "Not logged in").
PASS_ENV = ("PATH", "HOME", "USER", "LANG", "TMPDIR", "CLAUDE_CODE_OAUTH_TOKEN")


class ClaudeSubscriptionRunner:
    def __init__(
        self,
        owner_user_id: str | None,
        *,
        model: str = "sonnet",
        timeout_s: float = 600,
        claude_bin: str = "claude",
    ) -> None:
        self.owner_user_id = owner_user_id
        self.model = model
        self.timeout_s = timeout_s
        self.claude_bin = claude_bin

    def check_owner(self, request: BriefRequest) -> None:
        # Fail closed: no configured owner means nobody is allowed.
        if not self.owner_user_id or request.get("user_id") != self.owner_user_id:
            raise OwnerOnlyError("claude_subscription runner is restricted to the owner")

    def build_command(self) -> list[str]:
        tools = ",".join(ALLOWED_TOOLS)
        return [
            self.claude_bin,
            "-p",
            "--model",
            self.model,
            "--output-format",
            "stream-json",
            "--verbose",  # required by stream-json; gives the tool_use events we count
            "--json-schema",
            json.dumps(cli_schema()),
            "--tools",
            tools,
            "--allowedTools",
            *ALLOWED_TOOLS,
            "--permission-mode",
            "dontAsk",
            "--permission-prompts",
            "none",
            "--strict-mcp-config",
            "--system-prompt",
            SYSTEM_PROMPT,
        ]

    def build_env(self) -> dict[str, str]:
        return {k: os.environ[k] for k in PASS_ENV if k in os.environ}

    async def run(self, request: BriefRequest) -> BriefResult:
        self.check_owner(request)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="brief-run-") as cwd:  # empty dir: nothing to read
            proc = await asyncio.create_subprocess_exec(
                *self.build_command(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=self.build_env(),
            )
            try:
                out, _ = await asyncio.wait_for(
                    proc.communicate(build_user_prompt(request).encode()), self.timeout_s
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise OutputInvalidError("claude run timed out") from None
        if proc.returncode != 0:
            raise OutputInvalidError(f"claude exited with code {proc.returncode}")
        return self.parse_output(
            out.decode(errors="replace"), int((time.monotonic() - started) * 1000)
        )

    def parse_output(self, raw: str, duration_ms: int) -> BriefResult:
        """Parse `claude -p --output-format stream-json --verbose` (JSON lines), or a single
        `--output-format json` envelope. Field names are unverified against a real run
        (VERIFIED 5); see HANDOFFS 2026-11-14."""
        envelope, tool_uses = _split_output(raw)
        if envelope.get("is_error"):
            raise OutputInvalidError("claude reported an error")
        result = envelope.get("structured_output")
        if result is None:
            text = envelope.get("result")
            try:
                result = json.loads(text) if isinstance(text, str) else None
            except json.JSONDecodeError:
                result = None
        if not isinstance(result, dict):
            raise OutputInvalidError("no JSON object in claude output")
        usage = envelope.get("usage") or {}
        model_usage = envelope.get("modelUsage")
        per_model = list(model_usage.values()) if isinstance(model_usage, dict) else []
        per_model = [m for m in per_model if isinstance(m, dict)]
        searches = (
            tool_uses.get("WebSearch", 0)
            if tool_uses is not None
            else _search_count(usage, per_model)
        )
        fetches = tool_uses.get("WebFetch", 0) if tool_uses is not None else 0
        sections = result.get("sections")
        if (
            searches == 0
            and fetches == 0
            and isinstance(sections, list)
            and sections
            and all(
                isinstance(x, dict) and x.get("status") == "insufficient_news" for x in sections
            )
        ):
            # The model skipped research and declared everything empty. Invalid, so it is retried.
            raise OutputInvalidError("all sections insufficient_news and no search was done")
        result["usage"] = {
            "runner": "claude_subscription",
            "model": self.model,
            "duration_ms": duration_ms,
            "search_count": searches,
            "input_tokens": _tokens(usage.get("input_tokens"), per_model, "inputTokens"),
            "output_tokens": _tokens(usage.get("output_tokens"), per_model, "outputTokens"),
        }
        return result


def _split_output(raw: str) -> tuple[dict, dict[str, int] | None]:
    """Return (final result envelope, tool_use counts by tool name or None if not a stream)."""
    try:
        single = json.loads(raw)
    except json.JSONDecodeError:
        single = None
    if isinstance(single, dict) and single.get("type") != "assistant":
        return single, None
    objs = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            raise OutputInvalidError("claude output is not JSON lines") from None
        if isinstance(obj, dict):
            objs.append(obj)
    envelope = next((o for o in reversed(objs) if o.get("type") == "result"), None)
    if envelope is None:
        raise OutputInvalidError("claude output has no result message")
    counts: dict[str, int] = {}
    for obj in objs:
        message = obj.get("message")
        if obj.get("type") != "assistant" or not isinstance(message, dict):
            continue
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name")
                if isinstance(name, str):
                    counts[name] = counts.get(name, 0) + 1
    return envelope, counts


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _search_count(usage: dict, per_model: list[dict]) -> int:
    """Web searches used. Field names are from memory of the CLI output (unverified, VERIFIED 5):
    `usage.server_tool_use.web_search_requests` and `modelUsage.<model>.webSearchRequests`.
    The top-level usage may only cover the last turn, so take the larger of the two sources."""
    tool_use = usage.get("server_tool_use")
    top = _int(tool_use.get("web_search_requests")) if isinstance(tool_use, dict) else 0
    by_model = sum(_int(m.get("webSearchRequests")) for m in per_model)
    return max(top, by_model)


def _tokens(top: object, per_model: list[dict], key: str) -> int | None:
    by_model = sum(_int(m.get(key)) for m in per_model)
    value = max(_int(top), by_model)
    return value if (top is not None or per_model) else None
