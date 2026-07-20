# Teams — Phase 1 Report (role `project_manager` + data model + admin CRUD + FE wiring)

**Date:** 2026-07-20  
**Branch:** `feat/engaged-ownership-list` (working tree; uncommitted)  
**Status:** Implementation complete — **NO commits / NO staging. Migration written, NOT executed.**

Additive & inert: the role exists but nobody has it; `team_id` columns exist but are NULL. The only intentional behavior change is **supervisor gains message-read** (§FE-3.2.2 — see caveat, needs owner confirmation).

---

## 1. Phase A answers (verbatim "before")

**A1 — `users.py` role sets (before):**
```python
VALID_ROLES = {"owner", "admin", "dev", "sales", "support", "supervisor", "qa"}
PRIVILEGED = {"owner", "admin", "dev"}
CAN_LIST_USERS = PRIVILEGED | {"supervisor"}
```

**A2 — `supabase.py` role tuples (before):**
```python
_MANAGEMENT_ROLES = ("owner", "admin", "dev", "supervisor", "qa")
_OPERATOR_ROLES = ("sales", "support")
_HANDS_ON_ROLES = _OPERATOR_ROLES + ("owner", "admin", "dev")
```

**A3 — `conversations.py` `_enforce_tunnel` privileged (before):**
```python
if role in ("owner", "admin", "dev", "supervisor", "qa"):
    return tunnel
```

**A4 — `leads.py` (THE TRAP):**
```python
def _enforce_tunnel(user, tunnel):
    if role in ("owner", "admin", "dev", "supervisor", "qa"): return tunnel
...
if user.get("role") not in ("owner", "admin", "supervisor", "qa"):
    raise HTTPException(403, "Leads access restricted to admin/owner")
```
`project_manager` is **NOT** added to either list → PM gets the desired 403. **Zero changes to `leads.py`.**

**A5 — Next migration:** highest is `020_add_qa_review.sql` → next is **`021`**. Reused the discover-then-swap `DO $$` CHECK block from `011`/`013` verbatim (constraint name discovered via `pg_constraint`, never hardcoded).

**A6 — `supabase.py` conventions:** async wrappers over sync supabase-py via `await _run_sync(lambda: db.table(...).execute())`; errors are logged and return `None`/`[]`/`0` (never raised into the endpoint); `updated_at` stamped from `datetime.now(timezone.utc).isoformat()`. **RLS:** no table in `migrations/` declares RLS or policies — the app uses the `service_role` key which bypasses RLS entirely. (So there is no existing policy "shape" to copy; see §2 note.)

**A7 — `main.py` admin router pattern:**
```python
admin_deps = [Depends(get_current_user)]
app.include_router(users_router, prefix="/api", tags=["users"], dependencies=admin_deps)
```

**A8 — `models/admin.py`:** plain `BaseModel` classes, `Optional[...] = None` fields, no validators. `UserUpdate` had: name, role, tunnel_scope, is_active, phone, avatar_url.

**A9 — Test conventions:** `sys.path` insert + `os.environ.setdefault` for Supabase/Anthropic; `with patch("app.db.supabase.get_client"): from app.main import app`; `httpx.AsyncClient(ASGITransport(app))`; DB functions patched with `AsyncMock`. Existing auth in tests via Basic header. **New file uses `app.dependency_overrides[get_current_user]`** to act as any role (cleaner than minting JWTs).

**A10 — user-update allow-list:** effectively the `UserUpdate` model fields, dumped via `model_dump(exclude_none=True)`. Added `team_id` (with explicit-null handling — see §BE-3.3).

**A11 — `getPermissions()` (before):** explicit `case` for owner/admin/dev, qa, sales, support, supervisor, and a `default` (fail-closed) branch. Return object = the `Permissions` interface. `supervisor.canReadMessages` was **`false`**.

**A12 — `types.ts`:** `UserRole = 'owner'|'admin'|'dev'|'qa'|'sales'|'support'|'supervisor'`. `Permissions` interface had 15 `canX`/`visibleTunnels` fields, no team flags. No `Team` type. `BBCUser` had no `team_id`.

