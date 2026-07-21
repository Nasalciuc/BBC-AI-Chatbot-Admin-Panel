# Teams UI polish — Report (inline "Add team" in header + searchable operator picker)

**Date:** 2026-07-21
**Branch:** `feat/team-switcher` (working tree; **uncommitted**)
**Scope:** FRONTEND-ONLY, two small Teams-UI deltas in one PR. **NO commits / NO prod side effects.**

---

## 1. Investigation (A1–A5)

### A1 — Current "Add team" handler (`team-switcher.tsx`)
Navigated away:
```tsx
const navigate = useNavigate()
...
<DropdownMenuItem className='gap-2 p-2' onClick={() => navigate({ to: '/teams' })}>
  ... Add team
</DropdownMenuItem>
```

### A2 — Reusable create dialog?
**YES.** `features/teams/components/team-dialog.tsx` exports `TeamDialog` with controlled props:
```tsx
type Props = { open: boolean; onOpenChange: (open: boolean) => void;
               team?: Team | null; supervisors: TeamUser[]; projectManagers: TeamUser[] }
```
It self-handles create → `toast` → `queryClient.invalidateQueries(['teams'])` → `onOpenChange(false)`. **Reused** (imported into the header) — no new dialog built.

### A3 — `createTeam` + option lists + `setActiveTeam`
- `createTeam(payload)` returns the created `Team`, but **`TeamDialog` consumes it internally and does NOT surface the new id** to callers.
- Option lists (Teams page): `users.filter(u => u.role === 'supervisor')` / `=== 'project_manager'`, from `getUsers()` (`['teams-users']` query).
- `setActiveTeam` exists in `useTeamStore`. **But** since `TeamDialog` doesn't return the new id, per the prompt's fallback ("If the new id isn't returned, just invalidate + close") I do **not** call `setActiveTeam(newId)` — the dialog invalidates `['teams']` and the dropdown refreshes with the new team. (Auto-selecting the new team would require modifying the page's dialog, which is out of ownership.)

### A4 — Manage-members operator picker (before) + selected-state contract
Plain shadcn `Select` bound to `addValue`; **Add** consumes `addValue`:
```tsx
const [addValue, setAddValue] = useState<string>('')
<Select value={addValue} onValueChange={setAddValue}> ...
  {eligible.map(u => <SelectItem value={u.id}>{`${u.name…} (${u.role})`}</SelectItem>)}
</Select>
<Button onClick={() => add(addValue)} disabled={!addValue || …}>Add</Button>
```
Kept the **same `addValue` contract** — only the picking UI changed.

### A5 — `command.tsx` + `popover.tsx` present?
**YES both.** `components/ui/command.tsx` (exports `Command, CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem`) and `components/ui/popover.tsx` (`Popover, PopoverTrigger, PopoverContent`). → **Combobox (Command+Popover)**, not the filtered-input fallback.

**STOP conditions:** none — "Add team" navigated (not inline); picker was a plain Select.

---

## 2. Changes

### CHANGE 1 — Header "Add team" → inline `TeamDialog` (reuse, no navigation)
`team-switcher.tsx`:
- Removed `useNavigate`; added `const [createOpen, setCreateOpen] = useState(false)`.
- Added a `['teams-users']` query (enabled only when `canManageTeams`, shares the page's cache) → derives `supervisors` / `projectManagers` for the dialog.
- "Add team" `onClick` now `() => setCreateOpen(true)` (no router push).
- Render `<TeamDialog open={createOpen} onOpenChange={setCreateOpen} supervisors PMs />` (only when `canManageTeams`). It invalidates `['teams']` and closes on success → dropdown refreshes.
- **0-teams fix:** early-return-to-brand condition changed from `teams.length === 0` to `teams.length === 0 && !canManageTeams`, so a manager with no teams still gets the dropdown (and thus "Add team") to create the first team from the header.

### CHANGE 2 — Searchable operator picker (`team-members-dialog.tsx`)
- Replaced the `Select` with a **Command+Popover combobox**: trigger button shows the selected operator label or "Select an operator" (or "No eligible operators" when empty, disabled); `CommandInput` "Search operators…"; `CommandEmpty` "No operator found."; one `CommandItem` per eligible operator labelled `Name (role)` with a check on the selected one.
- Selecting sets the **same `addValue`** state and closes the popover; **Add** unchanged. `selectedLabel` derived via `useMemo`. Case-insensitive filtering is handled by `cmdk` on the item `value` (the `Name (role)` label).
- Member list, remove (`X`), counts, "belongs to one team" copy, Done button — **unchanged**.

---

## 3. `git status` + `git diff --stat` (owned files only)

```
 M bbc-admin-app/src/components/layout/team-switcher.tsx                    | 43 ++++--
 M bbc-admin-app/src/features/teams/components/team-members-dialog.tsx      | 99 ++++++++---
```
No other `features/teams/*` file touched (`team-dialog.tsx` is **import-only**). No store/switching-logic/backend changes. `team-store.ts` untouched.

---

## 4. Build + checklists

**Build:** `tsc -b && vite build` → **PASS, 0 TS errors** (`✓ built in 47.36s`).

**CHANGE 1**
| Check | Result |
|-------|--------|
| "Add team" opens dialog in place (no navigation), fields name+shift+supervisor+PM | **PASS** (static/build — reuses `TeamDialog`) |
| Create TEST- team → appears in dropdown; no page change | **PASS (wired)** — dialog invalidates `['teams']`; dropdown re-reads |
| supervisor already owns a team → 409 toast | **PASS (wired)** — `TeamDialog` `apiErrorMessage` toasts backend `{detail}` |
| Works with 0 teams (create first from header) | **PASS** — early-return relaxed for `canManageTeams` |
| Switching + sales/support (no switcher) unchanged | **PASS** — switching logic + `canViewTeams` gating untouched |

**CHANGE 2**
| Check | Result |
|-------|--------|
| Picker has a search box; typing narrows; clearing shows all | **PASS (wired)** — `cmdk` filters on `Name (role)` |
| "No operator found" for non-matching query | **PASS** — `CommandEmpty` |
| Selecting filtered operator + Add assigns as before | **PASS** — same `addValue` + `add()` path |
| Role shown per option ("Name (sales)") | **PASS** |
| Rest of dialog (members, Done) unchanged | **PASS** |

> Verified by clean typed build + code paths. A live browser click-through was not run in this environment.

---

## 5. TEST- teams / assignments created

**None.** No live UI/browser session was run, so no TEST- teams were created and no operators were assigned/unassigned. Nothing to undo. (Consistent with the no-prod-side-effects rule; live click-through is the owner's to run with `npm run dev`.)

---

## 6. Didn't fit HEAD / deviations

1. **No `setActiveTeam(newId)` after create.** `TeamDialog` doesn't expose the created id (it consumes `createTeam`'s return internally). Per the prompt's own fallback, I invalidate + close instead of auto-selecting. Auto-select would require editing the page's dialog (out of ownership). Easy follow-up if wanted: have `TeamDialog` accept an optional `onCreated?(team)` callback.
2. **0-teams dropdown for managers.** Had to relax the switcher's brand-only early return so a manager with 0 teams can reach "Add team" (create the first team from the header). Non-managers with 0 teams still get the plain brand entry (unchanged).
3. **`['teams-users']` fetch added to the header** (enabled only for `canManageTeams`) to feed the dialog's supervisor/PM pickers — shares the Teams page cache key, so no duplicate network when the page has already loaded.
4. Combobox width uses `w-(--radix-popover-trigger-width)` (Tailwind v4 paren syntax), matching the switcher's existing `w-(--radix-dropdown-menu-trigger-width)`.

---

## Final

| Item | Value |
|------|-------|
| CHANGE 1 | Header "Add team" → inline `TeamDialog` (reused), works with 0 teams, no navigation |
| CHANGE 2 | Manage-members operator picker → Command+Popover searchable combobox |
| Contracts kept | `addValue` + `add()`; switching logic; assign API; store |
| Files | 2 owned edited (`team-switcher.tsx`, `team-members-dialog.tsx`); `team-dialog.tsx` import-only |
| Build | **PASS**, 0 TS errors |
| Commit | **None** — awaiting owner approval |
