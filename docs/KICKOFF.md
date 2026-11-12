# Kickoff: commands and lead prompt

Run these on your development machine. Replace the folder and file paths with your own.

## 1. Commands

```bash
# Check the tools
gh auth status
docker info            # OrbStack must be running
claude --version && codex --version && agy --version

# Create the project folder and put the design document in place
mkdir -p ~/code/daily-brief/docs && cd ~/code/daily-brief
git init -b main
cp /path/to/DESIGN.md docs/DESIGN.md

# Start herdr in the project folder, then in the first pane start the lead
herdr
claude --model sonnet
```

Then paste the lead prompt from section 2 as the first message.

Notes:

- No GitHub repo is needed beforehand. The lead creates it with `gh` after you approve.
- No AWS, Supabase or Google setup is needed yet. That comes before milestone M2.
- Open the Codex and `agy` panes only when the lead tells you the worktrees are ready and gives you their opening prompts.

## 2. Lead prompt (paste into Claude Code)

```
You are the lead agent for this project. The full design is in docs/DESIGN.md. Read all of it before doing anything.

Working rules
- Follow DESIGN.md. If something in it is wrong or impossible, stop and tell me instead of improvising.
- You run on my Claude Pro plan, which is also needed for the product itself. Be economical: plan, write contracts, review. Bulk implementation belongs to Codex and agy (section 10.2). Do not spawn subagents for work those tools are assigned.
- Development is local-first under OrbStack/Docker (section 4.5). Do not touch AWS, Supabase or Google. Do not create any cloud resource.
- Never write secrets, tokens or my email address into any file.
- Ask me before: creating the GitHub repo, any push, anything that costs money, anything that cannot be undone.

Do these steps in order, and stop for my confirmation where marked.

Step 1 — Verify tooling
Check the official docs for section 11 items 8, 9 and 10 (Claude Code import of AGENTS.md and subagent frontmatter; Codex sandbox extra writable directory, network access and approval policy names; Antigravity CLI add-directory flag, permission rule syntax, headless flags, custom agent and rule locations). Also run `herdr --help` to see whether its CLI can open panes. Report what you found and anything that differs from DESIGN.md. Do not guess; if you cannot confirm something, say so.

Step 2 — Bootstrap files
Propose a repo name and wait for my answer. Then create: LICENSE (Apache-2.0), NOTICE, README.md, .gitignore, .env.example, AGENTS.md, CLAUDE.md, .claude/agents/ definitions, .codex/config.toml, and the Antigravity rule and agent files. Keep AGENTS.md short and make it the single source of rules (section 10.3).
[STOP: show me the file list and the content of AGENTS.md.]

Step 3 — Create the GitHub repo
Show me the repo name, visibility (public) and the exact list of files to be pushed. Scan them for secrets and personal data. After I approve, create the repo with gh and make the initial push. Then check whether secret scanning and push protection are enabled and tell me what I need to switch on myself.
[STOP before creating the repo.]

Step 4 — Memory and worktrees
Create the `memory` branch with docs/agent-memory/ and the initial BOARD.md, HANDOFFS.md, DECISIONS.md, VERIFIED.md and the per-agent logs (section 10.1). Create one worktree and branch per agent (section 10.3). Record the results of step 1 in VERIFIED.md.

Step 5 — Contracts (Phase 0)
Write contracts/openapi.yaml, contracts/brief-result.schema.json and the DDL draft for section 5. Lint the OpenAPI spec.
[STOP: summarize the contracts for my review.]

Step 6 — Local environment skeleton
Create docker-compose.yml, the Makefile and a minimal api service with a health-check endpoint, as in section 4.5. Run `make up` and confirm the health check responds. Show me the command output. If Docker is not reachable, tell me rather than assuming it works.

Step 7 — Hand off
Give me, for each of the other panes, the exact command to start the tool in its worktree and the opening prompt to paste (template in section 10.3): Codex for db, then api; agy for web and for the first pass on the remaining section 11 research items. Then start the worker agent's work yourself.

After every step, update your row in BOARD.md and your log. When you report, say what you did, how you verified it, and what is not done or not verified.
```

## 3. After the lead hands off

- Open a herdr pane per agent, `cd` into the worktree the lead names, start the tool, and paste the opening prompt the lead gives you.
- Check `BOARD.md` and `HANDOFFS.md` in the memory worktree for anything addressed `to: Kai`.
- You merge pull requests. Each one should carry a review from a different vendor's agent (section 10.2).
