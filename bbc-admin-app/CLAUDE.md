# BBC Admin Panel — CLAUDE.md

> Governance file for Claude Code. Read automatically on session start.
> Last updated: 2026-03-16

## Project Identity

| Field | Value |
|-------|-------|
| Name | BBC Admin Panel (BuyBusinessClass) |
| Type | Internal admin dashboard |
| Repo | `bbc-admin-app` |
| Companion API | `bbc-chatbot-api` (FastAPI) |
| Deploy | Netlify |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | React 19 + TypeScript 5.9 (strict) |
| Routing | TanStack Router (file-based, auto code-split) |
| Data fetching | TanStack React Query |
| State | Zustand (auth store) |
| UI primitives | Radix UI (30+ components in `src/components/ui/`) |
| Styling | Tailwind CSS 4 + CVA + tailwind-merge |
| Forms | React Hook Form + Zod |
| Charts | Recharts |
| Auth | Clerk (`@clerk/clerk-react`) |
| Build | Vite + SWC |
| Package manager | pnpm |
| Toasts | Sonner |

## Architecture Rules

### Directory Layout

```
src/
├── assets/          # SVG logos, custom icons
├── components/      # Shared components (ui/, data-table/, layout/)
├── config/          # App-wide config (fonts)
├── context/         # React context providers (theme, layout, search, direction, font)
├── features/        # Feature modules (dashboard, chats, leads, knowledge-base, users, settings)
├── hooks/           # Shared hooks (use-dialog-state, use-mobile, use-table-url-state)
├── lib/             # Utilities (api.ts, types.ts, utils.ts, cookies.ts)
├── routes/          # TanStack Router file-based routes
├── stores/          # Zustand stores
└── styles/          # Global CSS
```

### Key Conventions

1. **Feature-first organisation** — each domain lives in `src/features/<domain>/`.
2. **Single API client** — all backend calls go through `src/lib/api.ts` via `apiFetch<T>()`.
3. **Types mirror backend** — `src/lib/types.ts` must match the API contract in `specs/api-contract.md`.
4. **Radix + Tailwind** — never import a third-party component library when a Radix primitive already exists in `src/components/ui/`.
5. **Zod schemas** — every form must have a Zod schema; no manual validation.
6. **Path alias** — use `@/*` (maps to `src/*`).
7. **No default exports** — use named exports everywhere except route files.
8. **Strict TypeScript** — `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch` are on.

### API Integration

- Base URL: `VITE_API_URL` env var
- Auth: HTTP Basic via `VITE_API_USER` / `VITE_API_PASS` injected on every request
- Timeout: 8 seconds
- Error fallback: mock data for dashboard stats on network failure
- Response envelope: `{ success, data, count, error }`

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `VITE_API_URL` | Backend API base URL |
| `VITE_API_USER` | HTTP Basic username |
| `VITE_API_PASS` | HTTP Basic password |
| `VITE_CLERK_PUBLISHABLE_KEY` | Clerk auth key |

> **Never commit `.env` files.**

## Commands

| Task | Command |
|------|---------|
| Dev server | `pnpm dev` |
| Build | `pnpm build` (runs `tsc -b && vite build`) |
| Lint | `pnpm lint` |
| Format check | `pnpm format:check` |
| Format fix | `pnpm format` |
| Dead code | `pnpm knip` |

## Quality Gates

Before any PR:

1. `pnpm build` — must pass with zero errors
2. `pnpm lint` — must pass
3. `pnpm format:check` — must pass
4. No `any` types unless explicitly justified with `// eslint-disable-next-line`
5. Every new feature must live in `src/features/<domain>/`

## Spec-Driven Development

- Specs live in `specs/` — one Markdown file per complex feature.
- A feature needs a spec if it requires > 1 day of work.
- Spec format: Status → Problem → Acceptance Criteria (Given/When/Then) → Technical Design → Tasks → Decision Log.
- Claude must read the relevant spec before implementing a feature.

## Skills

| Skill | Purpose |
|-------|---------|
| `bbc-frontend-design` | Radix + Tailwind component patterns |
| `bbc-copilot-prompt` | GitHub Copilot prompt engineering for this codebase |
| `bbc-api-integration` | `apiFetch` patterns + React Query hooks |

## Do NOT

- Add dependencies without explicit approval.
- Modify `src/components/ui/` base primitives — extend via wrapper components.
- Use `document.querySelector` or direct DOM manipulation.
- Store secrets in code or committed files.
- Use `// @ts-ignore` — fix the type instead.
- Skip Zod validation on forms.
