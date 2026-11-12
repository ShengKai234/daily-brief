# Daily Brief — Design and Task Assignment (POC v1)

> Version: 2026-11-12. Owner: Kai.
> Purpose: bring this file to the CLI and assign work to sub agents as described in section 10. For every item marked "to verify", the responsible agent must check official documentation before implementing.

## 1. Goal and scope

At the times a user configures each day, an AI agent searches the news for the topics that user subscribes to, compiles the top N items per topic, and publishes them to a personal page. The user can mark items as read and write notes.

**In scope for the POC (this document)**

- One user: Kai. The agent runs on Kai's personal Claude plan, using a Sonnet model.
- Web app (PWA).
- Google sign-in with restricted registration (email allowlist or invite code).
- Topic subscriptions, item count per topic, delivery slots (at most 3 per user per day).
- Read state, notes, profile.

**Out of scope for the POC, but the design must leave room for them**

- Other users. Once anyone else is onboarded, the agent must run on an API key (see 3.1).
- Native iOS app (Swift). The backend stays a pure API with no web-only assumptions.
- Apple sign-in (added before the app opens to external testers).
- Community discussion and public report browsing.
- Commercialization (billing, quota plans).

## 2. Prerequisites: what Kai prepares before development starts

Sub agents cannot create accounts or approve billing. Store every secret in a password manager; never paste one into the repo, an issue, or an agent memory file.

**Development is local-first (see 4.5).** Only two things are needed to start:

- The local tools in 2.2, with `gh` authenticated (`gh auth status`).
- The three agent CLIs signed in.

The GitHub repo does not exist yet. The lead agent creates it with `gh` during bootstrap (see 10.3). Everything in the 2.1 table from row 3 onward (AWS, Supabase, Google OAuth, subdomain, worker credential) is needed only before M2, the first cloud deployment. Rows 1 and 2 are handled as noted.

### 2.1 Accounts and setup

| # | What | Steps | Output the agents need |
|---|---|---|---|
| 1 | **GitHub repo** | Created by the lead agent with `gh` after Kai confirms the name (see 10.3). Kai then checks that secret scanning and push protection are on and protects the default branch. | Repo name |
| 2 | **GitHub token for workflow dispatch** | Create a fine-grained personal access token limited to this repo with the minimum permission needed to trigger a workflow (scope to verify, section 11 item 6). | Token, stored later in AWS |
| 3 | **AWS (existing personal account)** | Confirm MFA on the root user. Create a deploy identity for Terraform (IAM role or user, not root). Create a budget alert. Pick the region (Tokyo, `ap-northeast-1`, recommended). | Account ID, region, deploy credentials configured locally |
| 4 | **Supabase** | Sign up, enable two-step verification, create one project in the same region as AWS. Save the database password. | Project URL, public key, service role key, database connection string |
| 5 | **Google OAuth** | In Google Cloud Console: create a project, configure the OAuth consent screen (testing mode is enough; add your own Google account as a test user), create an OAuth client of type "Web application". Set the redirect URI to the callback URL shown on Supabase's Google provider page. Then paste the client ID and secret into Supabase's Google provider settings. | Nothing for agents; it lives in Supabase |
| 6 | **Subdomain** | Decide the hostname (for example `brief.<your-domain>`). Make sure you can add DNS records: one CNAME to CloudFront and the validation records for the ACM certificate. | Hostname, DNS provider |
| 7 | **Claude plan credentials for the worker** | Confirm the plan is active. Generate the long-lived credential used for headless Claude Code runs (exact procedure to verify, section 11 item 5). | Credential, stored in GitHub Actions secrets |
| 8 | **Owner identity** | Decide the Google account email that is the owner and the first allowlisted user. | Email, supplied as an environment variable, never committed |

### 2.2 Local tools

Python 3.12 or newer, `uv`, Node.js LTS, Terraform, AWS CLI, GitHub CLI (`gh`), Docker (OrbStack is fine), and the three agent CLIs, each signed in with Kai's own subscription: Claude Code, Codex CLI, Antigravity CLI (`agy`).

### 2.3 Where each secret lives

| Secret | Location |
|---|---|
| Supabase service role key, database connection string | AWS (Lambda environment or Secrets Manager / SSM Parameter Store) |
| Internal service token (worker ↔ API) | AWS and GitHub Actions secrets |
| GitHub dispatch token | AWS |
| Claude plan credential | GitHub Actions secrets |
| Owner email | AWS (environment variable) |
| Supabase public key, project URL | Frontend build configuration (these are public by design) |

