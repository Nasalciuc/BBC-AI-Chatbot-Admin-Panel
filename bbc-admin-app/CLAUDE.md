# BBC Admin Panel — CLAUDE.md

> Every line in this file changes the behavior of an AI agent.
> If a line can be deleted without changing generated code, it does not belong here.

## Identity

- **Project:** BBC AI Chatbot Admin Panel (`bbc-admin-app`)
- **Purpose:** Internal dashboard for managing chatbot conversations, leads, knowledge base, users, and AI pipeline health for BuyBusinessClass.com
- **NOT:** Customer-facing widget, backend API (see `bbc-chatbot-api`), QM evaluation system
- **Users:** Dan (owner/marketing), Maria (sales agent), Scaler (dev), Nasalciuc (dev)

## Current State (updated 2026-03-16)

- Frontend deployed: `admin-panel-error.vercel.app` (Vercel)
- Backend deployed: `admin-panel-error-production.up.railway.app` (Railway)
- **All pages run on mock data** except Dashboard (calls getDashboardStats from API)
- Cleanup branch `cleanup/remove-invented-data` in progress
- Railway deployment behind GitHub HEAD due to platform incident
- Auth: custom JWT via `auth-store.ts` + cookie `bbc_admin_token`. Clerk in package.json but NOT used — must be removed.
- Supabase: `service_role` key required (not anon key). RLS active.

## Stack

Do NOT install alternatives. `package.json` is source of truth for versions.

| Layer | Tool | Import |
|-------|------|--------|
| Framework | React 19 | `react` |
| Routing | TanStack Router | `@tanstack/react-router` |
| Server state | TanStack Query | `@tanstack/react-query` |
| Client state | Zustand | `zustand` |
| UI primitives | shadcn/ui (Radix) | `@/components/ui/*` |
| Styling | Tailwind CSS 4 | utility classes only |
| Forms | react-hook-form + zod | `react-hook-form`, `zod` |
| Charts | Recharts | `recharts` |
| Icons | lucide-react | `lucide-react` |
| Dates | date-fns | `date-fns` |
| Toast | sonner | `sonner` |
| Build | Vite | `vite` |

## File Structure

```
src/
  features/{name}/index.tsx          page (default export)
    components/                      sub-components for this feature only
    data/                            mock data or static config
  components/ui/                     shadcn primitives ONLY (untouched)
  components/*.tsx                   BBC custom components
  lib/api.ts                         ALL API calls (single gateway)
  lib/types.ts                       ALL shared TypeScript types
  stores/                            Zustand stores (one per domain)
```

Max 2 levels under features/. Path alias `@/` for all imports.

## Data Flow

1. ALL API calls in `src/lib/api.ts` — features NEVER import axios/fetch
2. ALL types in `src/lib/types.ts` BEFORE implementation
3. `VITE_API_URL` env var — NEVER hardcode URLs
4. Axios interceptor in api.ts adds auth header — components NEVER send tokens
5. TanStack Query (`useQuery`/`useMutation`) for server state — NEVER useState for API data
6. Zustand ONLY for auth, sidebar, UI preferences

## RBAC

Four roles: `owner` | `admin` | `sales` | `support`.
Restricted items: disabled + lock icon + tooltip "Access restricted". Never hide nav items.
V1 RBAC is frontend-only. Backend enforcement in V2.

## Brand & Theme

- **Primary:** Navy `#0B1829` → sidebar background, page titles, strong emphasis
- **Accent:** Gold `#C9A54E` → active nav, primary CTA, important badge (MAX 2 per screen)
- **Background:** Clean white/off-white `bg-background` — light mode is PRIMARY
- **Dark mode:** supported but NOT default. Light is the everyday experience.
- **Palette:** Muted, professional. No saturated colors. Gray scale + gold accent.
- Use semantic tokens: `bg-background`, `text-foreground`, `text-muted-foreground`
- NEVER bright/neon colors. NEVER heavy shadows. NEVER dark-first design.
- Logo: `@/assets/logo`

## Never List

1. NEVER install `@faker-js/faker`
2. NEVER import from `@clerk/*`
3. NEVER use `React.FC` or class components
4. NEVER create `.css`/`.scss` files
5. NEVER hardcode API URLs
6. NEVER `console.log` in production
7. NEVER import axios/fetch in `features/`
8. NEVER use `bg-white`/`text-black` hardcoded — use semantic tokens
9. NEVER use Redux, useReducer for server state, or Context for global state
10. NEVER install moment.js, dayjs, react-icons, heroicons
11. NEVER nest features/ deeper than 2 levels
12. NEVER put business logic in `components/ui/`
13. NEVER skip TypeScript types for API responses

## Gates

Before presenting code: `tsc --noEmit` passes, no console.log, all imports resolve, light mode looks correct, API through lib/api.ts, types in lib/types.ts, `npm run build` passes.

## Agent Teams Readiness

Status: PRE-CONFIGURED, flag disabled.
Activate: set `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in `.claude/settings.json`.
