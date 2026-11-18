# AGENTS.md — rules for every agent in this repo

Single source of rules for Claude Code, Codex CLI and Antigravity CLI (`agy`).
Design: `docs/DESIGN.md`. Read sections 10 and 10.1 before any work. If the design and this file disagree, stop and ask in `HANDOFFS.md` (`to: Kai`).

## Directory ownership

Change only the directory you own. Everything else: ask its owner in `HANDOFFS.md`.

| Agent | Tool | Branch / worktree | Owns |
|---|---|---|---|
| lead + contracts (A) | Claude Code | `main` (main checkout) | root files, `docs/` (except `agent-memory/`), `contracts/`, `docker-compose.yml`, `Makefile`, `.github/workflows/ci.yml`, tool permission files |
| db (B) | Codex | `agent/db` → `../daily-brief-db` | `db/` |
| api (C) | Codex | `agent/api` → `../daily-brief-api` | `api/` |
| worker (D) | Claude Code | `agent/worker` → `../daily-brief-worker` | `worker/`, `.github/workflows/agent-worker.yml` |
| web (E) | agy | `agent/web` → `../daily-brief-web` | `web/` |
| infra (F) | Codex | `agent/infra` → `../daily-brief-infra` | `infra/`, `.github/workflows/deploy-*.yml` (starts after M1 only) |
| review (G) | Claude Code + Codex | read-only | nothing; writes review reports to its memory log |

In `docker-compose.yml` / `docker-compose.override.yml` each agent may edit only its own service block (db agent: `migrate`; api agent: `api`, `scheduler`; web agent: `web`), including removing its `todo-*` profile once the service builds. Anything else there (the `db` service, volumes, `Makefile`) goes to lead via `HANDOFFS.md`.

## Memory protocol (full rules: DESIGN 10.1)

Shared files live on the `memory` branch, checked out at `../daily-brief-memory`. Use `$BRIEF_MEMORY_DIR` (= `<that worktree>/docs/agent-memory`), never your own worktree's copy.

1. Start of session: read `BOARD.md`, `DECISIONS.md`, `HANDOFFS.md` entries addressed to you, and the logs of agents you depend on. Resolve open handoffs to you before new work.
2. Update your own `BOARD.md` row when you start and stop. Blocked → status `blocked` + a `HANDOFFS.md` entry.
3. Your log is `<agent>.md`: newest entry on top, dated. Shared files are append-only; never rewrite another agent's text.
4. Write facts: commands run and their results. Mark anything unverified as unverified.
5. The `memory` branch is local only; never push it. Lead merges it into `main` by PR at each milestone, after a secret scan and Kai's approval. Commit to the `memory` branch in that worktree in small commits (`git -C ../daily-brief-memory commit ...`). If `index.lock` exists, wait and retry; never delete it.
6. Contract changes (`contracts/`): propose in `HANDOFFS.md`, change only after affected agents agree, record in `DECISIONS.md`.
7. Escalate to Kai (`to: Kai`) and stop that piece of work if it costs money, deviates from DESIGN section 3, touches the Claude plan rule in 3.1, or cannot be undone.

## Local stack (OrbStack / Docker)

Each worktree has its own git-ignored `.env` (copy `.env.example`) with its own compose project and port offset, so stacks run in parallel:

| Worktree | `COMPOSE_PROJECT_NAME` | Offset | `DB_PORT` | `API_PORT` | `WEB_PORT` |
|---|---|---|---|---|---|
| main | `brief-main` | 0 | 5432 | 8000 | 5173 |
| db | `brief-db` | 100 | 5532 | 8100 | 5273 |
| api | `brief-api` | 200 | 5632 | 8200 | 5373 |
| worker | `brief-worker` | 300 | 5732 | 8300 | 5473 |
| web | `brief-web` | 400 | 5832 | 8400 | 5573 |
| infra | `brief-infra` | 500 | 5932 | 8500 | 5673 |

Repo-wide: `make up`, `make down`, `make migrate`, `make seed`, `make test`, `make lint`, `make logs`.

## Commands per directory

| Dir | Install | Test | Lint |
|---|---|---|---|
| `contracts/` | — | — | `npx --yes @redocly/cli@latest lint contracts/openapi.yaml` |
| `api/`, `db/`, `worker/` | `uv sync` | `uv run pytest` | `uv run ruff check . && uv run ruff format --check .` |
| `web/` | `npm ci` | `npm test` | `npm run lint && npm run typecheck` |
| `infra/` | `terraform init -backend=false` | `terraform validate` | `terraform fmt -check -recursive` |

Python 3.12+, `uv` only (no pip/poetry). Install dependencies separately in each worktree. Tests that need a database use the compose `db` service or a throwaway container, never a cloud database.

## Hard rules (apply even if your tool runs without a sandbox or approvals)

- Never push to `main`, never force-push, never rewrite published history.
- Only push to your own `agent/<name>` branch; merging into `main` is done by Kai via PR.
- Never run `rm -rf` or delete files outside this repo and `../daily-brief-memory`.
- Never modify `~/.ssh`, `~/.aws`, `~/.codex`, `~/.claude`, `~/.gemini`, or any credentials or `.env` secrets.
- Never run `terraform apply`, `aws ... delete`, or anything else that changes cloud resources.
- When blocked, stop and write to `$BRIEF_MEMORY_DIR/HANDOFFS.md` instead of guessing.

## Never

- Never write secrets, tokens, real email addresses or other personal data into any file, test, fixture, log or memory file. Use placeholders such as `owner@example.com`. Real values come from the git-ignored `.env`.
- Never edit outside your own directory (see ownership table), and never edit `contracts/` without the process above.
- Never deploy, run `terraform apply`, or touch AWS, Supabase, Google or GitHub account settings. Kai does that.
- Never push any branch other than your own `agent/<name>`; never push to `main`, force-push, merge PRs, use `gh` for anything but `gh pr create`/`gh pr view`, or run destructive git commands (`reset --hard`, `clean -fdx`, `branch -D`, `rebase` of shared branches, `worktree remove`).
- Never use "skip all permissions" / full-access modes.
- Never include `docs/` (agent memory included), `.agents/`, `.claude/`, `.codex/` or `.env` in any build artifact: Docker images, Lambda zips, `web/dist`. Each Dockerfile copies only what it needs; keep `.dockerignore` (root and per-service) excluding these.
- Never use `pull_request_target` in workflows.
- Never let the `claude_subscription` runner process a job for anyone but the configured owner (DESIGN 3.1).

## When you stop

Update your `BOARD.md` row and your log, then report: what you did, how you verified it (commands + results), and what is not done or not verified. Commit to your own `agent/<name>` branch, push only that branch (`git push -u origin agent/<name>`), and open a pull request into `main` with `gh pr create`. Never merge; Kai merges after a cross-vendor review (DESIGN 10.2). Set your BOARD status to `done` with the PR link.
