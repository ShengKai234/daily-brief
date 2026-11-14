# Daily Brief — Web PWA (`web/`)

React + TypeScript + Vite Progressive Web App following the minimal visual baseline in `docs/DESIGN.md` section 3.3.

## Commands

```bash
npm ci                 # Install dependencies
npm run codegen        # Generate src/api/schema.gen.ts from ../contracts/openapi.yaml
npm test               # Run Vitest unit, contract, baseline, and E2E user-flow tests
npm run test:e2e       # Run Playwright phone-width (375x667) browser E2E suite
npm run lint           # Run ESLint
npm run typecheck      # Run TypeScript compiler check
npm run build          # Production bundle into dist/
```

## Architecture & Rules

- **OpenAPI Types**: Generated into `src/api/schema.gen.ts` from `../contracts/openapi.yaml` via `openapi-typescript`. `src/api/types.ts` re-exports aliases strictly indexed from the generated schema.
- **Design Tokens (`src/styles/tokens.css`)**: Single source of CSS custom properties for typography (system font stack), neutral light/dark palette (`prefers-color-scheme`), spacing, radii, and layout dimensions. No component or stylesheet outside `tokens.css` hard-codes colors, spacing, radii, or font sizes.
- **Plain-Text Notes**: User notes on report items are rendered strictly as text nodes, never as HTML.
- **Mock API (`src/api/mockApi.ts`)**: Implements `/api/health` and `/api/v1/*` routes conforming to `contracts/openapi.yaml` (including RFC 9457 `application/problem+json` errors, registration gate for `pending` and `disabled` accounts, 3-slot limit, and 30-minute granularity). Enabled by default in standalone/test runs (`VITE_USE_MOCK_API=true`) and disabled in Docker Compose (`VITE_USE_MOCK_API=false`) where `/api` proxies to `http://api:8000`.