### 2.4 Not needed yet

Apple Developer Program membership, an Anthropic API key, any second user account. Do not set these up for the POC.

### 2.5 Costs to expect

The design targets free tiers, but nothing here guarantees zero cost. Free allowances change; the infra agent must confirm current limits (section 11) and the AWS budget alert is the safety net.

## 3. Confirmed decisions

| Item | Decision |
|---|---|
| Backend | Python, FastAPI, async throughout |
| Frontend | React + TypeScript (Vite), PWA |
| Database | Postgres (Supabase free plan) |
| Sign-in | Supabase Auth (Google). The backend only verifies JWTs |
| API runtime | AWS Lambda + Function URL (Kai's existing personal account) |
| Scheduling | EventBridge triggers a Lambda on a fixed interval |
| Queue | Postgres `jobs` table behind an interface (replaceable with SQS) |
| Agent worker | GitHub Actions, triggered only by the API, never by cron |
| Frontend hosting and domain | S3 + CloudFront on Kai's subdomain. The same distribution routes `/api/*` to the Lambda Function URL |
| Repo | Public, single repo |
| License | Apache-2.0 + NOTICE (see 3.2) |
| Architecture style | Modular monolith plus a separate worker; no microservices (see 4.3) |
| Frontend visual design | Clean and minimal first. Style discussion and selection happen only after the basic framework is complete (see 3.3) |
| Development agents | Claude Code (Pro plan), Codex CLI (ChatGPT plan), Antigravity CLI `agy` (Gemini AI Pro). Assignment in 10.2 |

### 3.1 Claude plan usage limit (hard rule)

Anthropic's terms do not allow routing requests through personal plan credentials (Free, Pro, Max) on behalf of other users. Therefore:

- The `claude_subscription` runner may process jobs for the **owner only**. The code must enforce this: if a job's `user_id` is not the configured owner, refuse to run it and mark it failed.
- Before any second user is onboarded, the `anthropic_api` runner (API key, pay as you go) must be complete.
- Reference: https://code.claude.com/docs/en/legal-and-compliance

### 3.2 License

Kai's requirement: others may use the code but must credit him. Apache-2.0 fits: users must retain the copyright notice and the contents of the NOTICE file, and the license includes a patent grant.

- Put `LICENSE` (full Apache-2.0 text) and `NOTICE` (project name, author Kai, repo link) in the repo root.
- Note: a permissive license means others can also use the code commercially. As copyright holder Kai can still commercialize it or relicense later, but relicensing after accepting outside contributions needs the contributors' consent. This is not legal advice; consult a professional before commercializing.

### 3.3 Frontend visual design: minimal first

The first version of the web app is deliberately plain. Visual style is discussed and chosen with Kai only after the basic framework works end to end (after M3).

- System font stack, one neutral palette, light and dark via `prefers-color-scheme`. No brand colors, illustrations, animations or custom icons.
- All colors, spacing, radii and font sizes come from one file of CSS variables (design tokens). Components never hard-code these values, so a later restyle is a token change, not a rewrite.
- Do not adopt a styled component library or a CSS framework with its own look. Plain CSS (or CSS modules) with semantic HTML is enough.
- Spend the effort on structure instead: routing, data loading, error and empty states, forms, accessibility (labels, focus states, keyboard use), and phone-width layout.
- The web agent must not make style decisions beyond this baseline. Style ideas go to `HANDOFFS.md` addressed to Kai.

## 4. Architecture

### 4.1 Components

```
[Browser PWA] ──► [CloudFront: default path → S3 static files; /api/* → Function URL] ──► [Lambda: FastAPI]
                                                                                              │
                                                                                              ▼
                                                                                [Supabase Postgres + Auth]
                                                                                              ▲
[EventBridge every 15 min] ──► [Lambda: scheduler tick] ──────────────────────────────────────┤
                                     │ dispatch only when a job is due                         │
                                     ▼                                                         │
                       [GitHub Actions: agent worker] ── writes back through the internal API ─┘
```

### 4.2 Flow of one report

1. EventBridge invokes the scheduler Lambda every 15 minutes.
2. The scheduler finds slots whose local time has arrived and that have no job yet today, and inserts rows into `jobs` (a unique key prevents duplicates).
3. If new jobs exist, the scheduler calls the GitHub API to trigger the `agent-worker` workflow (`workflow_dispatch`).
4. The worker claims a job through the internal API and receives the topic list and item counts.
5. The worker runs Claude Code headless (Sonnet, search and web-read tools only) and produces output that matches the JSON schema.
6. The worker validates the JSON and posts it to the internal API. In one transaction the API creates the report, sections and items, and marks the job succeeded.
7. On failure the worker reports the error; the job is re-queued or marked failed depending on the attempt count.

Slots use 30-minute granularity. The product promise is "arrives around 07:00", not an exact time.

### 4.3 Why not microservices

With one or two users, free-tier limits and a single developer, microservices only add deployment and operations work. Clear module boundaries keep a later split cheap:

- Modules call each other only through service interfaces; no cross-module table access.
- There are three deployable units: `api` (including the scheduler handler), `worker`, `web`.
- Every replaceable point sits behind an interface: `AgentRunner`, `JobQueue`, `AuthVerifier`, `WorkerDispatcher`.

### 4.4 Replaceability

| Now | Later | What changes |
|---|---|---|
| Kai's subdomain | Production domain | Environment variables, DNS records, CloudFront alias and ACM certificate, OAuth callback URLs |
| Supabase Postgres | RDS, Neon | Connection string (use standard Postgres features only) |
| Supabase Auth | Cognito, Auth0 | `AuthVerifier` implementation and JWKS configuration |
| `jobs` table | SQS | `JobQueue` implementation |
| GitHub Actions worker | Lambda or a long-running worker | `WorkerDispatcher` implementation |
| Claude subscription | Anthropic API key | `AgentRunner` implementation |
| Lambda | Cloud Run or another container platform | Deployment configuration (keep a Dockerfile maintained) |

Rule: **do not use Supabase-specific data access.** The frontend never queries tables directly and business logic never lives in RLS. The frontend uses Supabase only to sign in and obtain a JWT; everything else goes through our API. RLS is still enabled as a second line of defense.

### 4.5 Local development environment (OrbStack, Docker)

Early development runs entirely on Kai's machine in containers under OrbStack. No cloud account is touched until M2. The same interfaces that make the system portable (4.4) are what make it runnable locally.

**`docker-compose.yml` at the repo root**

| Service | Image / build | Purpose |
|---|---|---|
| `db` | Official Postgres image, pinned major version, named volume | Local database. Same engine as production |
| `migrate` | Built from `db/` | Runs Alembic migrations and seed data, then exits |
| `api` | Built from `api/Dockerfile` | FastAPI with auto-reload, source mounted as a volume |
| `scheduler` | Same image as `api`, different command | Calls the scheduler tick on an interval, replacing EventBridge |
| `web` | Built from `web/Dockerfile` | Vite dev server with hot reload, proxying `/api` to `api` |

The worker is not a long-running service. It runs on demand, either in a container with a stub runner or on the host when it needs the real Claude Code CLI and Kai's sign-in.

**Local implementations of the interfaces**

| Interface | Local implementation | Production implementation |
|---|---|---|
| `AuthVerifier` | Dev verifier: accepts tokens signed by a local dev key; a dev sign-in page issues one for the seeded owner | Supabase JWT verification |
| `JobQueue` | Postgres `jobs` table (same as production) | Postgres `jobs` table, later SQS |
| `WorkerDispatcher` | Starts the worker process locally | GitHub `workflow_dispatch` |
| `AgentRunner` | `StubRunner` returning fixed, schema-valid results for tests; the real subscription runner on the host when Kai wants a true end-to-end run | `ClaudeSubscriptionRunner` |

**Rules**

- The implementation is chosen by environment variables. `APP_ENV=local` enables the dev verifier.
- The dev verifier must be impossible to enable in production: the API refuses to start if dev auth is on while `APP_ENV` is not `local`, and a test covers this.
- The `api` Dockerfile is the one used for deployment later. Keep it production-shaped; development conveniences (reload, mounted source) come from compose overrides.
- A `Makefile` gives one-word entry points: `make up`, `make down`, `make migrate`, `make seed`, `make test`, `make lint`, `make logs`.
- `.env.example` lists every variable with placeholder values. The real `.env` is git-ignored.
- Each agent worktree runs its own compose project name and port offsets so parallel stacks do not collide. The lead agent defines the convention in `AGENTS.md`.
- Tests that need a database use the compose `db` or a throwaway container, never a cloud database.

**Definition of "works locally" (M1)**: from a fresh clone, `make up` brings the stack up; the dev sign-in works; a job created by the scheduler is processed by the worker with the stub runner; the report appears in the web app; read state and notes persist across restarts.

## 5. Data model

All tables use UUID primary keys and `timestamptz`, and carry `created_at` and `updated_at`.

| Table | Key columns | Notes |
|---|---|---|
| `profiles` | `user_id` PK, `display_name`, `avatar_url`, `bio`, `timezone` (default Asia/Taipei), `locale` (default zh-TW), `status` (pending / active / disabled) | Maps to the auth user. Community features extend from here |
| `allowed_emails` | `email` PK, `note` | Registration allowlist |
| `invite_codes` | `code` PK, `max_uses`, `used_count`, `expires_at`, `created_by` | Invite codes |
| `invite_redemptions` | `code`, `user_id`, `redeemed_at` | Redemption log |
| `topics` | `id`, `owner_id`, `title`, `normalized_key`, `visibility` (private / public) | Free-text topic entered by the user. `normalized_key` allows shared lookups later |
| `subscriptions` | `id`, `user_id`, `topic_id`, `item_count` (1–10, default 3), `position`, `enabled` | Who subscribes to what, how many items, display order |
| `delivery_slots` | `id`, `user_id`, `local_time` (30-minute steps), `enabled` | At most 3 per user, enforced by both the API and a database constraint |
| `agent_settings` | `user_id` PK, `runner` (claude_subscription / anthropic_api), `model` (default sonnet), `daily_run_limit` (default 3) | Per-user agent settings |
| `jobs` | `id`, `user_id`, `slot_id`, `scheduled_for`, `status` (queued / running / succeeded / failed), `attempts`, `runner`, `locked_at`, `error` | Unique key `(user_id, slot_id, scheduled_for)` |
| `reports` | `id`, `user_id`, `job_id`, `report_date`, `slot_id`, `generated_at`, `visibility` (default private) | One report per slot run |
| `report_sections` | `id`, `report_id`, `topic_id`, `title`, `position` | One topic within a report |
| `report_items` | `id`, `section_id`, `rank`, `title`, `summary`, `source_name`, `source_url`, `published_at` | One news item |
| `item_marks` | PK `(user_id, item_id)`, `read_at`, `note` | Read state and notes, kept apart from report content so each reader has their own marks once reports can be public |
| `usage_logs` | `job_id`, `runner`, `model`, `duration_ms`, `search_count`, `input_tokens`, `output_tokens` | Cost tracking; the basis for pricing later |

Principles:

- Content (reports, items) is separate from personal state (item_marks).
- The `visibility` column exists from day one and defaults to private; POC endpoints return only the caller's own data.
- Community features (comments, follows) will add tables, not change existing ones.

## 6. API (`/api/v1`)

Every public endpoint requires a Supabase JWT and `profiles.status = active`.

| Method and path | Purpose |
|---|---|
| `GET /me`, `PATCH /me` | Read and update the profile |
| `POST /auth/redeem-invite` | Redeem an invite code; moves a pending account to active |
| `GET/POST /topics`, `PATCH/DELETE /topics/{id}` | Manage topics |
| `GET/POST /subscriptions`, `PATCH/DELETE /subscriptions/{id}` | Subscriptions, item counts, order |
| `GET/PUT /delivery-slots` | Submit the full set of slots at once; the backend enforces the limit of 3 |
| `GET /reports?date=`, `GET /reports/{id}` | Report list and content, including the caller's read state and notes |
| `PUT /items/{id}/mark` | Set read state and note |
| `GET/PATCH /agent-settings` | Display only in the POC; fields are reserved |

Internal endpoints (`/internal`, authenticated with a separate service token; user JWTs are rejected):

| Method and path | Purpose |
|---|---|
| `POST /internal/jobs/claim` | Worker claims a job |
| `POST /internal/jobs/{id}/result` | Write back a report |
| `POST /internal/jobs/{id}/fail` | Report a failure |

The scheduler is a Lambda handler invoked directly by EventBridge, not over HTTP.

**Registration gate**: on first sign-in the backend creates a `profiles` row. If the email is in `allowed_emails` the status is active; otherwise it is pending and the account can call only `GET /me` and `POST /auth/redeem-invite`. This check lives in the backend and does not depend on the identity provider.

The OpenAPI spec is the single contract for the frontend and the future iOS app; client types are generated from it.

## 7. Agent design

```python
class AgentRunner(Protocol):
    async def run(self, request: BriefRequest) -> BriefResult: ...
```

- `BriefRequest`: topics (title, item count), language, time zone, date.
- `BriefResult`: per topic, the items (title, summary, source name, source URL, published time), plus usage information.
- Implementation 1: `ClaudeSubscriptionRunner` (POC). Runs Claude Code headless inside GitHub Actions with the credential in GitHub secrets. Only search and web-read tools are allowed; no shell, no file writes.
- Implementation 2: `AnthropicApiRunner`. Define the interface and a test double now; complete it before a second user is onboarded.

Prompt requirements:

- Include only news from roughly the last 24–48 hours, ordered by importance.
- Write each summary in the agent's own words, 1–3 sentences, never copying source sentences, with a source link.
- Source URLs must be links actually found, never invented.
- When sources disagree on a number, say so.
- When there is not enough fresh news, report that plainly instead of padding.
- Output language follows the user's `locale` (default Traditional Chinese).

The worker validates that the output matches the JSON schema, that URLs are http(s), and that lengths are within limits. Invalid output is retried once, then the job is marked failed. Web page content is untrusted data and must never change what the worker does.

## 8. Security

- All keys live in AWS and GitHub secrets, never in the repo. The repo is public; CI runs a secret scan.
- The GitHub workflow is triggered only by `workflow_dispatch`. Do not use `pull_request_target`. Pull requests from forks must not receive secrets.
- The Supabase service role key exists only in the Lambda environment. The frontend holds only the public key.
- RLS is enabled on every table with default deny; the backend connects with a privileged role.
- The Function URL must not be callable around CloudFront (approach in section 11 item 1). Every route requires authentication, and a Lambda concurrency cap acts as cost insurance.
- Frontend and API share one origin (`/api/*`): no CORS, and no other origins are allowed.
- An AWS budget alert is configured.
- Each user has a daily run limit (`daily_run_limit`).
- Notes are always rendered as plain text.

## 9. Repo layout

```
/
├── LICENSE, NOTICE, README.md, AGENTS.md, CLAUDE.md
├── docker-compose.yml, Makefile, .env.example
├── docs/               design documents, ADRs
│   └── agent-memory/   sub agent progress and communication (see 10.1)
├── contracts/          openapi.yaml, brief-result.schema.json
├── api/                FastAPI (modules: auth, profiles, topics, subscriptions, slots, reports, marks, jobs)
├── worker/             agent runner, prompt, job client
├── web/                React PWA
├── infra/              Terraform (Lambda, EventBridge, IAM, S3, CloudFront, ACM)
├── db/                 Alembic migrations, RLS policies
└── .github/workflows/  ci.yml, deploy-*.yml, agent-worker.yml
```

Do not create an `ios/` directory yet.

## 10. Sub agent assignments

**Phase 0 (one agent, prerequisite for everything else)**: finalize `contracts/openapi.yaml`, `brief-result.schema.json` and the DDL for the tables in section 5. Phase 1 runs in parallel only after this is done.

| Agent | Scope | Deliverables | Acceptance criteria | Depends on |
|---|---|---|---|---|
| **A. contracts** | Phase 0 | OpenAPI spec, JSON schema, DDL draft, the `docs/agent-memory/` files | Spec passes lint; covers every endpoint in section 6 | none |
| **B. db** | `db/` | Alembic migrations, RLS policies, seed data (allowlist email injected from the environment) | An empty database can be built with one command; tests cover the 3-slot limit and the job unique key | A |
| **C. api** | `api/` | FastAPI modules, JWT verification, internal endpoints, scheduler handler | Unit and integration tests pass; responses conform to the OpenAPI spec; a subscription-runner job for a non-owner is rejected | A, B |
| **D. worker** | `worker/`, `agent-worker.yml` | `AgentRunner` interface and two implementations (the API one may be a test double), prompt, output validation | Produces valid JSON locally for fixed topics; invalid output is blocked | A |
| **E. web** | `web/` | Sign-in, today's report, read state and notes, topic and slot settings, profile, PWA configuration, design tokens file | Usable at phone width; types generated from OpenAPI; end-to-end tests pass against a mock API; follows the minimal baseline in 3.3 with no hard-coded style values | A |
| **F. infra** | `infra/`, deploy workflows | Terraform (including S3, CloudFront, ACM), deployment pipeline, budget alert, concurrency cap | One command deploys to Kai's AWS account; `/api/health` responds through the subdomain; calling the Function URL directly is refused | a deployable version of C |
| **G. review** | whole repo | Security and consistency review report | Every item in section 8 checked and reported; must not be the same agent that implemented the code | all |

Parallelism: after A finishes, B, D and E start together. C starts once B has a first migration. F (cloud infra) starts only after M1; the local Docker environment in 4.5 is set up by the lead agent during bootstrap and maintained by each agent for its own service. G goes last.

Rules for every agent:

- Change only your own directory. To change a contract, follow the process in 10.1.
- Never write keys, tokens or personal email addresses into code or test data.
- When finished, report what you did, how you verified it, and what you did not do or did not verify.

### 10.1 Communication between sub agents: memory md files

Sub agents **communicate and coordinate among themselves**. They do not need to go back to the lead agent or Kai for everything. The channel is a set of memory md files in the repo. All information exchange and progress updates go there so that other agents and later sessions can read them.

**Location: `docs/agent-memory/`, kept on a dedicated `memory` branch**

Each agent works in its own git worktree and branch (see 10.3), so files committed on one agent's branch are invisible to the others until merged. The memory files therefore live on a separate `memory` branch, checked out once as a shared worktree that every agent reads and writes directly. Agents reach it through the `BRIEF_MEMORY_DIR` environment variable, not through their own worktree. Commit to the `memory` branch often and in small commits; pull with rebase before committing.

| File | Who writes | Content |
|---|---|---|
| `BOARD.md` | Each agent edits only its own row | Status board: agent, current task, status (todo / doing / blocked / done), last updated, blocked by whom |
| `<agent>.md` (for example `api.md`, `db.md`) | That agent only | Work log: what was done, how it was verified, known issues, next step. Newest entry on top, dated |
| `HANDOFFS.md` | Any agent, append only | Handoffs and questions: `[date] from → to: content`. The recipient replies under the same entry and marks it `resolved` |
| `DECISIONS.md` | Any agent, append only | Decisions that affect other agents: context, decision, impact. For example connection pool mode, error format, naming conventions |
| `VERIFIED.md` | The agent responsible for the check | Results for the items in section 11, with links to official documentation and the date checked |

**Rules**

1. **Read before starting**: `BOARD.md`, `DECISIONS.md`, the `HANDOFFS.md` entries addressed to you, and the logs of the agents you depend on.
2. **Update your row in `BOARD.md` when you start and when you stop.** If you are blocked, set the status to blocked and write in `HANDOFFS.md` what you need and from whom.
3. **When you need another agent's help**, write it in `HANDOFFS.md`. Do not edit their directory.
4. **Answering others comes before your own new work**: at the start of each session, clear the unresolved entries addressed to you.
5. **Write facts and conclusions only**: commands actually run and their results, not "this should work". If something is unverified, say so.
6. **Append; never rewrite someone else's content.** You may tidy your own log, but do not delete information that is still valid.
7. **Never write keys, tokens, personal email addresses or other personal data.** The repo is public, so these files are public too.
8. **Contract changes**: the agent that wants to change `contracts/` proposes it in `HANDOFFS.md` (the change, the reason, the affected agents). It makes the change only after the affected agents agree, then records it in `DECISIONS.md`. Escalate to the lead agent or Kai only when agents disagree.
9. **Escalate to Kai and stop that piece of work when**: a decision would incur cost, deviates from the confirmed decisions in section 3, touches the usage limit in 3.1, or cannot be undone. Mark the entry `to: Kai` in `HANDOFFS.md`.

To avoid overwriting each other during parallel work, each agent writes mainly to its own `<agent>.md`; shared files are append only.

The Phase 0 contracts agent creates this directory and the initial content of each file, including the seven rows of `BOARD.md`.

### 10.2 Which tool runs which agent

Kai has three agent CLIs. The assignment below is based on structural reasons, not on a claim that one model writes better code than another:

- **Quota**: the Claude Pro plan is also what runs the product's daily news worker, and it is the plan to protect. Claude Code takes the low-volume, high-leverage work; bulk implementation goes to the other two.
- **Independent review**: code is reviewed by a different vendor's agent than the one that wrote it.
- **Fit**: the worker drives Claude Code headless, so the Claude agent builds it.

| Agent (section 10) | Tool | Reviewed by |
|---|---|---|
| Lead / orchestration | Claude Code | Kai |
| A. contracts | Claude Code | Codex |
| B. db | Codex CLI | Claude Code |
| C. api | Codex CLI | Claude Code |
| D. worker | Claude Code | Codex |
| E. web | Antigravity CLI (`agy`) | Codex |
| F. infra | Codex CLI | Claude Code |
| G. review (final) | Claude Code and Codex, each on code it did not write | Kai |
| Section 11 research items | `agy` (first pass), confirmed by the owning agent | — |

Revisit this table after M1. If one tool is clearly struggling with its area or running out of quota, Kai reassigns; record the change in `DECISIONS.md`.

### 10.3 Setup so the agents run smoothly

Items marked "to verify" are in section 11 (items 8–10). Tool flags and file locations change often; check each tool's current documentation before relying on anything below.

**One shared instruction file**

- `AGENTS.md` at the repo root is the single source of rules for all three tools. Codex and `agy` read it natively. Claude Code reads `CLAUDE.md`, so `CLAUDE.md` contains only an import of `AGENTS.md` plus anything Claude-specific (to verify).
- `AGENTS.md` contains: a pointer to this design document; the directory ownership table; the memory protocol from 10.1; the commands to install, test and lint each directory; and the never-do list (no secrets in files, no edits outside your directory, no deploys, no `terraform apply`, no destructive git commands, no changes to `contracts/` without the 10.1 process).
- Keep it short. Tool-specific files hold only what cannot go in `AGENTS.md`.

**Tool-specific configuration (all committed to the repo)**

| Tool | Files | Content |
|---|---|---|
| Claude Code | `CLAUDE.md`, `.claude/agents/<name>.md` | One subagent definition per Claude-run agent (contracts, worker, reviewer): `name`, `description`, `tools`, `model: sonnet`, `isolation: worktree` |
| Codex CLI | `.codex/config.toml` | `sandbox_mode = "workspace-write"`, `approval_policy = "on-request"` for interactive work |
| Antigravity CLI | `.agents/rules/*.md`, `.agents/agents/` | Scoped rules for `web/`; a custom agent definition for the web agent |

**Isolation: one worktree and branch per agent**

```
git worktree add ../daily-brief-memory memory      # shared memory, created once
git worktree add ../daily-brief-api -b agent/api   # repeat per agent: db, worker, web, infra
export BRIEF_MEMORY_DIR="$(cd ../daily-brief-memory/docs/agent-memory && pwd)"
```

- Each agent runs in its own worktree, in its own terminal pane, and opens a pull request into the default branch. Kai (or the lead agent, with Kai's approval) merges.
- Every tool must be allowed to write to `BRIEF_MEMORY_DIR` in addition to its own worktree. Each CLI has an option to add an extra directory to the workspace; the exact flag or config key is to verify.
- Install dependencies separately in each worktree.

**Permissions**

- Default to the sandboxed, ask-when-needed mode of each tool. Agents may edit files and run tests and linters inside their worktree without asking.
- For unattended runs use each tool's non-interactive mode with an explicit sandbox (`codex exec` with a workspace-write sandbox, `agy -p` with scoped allow rules, `claude -p` with an allowed-tools list). Do not use any "skip all permissions" or "full access" flag on the development machine.
- Agents never receive production secrets. Local development uses a git-ignored `.env`; the repo carries only `.env.example` with placeholder values.
- Agents never deploy. `terraform apply`, pushing to the default branch, and anything touching the AWS, Supabase, Google or GitHub accounts is done by Kai.
- **One exception, authorized by Kai**: the lead agent creates the GitHub repo and makes the initial bootstrap push using `gh`. Before doing so it shows Kai the repo name, the visibility (public) and the list of files to be pushed, confirms that no secret or personal email is among them, and waits for Kai's go-ahead. After the bootstrap push, all changes go through pull requests.

**Sign-in**

Sign in to each CLI once, interactively, with Kai's own subscription. This is ordinary individual use of each plan. Do not put these credentials in CI, with the single exception of the Claude credential for the product's worker described in 2.1.

**Starting an agent**

Use the same opening prompt shape for every tool:

```
You are the <name> agent for this repo. Read AGENTS.md and docs/DESIGN.md sections 10 and 10.1.
Your directory is <dir>/. Your acceptance criteria are in the section 10 table.
Before any work: read BOARD.md, DECISIONS.md and HANDOFFS.md in $BRIEF_MEMORY_DIR and update your BOARD.md row.
When you stop: update your row and your log, and report what you did, how you verified it, and what is not done.
```

**Order of work**

Kai runs the agents in herdr, one pane per agent, with Claude Code as lead in the first pane.

1. Kai creates a local folder, runs `git init`, puts this file at `docs/DESIGN.md`, and starts Claude Code there as lead.
2. Lead verifies section 11 items 8–10, then writes the bootstrap files: `LICENSE`, `NOTICE`, `README.md`, `.gitignore`, `.env.example`, `AGENTS.md`, `CLAUDE.md`, and the tool configuration files.
3. Lead creates the GitHub repo with `gh` under the exception above, then creates the `memory` branch with the initial memory files and the worktrees for each agent.
4. Lead runs Phase 0 (contracts) and builds the local environment skeleton from 4.5 (`docker-compose.yml`, `Makefile`, a health-check endpoint) and confirms `make up` works.
5. `agy` does the first pass on the section 11 research items in parallel with Phase 0.
6. Lead gives Kai the opening prompt for each other pane. Codex starts db, then api; `agy` starts web against a mock API; Claude Code starts worker.
7. Cross-vendor reviews happen per pull request, not only at the end.
8. Cloud work (infra agent, section 2.1 rows 3–7) starts only after M1.

## 11. Items to verify

The responsible agent must check official documentation first and record the result in `docs/agent-memory/VERIFIED.md`:

1. **CloudFront in front of a Lambda Function URL** (F):
   - Confirm CloudFront's current free allowance and pricing model.
   - Choose one way to lock the Function URL: (a) set the Function URL to IAM auth and use CloudFront Origin Access Control; or (b) keep the Function URL public, have CloudFront add a secret custom header, and have the API check it.
   - Before choosing (a), confirm whether requests with a body (POST, PUT, PATCH) require the client to send an extra content-hash header. If they do and the frontend cannot reasonably provide it, use (b).
   - CloudFront must forward the `Authorization` header and must not cache `/api/*`.
   - The ACM certificate for CloudFront must be created in us-east-1.
2. **Lambda packaging**: zip plus Lambda Web Adapter, or a container image (needs ECR; confirm storage cost) (F).
3. **Supabase connections**: the pooling mode to use from a short-lived environment such as Lambda, and async driver compatibility (B, C).
4. **Supabase free-project inactivity pause**: the scheduler queries the database every 15 minutes, which should prevent a pause, but confirm (B).
5. **Running Claude on a personal plan headless in GitHub Actions**: the official procedure, the credential's environment variable name, and the usage rules (D).
6. **GitHub `workflow_dispatch` API**: the token permission required, using least privilege (C, F).
7. **Region**: put Lambda and Supabase in the same region (Tokyo recommended) and confirm both are available there (F, B).
8. **Claude Code and `AGENTS.md`**: confirm how `CLAUDE.md` imports another file, and the current subagent frontmatter fields (`tools`, `model`, `isolation`) (lead).
9. **Codex CLI sandbox**: the config key or flag that adds a writable directory outside the workspace, whether network access is available in the workspace-write sandbox (needed for package installs), and the current approval policy names (lead).
10. **Antigravity CLI**: the flag to add a directory to the workspace, the syntax of permission allow rules, the headless flags that actually exist in the installed version, and where custom agents and scoped rules live (lead).

## 12. Milestones

1. **M0**: contracts and tables finalized.
2. **M1**: the whole system works locally under Docker as defined in 4.5, including the web app with the minimal UI.
3. **M2**: deployed to AWS; Kai can open the subdomain, sign in with Google and see the daily report.
4. **M3**: settings pages done (topics, item counts, slots); read state and notes work. Retire the existing Claude scheduled-task version at this point.
5. **M4**: visual style discussion with Kai, style selection, then restyle through the design tokens.
6. **Later**: `AnthropicApiRunner`, inviting other users, Apple sign-in, native iOS app, public reports and community features.

## 13. Current stopgap

A Claude scheduled task currently produces a report at 06:40 Taipei time every day and writes it to the "每日晨報" page. It covers six topics: stock market, AI, information security, weather, Ruby and Python releases, travel news. Keep using it until M3. These six topics are also the default seed topics for the new system.
