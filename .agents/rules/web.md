---
trigger: glob
globs: "web/**"
description: Rules for the React PWA in web/ (minimal visual baseline, DESIGN 3.3)
---

# web/ rules

- Stack: React + TypeScript + Vite, PWA. API types are generated from `contracts/openapi.yaml`; never hand-write API types.
- Visual baseline (DESIGN 3.3): system font stack, one neutral palette, light and dark via `prefers-color-scheme`. No brand colors, illustrations, animations, custom icons, styled component library or CSS framework.
- Every color, spacing, radius and font size comes from the design-tokens CSS variables file. Components never hard-code these values.
- Spend effort on structure: routing, data loading, error and empty states, forms, accessibility (labels, focus, keyboard), phone-width layout.
- Notes are always rendered as plain text, never as HTML.
- The frontend uses the identity provider only to sign in and get a JWT; all data goes through `/api/v1`. No direct table access.
- Style ideas go to `HANDOFFS.md` addressed `to: Kai`; do not implement them.
