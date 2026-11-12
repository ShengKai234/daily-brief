# Daily Brief

At the times you choose each day, an AI agent searches the news for the topics you subscribe to, compiles the top items per topic, and publishes them to your personal page. Mark items as read and keep notes.

**Status:** proof of concept, under development. Local-first; not deployed.

## Architecture

- `api/` — Python, FastAPI (modular monolith, includes the scheduler handler)
- `worker/` — agent runner that produces each brief (headless Claude Code for the POC)
- `web/` — React + TypeScript PWA
- `db/` — Postgres migrations (Alembic) and RLS policies
- `contracts/` — OpenAPI spec and the brief result JSON schema
- `infra/` — Terraform for AWS (later)

Full design: [`docs/DESIGN.md`](docs/DESIGN.md).

## Local development

Requires Docker (OrbStack works), Python 3.12+, `uv`, and Node.js LTS.

```bash
cp .env.example .env    # then fill in local values
make up                 # start db, api, scheduler, web
make test
make down
```

## Contributing with agents

Rules for coding agents are in [`AGENTS.md`](AGENTS.md).

## License

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE). If you use this code, keep the copyright notice and the NOTICE file.