**A13 — `schema.ts`:** `userRoleSchema` = union of owner/admin/sales/support/supervisor/qa (note: `dev` already absent — pre-existing gap, left as-is).

**A14 — `sidebar-data.ts`:** nav is **permission-flag gated** (not a role array): Leads on `canViewLeads`, Users on `canViewUsers`, KB on `canViewKB`, Tasks on `canAssignTasks`. Dashboard + Conversations always shown.

**A15 — dialogs:** both `users-invite-dialog.tsx` and `users-action-dialog.tsx` render selectable roles from the shared `roles` array in `features/users/data/data.ts`, and each has an inline `tunnelMap` (`sales/support/owner/admin/qa`).

**A16 — `canReadMessages` consumers:** `features/chats/detail.tsx` L274 (`if (!permissions.canReadMessages)` → renders the "Message content is not visible" supervisor placeholder instead of the transcript) and L466 (gates the lead edit panel, also requires `canEditLeads`). Flipping supervisor → true reveals the **conversation transcript** in the chat detail view.

**A17 — role source:** **REAL session, not mocked.** Role is decoded from the `bbc_admin_token` JWT claims and set into the auth store at `routes/_authenticated/route.tsx`, `sign-in/.../user-auth-form.tsx`, `lib/crm-embed-auth.ts`, and `settings/profile/profile-form.tsx`. The `hooks.ts` "V1 mock" comment is stale — gating is wired to the live session.

**STOP conditions:** none triggered — no `teams`/`team_id`/`project_manager` anywhere; role lists structurally as expected; `DO $$` pattern present; `getPermissions` has a `default` and role is session-driven.

---

## 2. Migration (written, NOT applied)

`bbc-chatbot-api/migrations/021_teams.sql` — idempotent. Extends the `users.role` CHECK to include `project_manager` (reusing the 011/013 discover-then-swap block), creates `public.teams`, adds nullable `team_id` to `users`/`conversations`/`leads`, 5 indexes, and enables RLS on `teams` with a `service_role FOR ALL` policy.

**RLS note:** no other table declares RLS in this repo (they rely on the service_role key bypassing RLS). Enabling RLS on `teams` + a permissive `service_role` policy is safe (service_role bypasses RLS regardless) and explicit. If the owner prefers strict parity with the other tables, the RLS block can be dropped — behavior is identical either way for the app.

**Night-shift wrap note:** `shift_start`/`shift_end` are informational in Phase 1 (no time routing). A night shift (e.g. 22:00–06:00) has `end < start`; any future time-based logic must handle the wrap.

---

## 3. Role touchpoint table (all 12)

| # | Layer / file | Change |
|---|--------------|--------|
| 1 | `migrations/021_teams.sql` role CHECK | **+ project_manager** |
| 2 | `supabase.py` `_MANAGEMENT_ROLES` | **+ project_manager** |
| 3 | `supabase.py` `_OPERATOR_ROLES` | **NO CHANGE** (PM does not receive chats) |
| 4 | `supabase.py` `_HANDS_ON_ROLES` | **NO CHANGE** (PM does not write chats) |
| 5 | `users.py` `VALID_ROLES` | **+ project_manager** |
| 6 | `users.py` `CAN_LIST_USERS` | **+ project_manager** |
| 7 | `users.py` `PRIVILEGED` | **NO CHANGE** (PM must not manage users) |
| 8 | `conversations.py` `_enforce_tunnel` | **+ project_manager** |
| 9 | `leads.py` (tunnel + access gate) | **NO CHANGE — THE TRAP** (PM must NOT see leads → 403) |
| 10 | FE `types.ts` `UserRole` | **+ project_manager** |
| 11 | FE `hooks.ts` `getPermissions` | **explicit `case 'project_manager'`** (never default) |
| 12 | FE `schema.ts` + `data.ts` + both dialogs | **+ project_manager** (selectable, label "Project Manager", tunnel→all) |

---

## 4. FE permission matrix implemented (`project_manager`)

