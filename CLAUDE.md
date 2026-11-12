@AGENTS.md

## Claude Code only

- The Claude Pro plan also runs the product's daily worker. Keep usage low: plan, write contracts, review. Do not spawn subagents for work assigned to Codex or agy (DESIGN 10.2).
- Agent definitions are in `.claude/agents/`: `contracts`, `worker`, `reviewer`. They run as top-level sessions in their own worktree, not as temporary worktrees:
  `claude --agent worker --add-dir "$BRIEF_MEMORY_DIR"` (from `../daily-brief-worker`).
- Use `--add-dir "$BRIEF_MEMORY_DIR"` so the shared memory files are writable.
