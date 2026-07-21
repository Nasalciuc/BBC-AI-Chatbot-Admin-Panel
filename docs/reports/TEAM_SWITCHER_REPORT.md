# Team Switcher — Report (activate header dropdown → real team context)

**Date:** 2026-07-21
**Branch:** `feat/teams-frontend` (working tree; **uncommitted**)
**Scope:** FRONTEND-ONLY. **NO commits / NO staging / NO prod side effects.**

Turns the leftover shadcn `TeamSwitcher` (hardcoded "BuyBusinessClass" + dead "Add team") into a real, API-backed team context switcher. The Teams management **page** is untouched (owner decision: page = management, switcher = context).

---

## 1. Phase A answers (A1–A6, verbatim)

### A1 — Current `TeamSwitcher`
Template component fed a hardcoded prop; `activeTeam` was **local `useState`**, "Add team" was a **dead item** (no handler):
```tsx
type TeamSwitcherProps = { teams: { name: string; logo: React.ElementType; plan: string }[] }
export function TeamSwitcher({ teams }: TeamSwitcherProps) {
  const [activeTeam, setActiveTeam] = React.useState(teams[0])
  ...
  {teams.map((team, index) => (
    <DropdownMenuItem onClick={() => setActiveTeam(team)}>...⌘{index+1}</DropdownMenuItem>
  ))}
  <DropdownMenuItem className='gap-2 p-2'>  {/* Add team — no onClick */}
    <Plus/> Add team
  </DropdownMenuItem>
}
```

### A2 — How `AppSidebar` passed teams
From the hardcoded `sidebarData.teams`:
```tsx
const data = getSidebarData(permissions, ...)
<TeamSwitcher teams={data.teams} />
```
and in `sidebar-data.ts`: `teams: [{ name: 'BuyBusinessClass', logo: Logo, plan: 'Admin Panel' }]`.

### A3 — Does a `getTeams` client / `Team` type / `useTeams` hook exist?
- **`getTeams()` — YES** (`src/lib/api.ts:328`), returns `Team[]` from `/api/admin/teams`. **Reused.**
- **`Team` type — YES** (`src/lib/bbc/types.ts:147`: `id, name, shift_name?, shift_start?, shift_end?, supervisor_id?, pm_id?, is_active, ...`). **Reused.**
- **`useTeams` hook — NO.** The Teams page inlines `useQuery({ queryKey: ['teams'], queryFn: () => getTeams() })`. → I inlined the same `useQuery(['teams'])` in the switcher to **share the cache** (see §7 deviation — no separate hook file).

### A4 — Stores pattern
Zustand via `create<...>()(...)` (`src/stores/auth-store.ts`). Only the **token** is persisted (cookie); user + other state are **in-memory**. New `team-store` mirrors this: in-memory, no localStorage.

### A5 — How chats/leads build query params (and can the backend focus by team?)
`features/chats/index.tsx`: tab presets drive params, e.g.
```tsx
const listParams = { ...tab.params, limit: '50' }   // tab.params = { assigned_to, status }
useQuery({ queryKey: ['conversations', activeTab, debouncedSearch, tunnelFilter, handledByFilter],
           queryFn: () => getConversations(listParams) })
```
**No `team_id`/`teamId` param** exists on `getConversations`/`getLeads` clients, nor on the `/api/conversations` · `/api/leads` endpoints (Phase 2 auto-scopes supervisor/PM server-side, but exposes **no team-focus param** for owner/admin/PM). → **§3.5 is a documented follow-up; NOT wired here.**

### A6 — `canViewTeams` / `canManageTeams`
Available via `usePermissions(role)` (`src/lib/bbc/hooks.ts`): owner/admin/dev/qa/supervisor/PM → `canViewTeams: true`; sales/support → `false`. `canManageTeams: true` only for owner/admin/dev.

**STOP conditions:** none — switcher was hardcoded (not real); `getTeams`/`Team` exist and are reused.

---

## 2. What was added / changed

| File | Change |
|------|--------|
| `src/stores/team-store.ts` (**new**) | Zustand store: `activeTeamId: string \| null`, `setActiveTeam`. In-memory. `null` = "All teams". |
| `src/components/layout/team-switcher.tsx` | Rewritten: loads real teams via `useQuery(['teams'], getTeams)`; renders **All teams** + each team (name + shift); selection → `setActiveTeam`; **Add team** → `navigate('/teams')` for `canManageTeams`; loading/0-teams fallback to static brand entry. No `teams` prop anymore. |
| `src/components/layout/app-sidebar.tsx` | Header now: `{permissions.canViewTeams ? <TeamSwitcher /> : <AppTitle />}`. Dropped the `teams={data.teams}` prop. |
| `src/components/layout/data/sidebar-data.ts` | Removed hardcoded `teams: [{BuyBusinessClass…}]` → `teams: []` (deprecated; kept only to satisfy the out-of-ownership `SidebarData` type). Removed now-unused `Logo` import. |