| Flag | Value | Rationale |
|------|-------|-----------|
| canViewAllConversations | true | oversees teams' chats |
| canReadMessages | true | must see operator messages (scoping = Phase 2, backend-enforced) |
| canReassignConversations | false | PM observes |
| **canViewLeads** | **false** | **owner decision; backend 403s PM on `/api/leads` — UI mirrors it** |
| canEditLeads | false | follows |
| canViewUsers | true | needs to see its teams' operators (backend `CAN_LIST_USERS` includes PM) |
| canEditUsers | false | PM ∉ PRIVILEGED |
| canViewDashboardGlobal | true | oversight |
| canViewTeams | true | new flag |
| canManageTeams | false | admin/owner/dev only |
| write in chat | false | PM ∈ management, ∉ hands-on |

New flags set for existing roles: owner/admin/dev → view+manage **true**; supervisor → view **true**, manage **false**; qa → view **true**, manage **false**; sales/support → both **false**; `default` → both **false** (fail closed).

`canViewLeads=false` ties UI to backend: PM hitting `/api/leads` gets 403 (`test_pm_forbidden_on_leads`), and the sidebar hides Leads because it is gated on `canViewLeads`.

---

## 5. Supervisor `canReadMessages` false → true (§FE-3.2.2)

**Surface unlocked (from A16):** the **conversation transcript** in `features/chats/detail.tsx`. Before, a supervisor saw only the "Message content is not visible / you can reassign" placeholder; now they see the actual messages.

**⚠️ INTERIM WIDENING CAVEAT — needs owner decision:** team-based filtering is **Phase 2**. Until it ships, a supervisor with `canReadMessages=true` can read conversations across their **whole tunnel**, not only their team. A code comment documents this. **Owner: please confirm you accept this interim tunnel-wide read for supervisors, OR tell us to defer FE-3.2.2 to Phase 2** (revert the single `supervisor.canReadMessages` flag to `false`). Everything else in this PR is inert regardless of that choice.

---

## 6. Git status + diff --stat (owned files)

Modified/new — all within ownership **except one flagged deviation** (`data.ts`, see §10):
```
 M bbc-admin-app/src/features/users/components/users-action-dialog.tsx
 M bbc-admin-app/src/features/users/components/users-invite-dialog.tsx
 M bbc-admin-app/src/features/users/data/data.ts        ← DEVIATION (see §10)
 M bbc-admin-app/src/features/users/data/schema.ts
 M bbc-admin-app/src/lib/bbc/hooks.ts
 M bbc-admin-app/src/lib/bbc/types.ts
 M bbc-chatbot-api/app/api/conversations.py
 M bbc-chatbot-api/app/api/users.py
 M bbc-chatbot-api/app/db/supabase.py
 M bbc-chatbot-api/app/main.py
 M bbc-chatbot-api/app/models/admin.py
?? bbc-chatbot-api/app/api/teams.py
?? bbc-chatbot-api/app/models/teams.py
?? bbc-chatbot-api/migrations/021_teams.sql
?? bbc-chatbot-api/tests/test_teams.py
?? docs/reports/TEAMS_PHASE1_REPORT.md
```
`sidebar-data.ts` (owned) intentionally **unchanged** — flag-based gating already yields the correct PM menu (Dashboard, Conversations, Users; no Leads; no Teams item). `leads.py` untouched.

---

## 7. Test results

- **`tests/test_teams.py`: 16 passed.** Key cases:
  - **#3 PM → 403 on `/api/leads`** (`test_pm_forbidden_on_leads`) — the trap guard. ✅
  - supervisor/qa/owner/admin still 200 on leads (regression). ✅
  - #4 create teams: owner OK; project_manager/supervisor/sales/qa → 403. ✅
  - #5 list scoping: admin (no pm/supervisor filter), PM (`pm_id=me`), supervisor (`supervisor_id=me`); sales → 403. ✅
  - #6 supervisor already owns active team → 409. ✅
  - #7 supervisor_id→non-supervisor → 400; pm_id→non-PM → 400. ✅
  - #8 PATCH user team_id: valid → 200, non-existent → 400, explicit `null` → 200 (and forwarded to DB, not dropped). ✅
  - #1/#2 role registration + classification (`_MANAGEMENT_ROLES` ∋ PM; ∉ operator/hands-on). ✅
  - **#9 regression:** sales still tunnel-forced (403 on cross-tunnel), owner conversations 200. ✅
