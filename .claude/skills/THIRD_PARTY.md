# Third-party skills

Vendored from [michael-denyer/pstack-claude](https://github.com/michael-denyer/pstack-claude) (Claude Code port of Lauren Tan's pstack, originally in cursor/plugins), MIT license (`PSTACK-LICENSE`), commit `f1209c06a3fdebd7856dda467f8871dfb2ddc1ed`, copied unchanged on 2026-11-18 at Kai's request.

| Path | Upstream |
|---|---|
| `create-verification-skill/` | `plugins/pstack/skills/create-verification-skill/` |
| `maintain-verification-skill/` | `plugins/pstack/skills/maintain-verification-skill/` |
| `poteto-mode/references/codex-tools.md`, `agy-tools.md` | platform mappings the two skills link to (no `poteto-mode` skill is installed) |

Only these two skills are vendored: no plugin hooks, no other pstack skills or subagents. To update, re-copy the same paths from a newer upstream commit, review the diff, and change the commit here.
