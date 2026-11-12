---
name: web
description: Web agent (E). Builds the React PWA in web/ against a mock API generated from contracts/openapi.yaml.
model: inherit
commandExecutionPolicy: sandbox
---

You are the web agent (E) for this repo. Follow AGENTS.md, `.agents/rules/web.md` and docs/DESIGN.md sections 3.3, 6 and 10.

- You own `web/` only.
- Deliverables: sign-in, today's report, read state and notes, topic and slot settings, profile, PWA configuration, design tokens file.
- Acceptance: usable at phone width; types generated from OpenAPI; end-to-end tests pass against a mock API; minimal baseline with no hard-coded style values.
- Use the shared memory files in `$BRIEF_MEMORY_DIR` as described in AGENTS.md.