- **Full backend suite: 185 passed, 4 failed.** The 4: 3 pre-existing `test_generator` failures (SmartRouting/BudgetGuard — unrelated, present before this work) + `test_internal_scheduler::test_run_forever_survives_repeated_job_failures`, which **passes in isolation (6/6)** — order/timing-flaky, not caused by this PR.
- **Frontend `npm run build`: PASS** — `tsc -b && vite build`, 0 TS errors (chunk-size warning pre-existing). The `UserRole` union addition ripples cleanly through dialogs, schema, and the users route filter.
- **`grep project_manager app/api/leads.py` → 0 matches** (trap respected).

---

## 8. §FE-3.6 consistency-guard output

`getPermissions` cases: `owner`, `admin`, `dev`, `qa`, `sales`, `support`, `supervisor`, `project_manager` + `default`. **Every backend role has an explicit FE case.**

`project_manager` present in FE: `hooks.ts`, `types.ts`, `schema.ts`, `data.ts`, `users-invite-dialog.tsx`, `users-action-dialog.tsx`. (Sidebar is flag-based, so no role literal there — correct.)

**Pre-existing gap noted (not fixed — out of scope):** `dev` is absent from `schema.ts` `userRoleSchema` and from `data.ts` `roles`. It predates this PR; a `dev` user row would fail the users-list zod parse. Flagging for a future cleanup.

---

## 9. Owner merge checklist

- [ ] Apply `migrations/021_teams.sql` in Supabase SQL Editor, then verify:
  - `SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname LIKE '%role%';` includes `project_manager`
  - `SELECT to_regclass('public.teams');` → not null
  - `SELECT table_name FROM information_schema.columns WHERE column_name='team_id';` → 3 rows (users, conversations, leads)
- [ ] Decide on **§5 supervisor interim widening** (accept, or defer FE-3.2.2 to Phase 2).
- [ ] Merge order: land **engaged-ownership** PR and **auth-hardening** PR first, then this. (This PR's `supabase.py` / `conversations.py` edits are additive and should rebase cleanly on the engaged-ownership OR-filter change.)
- [ ] **Phase 1.5 (after merge): create real teams + assign supervisors/operators BEFORE any team-filtering phase**, or supervisors/PMs see empty screens.

---

## 10. Deviations / didn't fit HEAD

1. **File-ownership deviation (flagged):** `features/users/data/data.ts` is **not** in the owned list, but both owned dialogs render their selectable roles from its shared `roles` array (the prompt assumed the role list lived inline in each dialog — A15 shows it does not). To make "Project Manager" selectable in **both** dialogs without duplicating the list, I added one entry (label "Project Manager", `Briefcase` icon) to `data.ts`. The inline `tunnelMap` additions (`supervisor`/`project_manager` → `all`) were made in the two owned dialog files as specified. **Please confirm this single shared-file edit is acceptable.**
2. `sidebar-data.ts` needed no change (flag-based gating) — left untouched though owned.
3. Delete-team policy choice: **soft delete + refuse with 409 if members still reference the team** (admin reassigns first). Documented and consistent; reversible.
4. `dev` role pre-existing gap in FE `schema.ts`/`data.ts` (see §8) — not touched (out of scope).
5. Team CRUD reads role from the JWT user dict (matching sibling `users.py`), not a fresh DB lookup. Consistent with the existing admin-router pattern; note for reviewers who expect the "role from DB" pattern used in `agent.py`/claim.
6. `_issue_jwt` already carries `role`/`tunnel_scope` (unchanged) — no login impact from the new role.

---

## Final

| Item | Value |
|------|--------|
| Backend | migration + role lists + team CRUD + team_id allow-list |
| Frontend | UserRole/Permissions/Team + PM permission case + supervisor read + dialogs/schema |
| `leads.py` | **untouched** (PM 403 preserved) |
| `PRIVILEGED` | **unchanged** (PM ∉ user management) |
| Tests | test_teams **16/16**; full suite 185 pass (4 unrelated/flaky) |
| Build | frontend **PASS** |
| Migration | **written, NOT applied** |
| Commit | **None** — awaiting owner approval |
