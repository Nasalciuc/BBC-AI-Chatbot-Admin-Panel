# BBC Admin Panel — CLAUDE.md

> Every line changes AI agent behavior. If deletable without effect, it doesn't belong.

## Identity

- **Project:** BBC AI Chatbot Admin Panel (`bbc-admin-app`)
- **Purpose:** Dashboard for conversations, leads, KB, users, AI health — BuyBusinessClass.com
- **NOT:** Customer widget, backend API (`bbc-chatbot-api`), QM system
- **Users:** Dan (owner/marketing), Maria (sales), Scaler (dev), Nasalciuc (dev)

## Current State (2026-03-16)

- Frontend: `admin-panel-error.vercel.app` (Vercel)
- Backend: `admin-panel-error-production.up.railway.app` (Railway, BEHIND HEAD)
- **All pages MOCK except Dashboard** (getDashboardStats from API)
- Clerk REMOVED (was in package.json, never used)
- Auth: custom JWT, cookie `bbc_admin_token`
- Supabase: `service_role` key required, RLS active

## Stack

| Layer | Tool | Import |
|-------|------|--------|
| Framework | React 19 | `react` |
| Routing | TanStack Router | `@tanstack/react-router` |
| Server state | TanStack Query | `@tanstack/react-query` |
| Client state | Zustand | `zustand` |
| UI | shadcn/ui (Radix) | `@/components/ui/*` |
| Styling | Tailwind CSS 4 | utility classes |
| Forms | react-hook-form + zod | |
| Charts | Recharts | `recharts` |
| Icons | lucide-react | `lucide-react` |
| Dates | date-fns | `date-fns` |
| Toast | sonner | `sonner` |
| Build | Vite | TypeScript strict |

## File Structure

```
src/features/{name}/index.tsx       page (default export)
  components/                       sub-components this feature only
  data/                             mock or config
src/components/ui/                  shadcn untouched
src/components/*.tsx                BBC custom
src/lib/api.ts                      ALL API calls
src/lib/types.ts                    ALL shared types
src/stores/                         Zustand (auth, sidebar, UI)
```

Max 2 levels. Path alias `@/`.

## Data Flow

1. ALL API in `src/lib/api.ts` — features NEVER import axios/fetch
2. Types in `src/lib/types.ts` BEFORE implementation
3. `VITE_API_URL` — NEVER hardcode URLs
4. Auth header via axios interceptor — components NEVER send tokens
5. `useQuery`/`useMutation` for server data — NEVER useState for API data
6. Zustand ONLY for auth, sidebar, UI prefs

## RBAC

owner | admin | sales | support. Restricted = disabled + lock icon + tooltip. V1 frontend-only.

## Brand & Theme

- Navy `#0B1829` → sidebar, headings, strong emphasis
- Gold `#C9A54E` → active nav, CTA, badge (MAX 2 per screen)
- Light mode PRIMARY. Dark supported, not default.
- Semantic tokens: `bg-background`, `text-foreground`, `text-muted-foreground`
- NEVER bright/neon. NEVER heavy shadows. NEVER dark-first.

## Current Sprint (completed 2026-03-17 to 2026-03-21)
1. ✅ SDD governance (CLAUDE.md, 10 agents, 3 skills, Agent Teams)
2. ✅ Auth dual-mode (Basic + Bearer), 97/97 tests
3. ✅ Response shape fixes (leads, conversations, KB)
4. ✅ Railway LIVE — correct repo, auto-deploy, Hobby plan pending
5. ✅ Vercel LIVE — SPA routing, npm build, iframe headers
6. ✅ Widget Preview + Widget Embed (/widget-embed for iframe)
7. ✅ CORS updated for buybusinessclass.com
8. ✅ Qdrant semantic search — MiniLM 384d FREE, 15 entries
9. ✅ Lead detail drawer — click row → Sheet with conversation
10. ✅ Templates 23→33 keys, auto-summarization every 5 msgs
11. ✅ System Prompt V2 — few-shot, handoff, premium tone
12. ✅ KB gap analysis script + README rewrite + user guide

## Next Sprint (Week 2)
1. ⬜ Widget pe buybusinessclass.com (Dan decision)
2. ⬜ Railway Hobby upgrade (Dan — $5/mo)
3. ⬜ Users page frontend (mock → real)
4. ⬜ Dashboard polish (real data styling)
5. ⬜ WhatsApp integration (Meta verification)

## Git Rules
- One scope per commit: feat(api), fix(ui), fix(infra), docs
- NEVER mix frontend + backend in one commit
- ONLY remote: github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel
- Push after EVERY completed task, verify on production

## Never List

1. NEVER `@faker-js/faker` | 2. NEVER `@clerk/*` | 3. NEVER React.FC / class components
4. NEVER .css/.scss | 5. NEVER hardcode URLs | 6. NEVER console.log in prod
7. NEVER axios/fetch in features/ | 8. NEVER bg-white/text-black hardcoded
9. NEVER Redux/Context for global state | 10. NEVER moment/dayjs/react-icons/heroicons
11. NEVER features/ >2 levels | 12. NEVER logic in components/ui/
13. NEVER skip types for API responses

## Gates

tsc --noEmit | no console.log | imports resolve | light mode correct | API through api.ts | types in types.ts | build passes

## Agent Teams

Status: **ACTIVE** — flag enabled in .claude/settings.json
Workflow: Sub agents research (cheap) → Plan approval (human) → Agent Teams execute (contract-first)
Budget: $50/day sprint, /day maintenance. Run `/cost` after every session.
