# Teams — Phase 3 UI Report (management page: create/edit teams, assign people)

**Date:** 2026-07-20  
**Branch:** `feat/teams-phase-1` (working tree; uncommitted)  
**Status:** Implementation complete — **NO commits / NO staging. Frontend only.**

Consumes the Phase 1 CRUD endpoints (`/api/admin/teams`, `PATCH /api/admin/users/{id}` with `team_id`). No backend changes.

---

## 1. Phase A answers

**A1 — `features/users/index.tsx` structure:** TanStack Query (`useQuery({ queryKey: ['users'], queryFn: getUsers })`), `Header`/`Main` layout wrappers, a title + primary button, and a table component. Dialogs mounted at the bottom via a context provider. I mirrored the **layout + query pattern** but used a simpler self-contained table + local dialog state (the users page's `react-table` provider/columns framework is heavier than this page needs).

**A2 — Dialog pattern:** `react-hook-form` + `zodResolver` + shadcn `Dialog`/`Form`/`FormField`/`Input`/`Select`. Copied from `users-action-dialog.tsx`.

**A3 — Routing:** file-based TanStack Router (`@tanstack/router-plugin/vite`, `autoCodeSplitting`). A page = `src/routes/_authenticated/<name>/index.tsx` exporting `Route = createFileRoute('/_authenticated/<name>/')({ component })`. The plugin regenerates `src/routeTree.gen.ts` on dev/build. Sidebar links via `url` in `sidebar-data.ts` (typed `LinkProps['to'] | (string & {})`).

**A4 — `Team` type / API:** `Team` **already exists** in `src/lib/bbc/types.ts` (Phase 1) → **reused, not duplicated**. **No team API functions existed** in `src/lib/api.ts` → added them (see §2). Note: there is **no `src/lib/bbc/api.ts`** in this codebase (the prompt assumed one); the real client is `src/lib/api.ts` — deviation noted in §8.

**A5 — user list / role filter:** `getUsers()` → `{ success, data, count }` (`GET /api/admin/users?limit=100`). Filtered client-side by `role` for pickers: `supervisor` (supervisor field), `project_manager` (PM field), `sales`/`support` (operator members).

**A6 — permissions:** `usePermissions(role)` from `@/lib/bbc/hooks`; role read from `useAuthStore(s => s.auth.user?.role)`. Page gated on `canManageTeams`.

**A7 — shadcn components present:** dialog, form, select, input, table, badge, switch, alert-dialog, dropdown-menu, command, popover, card — all available. Used Dialog/Form/Select/Input/Table/Badge/DropdownMenu/AlertDialog. Time via native `<input type="time">` (no shadcn time component exists).

**STOP conditions:** none — no existing `/teams` route/page; endpoints exist and match §1 shapes; routing is safely extensible.

---

## 2. api.ts / types.ts additions

**`src/lib/api.ts`** (new, mirroring `apiFetch` + existing error handling so backend 400/409 messages propagate as thrown `ApiError`):
- `getTeams({ is_active? }) → Team[]`
- `createTeam(body) → Team`
- `updateTeam(id, body) → Team`
- `deleteTeam(id) → void`
- `assignUserToTeam(userId, teamId | null) → void` (`PATCH /api/admin/users/{id}` `{ team_id }`)
- types `TeamCreatePayload`, `TeamUpdatePayload`; imports `Team` from `./bbc/types`.

**`types.ts`:** unchanged — reused Phase 1 `Team`.

---

## 3. §3.2 gating choice

**Management-only:** the Teams nav item and page are gated on **`canManageTeams`** (owner/admin/dev). PM/supervisor do **not** see it (their read-only view is a later phase). Nav item sits in the **Management** group, between Users and Knowledge Base, icon `UsersRound`. The page also guards in-component (renders a "Not authorized" state if `!canManageTeams`) as defense-in-depth; the backend independently enforces PRIVILEGED on writes.

---

## 4. `git diff --stat` (owned files only)

```
 M bbc-admin-app/src/components/layout/data/sidebar-data.ts   (Teams nav item)
 M bbc-admin-app/src/lib/api.ts                               (team client fns)
 M bbc-admin-app/src/routeTree.gen.ts                         (generated — /teams route)
?? bbc-admin-app/src/features/teams/index.tsx
?? bbc-admin-app/src/features/teams/data/types.ts
?? bbc-admin-app/src/features/teams/components/team-dialog.tsx
?? bbc-admin-app/src/features/teams/components/team-members-dialog.tsx
?? bbc-admin-app/src/features/teams/components/team-deactivate-dialog.tsx
?? bbc-admin-app/src/routes/_authenticated/teams/index.tsx
?? docs/reports/TEAMS_PHASE3_UI_REPORT.md
```
(Other dirty files — `bbc-chatbot-api/*`, Phase 1 FE files — belong to the **Phase 1** change already in this working tree; not part of this PR.)

`routeTree.gen.ts` is a generated file, rewritten by the router plugin as the required byproduct of adding the `/teams` route (the prompt explicitly asks to register the route). Flagged in §8.

---

## 5. Build + manual checklist

**Build:** `npm run build` (`tsc -b && vite build`) → **PASS, 0 TS errors** (`✓ built in 21.89s`; pre-existing >500 kB chunk-size warning only). ESLint/type lints on all new files: clean.

Route tree regenerated (verified `routeTree.gen.ts` now contains `/_authenticated/teams/`).

| Check | Result |
|-------|--------|
| Teams item in sidebar for owner/admin; hidden for sales/support/qa/PM/supervisor | **PASS** (gated on `canManageTeams`, true only for owner/admin/dev) |
| Non-management role at `/teams` → "Not authorized" | **PASS** (in-component guard + backend) |
| Create dialog: name + shift + supervisor + PM selects, submit → `createTeam`, invalidate `['teams']` | **PASS (static/build)** |
| 409 "supervisor already owns a team" surfaced to user | **PASS (wired)** — `apiErrorMessage` parses `{detail}` and toasts it; not swallowed |
| Non-supervisor not offered in supervisor field; backend 400 surfaced if forced | **PASS** — picker filtered to `role==='supervisor'`; 400 parsed to toast |
| Manage members: add/remove operators, counts refresh | **PASS (wired)** — invalidates `['teams']`, `['teams-users']`, `['users']` |
| Assign operator already on another team → moves | **PASS (wired)** — backend overwrites `team_id`; UI copy states this |
| Edit team (rename, shift) persists | **PASS (wired)** |
| Deactivate → confirm AlertDialog → `deleteTeam` | **PASS (wired)** |
| `git status` only owned files | **PASS** (+ generated route tree) |

**⚠️ Live-interaction checks were NOT exercised against a running backend.** No local backend was started, and the Phase 1 migration is not yet applied to any DB. Per the "NO PROD SIDE EFFECTS" rule I did **not** create teams against production. Checks marked "wired" are verified by code + a clean type-checked build, not a live click-through. The owner should do the live click-through after applying the Phase 1 migration to a dev/staging DB.

---

## 6. TEST- teams created

**None.** I did not create any TEST- teams (no local backend; refused to write to production). Nothing to clean up.

---

## 7. Sequencing note for the owner

- This PR (Phase 3 UI) depends on **Phase 1** (role + model + CRUD) being merged and the **`021_teams.sql` migration applied**. Order: Phase 1 → this UI → create real teams → **then** Phase 2 (team filtering).
- **Do NOT ship Phase 2 until real teams exist and operators/supervisors are assigned**, or supervisors/PMs see empty lists.
- The Phase 1 **`supervisor.canReadMessages` flip is live**: until Phase 2, a supervisor reads conversations across their whole **tunnel**, not just their team. Phase 2 narrows them to their team.

---

## 8. Deviations / didn't fit HEAD

1. **API client location:** prompt named `src/lib/bbc/api.ts`; it doesn't exist. Real client is `src/lib/api.ts` (where `getUsers`/`updateUser`/`getLeads` live). Added the team functions there — following intent.
2. **`routeTree.gen.ts`** (not in the literal ownership list) is regenerated automatically when the `/teams` route is added — an unavoidable, expected byproduct of the requested route registration.
3. **Table implementation:** used a simpler shadcn `Table` + local dialog state instead of the users page's `react-table` provider/columns/facet framework — same visual conventions, far less code, no new deps. Members multi-select uses a Select-to-add + list-to-remove (not a Command combobox) for simplicity; both use only existing shadcn components.
4. **`Team` type reused** from Phase 1 (`src/lib/bbc/types.ts`); `types.ts` untouched.
5. Live backend interaction not tested (see §5 caveat) to avoid production writes.

---

## Final

| Item | Value |
|------|--------|
| Page | `/teams` — create/edit, manage members, deactivate |
| Gating | management-only (`canManageTeams`) |
| API | 5 team fns added to `src/lib/api.ts` |
| Backend | untouched |
| Build | **PASS**, 0 TS errors |
| TEST teams | none created |
| Commit | **None** — awaiting owner approval |