Shift label helper: `"<shift_name> · HH:MM–HH:MM"` (falls back to times-only, then shift_name, then nothing).

---

## 3. §3.5 status — team focus filtering: **DEFERRED (documented follow-up)**

The switcher **sets context** (`activeTeamId` in the store) and works fully, but it does **not yet refilter** conversations/leads, because:
- The backend list endpoints accept **no team-focus param** for owner/admin/PM (Phase 2 only auto-scopes supervisor/PM to their own team, server-side).
- Wiring a filter would require **backend changes** (add a `team_id` focus param) + editing `features/chats/index.tsx` / `features/leads/index.tsx` — **both outside this PR's ownership**.

Per §3.5 I did **not** add backend code and did **not** touch chats/leads. Follow-up to make switching actually refilter:
1. Backend: accept an optional `team_id` focus param on `/api/conversations` and `/api/leads` (owner/admin/PM only; supervisor stays hard-scoped).
2. Frontend: read `activeTeamId` from `useTeamStore` in chats/leads, add it to the query key + params.

For **supervisors**, the backend already hard-scopes to their team, so the switcher is **informational** for them regardless (selecting "All teams" cannot widen their server-side scope).

---

## 4. `git status` + `git diff --stat` (owned files only)

```
 M bbc-admin-app/src/components/layout/app-sidebar.tsx            |   4 +-
 M bbc-admin-app/src/components/layout/data/sidebar-data.ts       |   8 +-
 M bbc-admin-app/src/components/layout/team-switcher.tsx          | 148 +++++++++++++-----
?? bbc-admin-app/src/stores/team-store.ts                         (new)
```
`features/teams/*` — **untouched** (verified: empty `git status` for that path). No backend files touched.

---

## 5. Build + checklist

**Build:** `tsc -b && vite build` → **PASS, 0 TS errors** (`✓ built in 24.00s`; pre-existing chunk-size warning only).

| Check | Result |
|-------|--------|
| owner/admin: switcher lists real teams + "All teams"; selecting sets active context; "All teams" clears it | **PASS** (build/static; context set in store) |
| "Add team" (owner/admin) → navigates to `/teams` | **PASS** (`navigate({ to: '/teams' })`, gated on `canManageTeams`) |
| supervisor with 1 team: shows team, no confusing empty dropdown, no crash | **PASS** (dropdown = "All teams" + the 1 team; informational, backend-scoped) |
| sales/support: NO switcher — brand `AppTitle` shows (regression identical to today) | **PASS** (`canViewTeams=false` → `<AppTitle/>`) |
| 0 teams / loading: static brand entry, no flicker, no empty dropdown | **PASS** (early return to brand `SidebarMenuButton`) |
| `git status` only owned files; `features/teams/*` untouched | **PASS** |

> Live click-through against a running backend was not performed here; checks are verified by a clean typed build + code paths. Because 0 real teams exist yet in prod, the dropdown will show the brand fallback until teams are created via the Teams page.

---

## 6. Teams management page — kept & untouched

Per owner decision, `features/teams/*` (create/edit/assign) is **unchanged**. The switcher is the fast header context-switch; the page remains the CRUD surface. "Add team" reuses the page (`/teams`) rather than duplicating a create dialog.

---

## 7. Didn't fit HEAD / deviations

1. **No separate `useTeams` hook.** §3.1 suggested one, but the Teams page inlines `useQuery(['teams'])`; I inlined the same query in the switcher to share cache with a single owned-file change (avoids touching `lib/bbc/hooks.ts`). If a shared hook is preferred later, extract `useTeams()` wrapping `getTeams()` under `['teams']`.
2. **`sidebar-data.ts` `teams` field kept as `[]`** (not fully removed) because `SidebarData.teams` is required in `components/layout/types.ts`, which is **outside this PR's ownership**. Left empty + deprecated comment instead of editing the type. If you want it gone entirely, make `teams` optional in `types.ts` (separate, tiny change) and drop the field.
3. **1-team UX:** chose a normal dropdown ("All teams" + the single team) over a static label, for consistency with the multi-team case. Noted; trivial to switch to static if preferred.
4. **`⌘1` keyboard shortcuts** from the template were dropped (they were decorative and not wired to any handler); not re-added to avoid over-building.

---

## Final

| Item | Value |
|------|-------|
| Store | `team-store.ts` — `activeTeamId` / `setActiveTeam` (in-memory) |
| Switcher | real teams + "All teams" + shift labels; "Add team" → `/teams` (canManageTeams) |
| Gating | `canViewTeams` → switcher; else `AppTitle` (sales/support unchanged) |
| Refilter (§3.5) | **deferred** — backend has no team-focus param (follow-up) |
| Teams page | **untouched** |
| Build | **PASS**, 0 TS errors |
| Files | 3 owned edited + 1 new store; `features/teams/*` untouched |
| Commit | **None** — awaiting owner approval |
