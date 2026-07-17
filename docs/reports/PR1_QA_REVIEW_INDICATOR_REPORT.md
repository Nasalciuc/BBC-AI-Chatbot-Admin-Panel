# PR1 — QA Review Indicator Report

**Date:** 2026-07-17  
**Branch:** `feat/crm-embed-widget` (HEAD `44ab51e`)  
**Status:** Implementation completed — uncommitted. No git write. No prod side effects.

---

## 1. Phase A — Investigation (A1–A8)

### A1 — Git status / recent commits

```
On branch feat/crm-embed-widget
Your branch is up to date with 'origin/feat/crm-embed-widget'.

(many unrelated modified/untracked files already present in workspace)

44ab51e fix: allow crm.test to frame dashboard + CORS
ff7eca5 fix: allow crm.buybusinessclass.com to frame the operator dashboard
ff66f25 Merge branch 'master' into feat/crm-embed-widget
```

Owned files had no content diff vs HEAD before this PR (CRLF noise only).

### A2 — Leads table structure (before)

No "Reviewed" column. Headers were a fixed string array; Score cell then Contact.

**Verbatim before — header / Score cell:**

```tsx
{['Score', 'Contact', 'Route', 'Tier', 'Status', 'Departure', 'Action'].map(h => (
  <th key={h} className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wide">{h}</th>
))}
...
<td className="px-4 py-3"><ScoreBadge score={lead.score} /></td>
<td className="px-4 py-3">
  <div className="font-medium text-gray-900">
```

**Verbatim before — lucide / API imports:**

```tsx
import { Search, Filter, Phone, Mail, Plane, ChevronDown, RefreshCw } from 'lucide-react'
import type { Lead } from '@/lib/types'
import { getLeads, updateLeadStatus } from '@/lib/api'
```

**Verbatim before — canReview / review filter / counters (already present):**

```tsx
const canReview = ['owner', 'admin', 'supervisor', 'qa'].includes(user?.role || '')
...
if (reviewFilter !== 'all') params.reviewed = reviewFilter
...
if (json.review_stats) {
  setReviewedCount(json.review_stats.reviewed)
  setTotalCount(json.review_stats.total)
}
```

List data path: `useState` + `getLeads` + `fetchLeads` (not React Query).

### A3 — Lead detail drawer (before)

QA Review UI already existed. Error toast was generic.

**Verbatim before — reviewMutation.onError:**

```tsx
onError: () => toast.error('Failed to update review status'),
```

**Verbatim before — reviewMutation success invalidation:**

```tsx
onSuccess: (_data, reviewed) => {
  queryClient.invalidateQueries({ queryKey: ['leads'] })
  queryClient.invalidateQueries({ queryKey: ['lead', leadId] })
  toast.success(reviewed ? 'Marked as reviewed' : 'Review cleared')
},
```

### A4 — `reviewed_by_qa` grep in `bbc-admin-app/src/`

| Location | Finding |
|----------|---------|
| `lib/types.ts` L70 | `reviewed_by_qa?: boolean` on `Lead` |
| `lead-detail-drawer.tsx` | Uses `lead.reviewed_by_qa` for label + toggle |
| `index.tsx` (before) | No column / no field usage in table cells |

### A5 — Lead type / typed? — VERDICT

**Typed on the list `Lead` type. No local assertion needed.**

- Leads page imports `Lead` from `@/lib/types` (not `@/lib/bbc/types`).
- At HEAD, `bbc-admin-app/src/lib/types.ts` already includes:

```ts
reviewed_by_qa?: boolean
reviewed_at?: string | null
reviewed_by?: string | null
```

- Prompt ownership note pointed at `lib/bbc/types.ts` (PR2). That file has a **different demo `Lead`** without `reviewed_by_qa`. It is unused by the leads page. **Did not edit types files.**

### A6 — `reviewLead` API client

Lives in `bbc-admin-app/src/lib/api.ts`:

```ts
export async function reviewLead(
  leadId: string,
  reviewed: boolean,
  qaNotes?: string,
) {
  return apiFetch<{ success: boolean; reviewed: boolean }>(`/api/leads/${encodeURIComponent(leadId)}/review`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reviewed, qa_notes: qaNotes }),
  })
}
```

`ApiError extends Error` → `e.message` works for toast surfacing.

### A7 — Lucide imports in `leads/index.tsx` (before)

```tsx
import { Search, Filter, Phone, Mail, Plane, ChevronDown, RefreshCw } from 'lucide-react'
```

No `Check` / `X` before this PR.

### A8 — Backend `get_leads` returns `reviewed_by_qa`?

**Yes.** `bbc-chatbot-api/app/db/supabase.py` `get_leads` selects `"*, conversations!inner(...)"`, so `reviewed_by_qa` is on each row. Filters and `review_stats` already wired via `reviewed` query param + `get_leads_review_counts`. Review write path: `PATCH /api/leads/{id}/review`.

**STOP conditions:** not triggered (no existing Reviewed column; field typed + returned by API).

---

## 2. Phase B — What changed

### 3.1 Precondition

Satisfied via A5 — use `lead.reviewed_by_qa` directly on `Lead`.

### 3.2 Column (`index.tsx`)

- Header `Reviewed` inserted immediately after `Score`, gated on `canReview`.
- Matching `<td>` after Score cell, gated on `canReview`.
- Icons: lucide `Check` (green) / `X` (red) with `aria-label` and `title`.

### 3.3 Click-to-toggle (`index.tsx`)

- Button + `stopPropagation` so row click does not open drawer.
- `pendingId` in-flight guard (blocks concurrent toggles).
- `reviewLead(lead.id, !lead.reviewed_by_qa)` then **existing** `fetchLeads()` (refreshes rows + `review_stats` counter).
- Errors: `toast.error` with real `e.message` and fallback.

### 3.4 Honest errors (`lead-detail-drawer.tsx` only)

```tsx
onError: (e) => toast.error(e instanceof Error ? e.message : 'Failed to update review status'),
```

Nothing else in the drawer changed.

---

## 3. Full diff (owned files only)

```diff
diff --git a/bbc-admin-app/src/features/leads/components/lead-detail-drawer.tsx b/bbc-admin-app/src/features/leads/components/lead-detail-drawer.tsx
index f53b382..1fb4590 100644
--- a/bbc-admin-app/src/features/leads/components/lead-detail-drawer.tsx
+++ b/bbc-admin-app/src/features/leads/components/lead-detail-drawer.tsx
@@ -97,7 +97,7 @@ export function LeadDetailDrawer({ leadId, onClose }: Props) {
       queryClient.invalidateQueries({ queryKey: ['lead', leadId] })
       toast.success(reviewed ? 'Marked as reviewed' : 'Review cleared')
     },
-    onError: () => toast.error('Failed to update review status'),
+    onError: (e) => toast.error(e instanceof Error ? e.message : 'Failed to update review status'),
   })
 
   const messages: Message[] = conversation?.messages ?? []
diff --git a/bbc-admin-app/src/features/leads/index.tsx b/bbc-admin-app/src/features/leads/index.tsx
index 0eb0af6..1a60d2c 100644
--- a/bbc-admin-app/src/features/leads/index.tsx
+++ b/bbc-admin-app/src/features/leads/index.tsx
@@ -1,7 +1,8 @@
 import { useState, useEffect, useCallback } from 'react'
-import { Search, Filter, Phone, Mail, Plane, ChevronDown, RefreshCw } from 'lucide-react'
+import { Search, Filter, Phone, Mail, Plane, ChevronDown, RefreshCw, Check, X } from 'lucide-react'
+import { toast } from 'sonner'
 import type { Lead } from '@/lib/types'
-import { getLeads, updateLeadStatus } from '@/lib/api'
+import { getLeads, updateLeadStatus, reviewLead } from '@/lib/api'
 import { Header } from '@/components/layout/header'
 import { Main } from '@/components/layout/main'
 import { ConnectionBanner } from '@/components/connection-banner'
@@ -66,6 +67,7 @@ export function Leads() {
   const [reviewFilter, setReviewFilter] = useState('all')
   const [offset, setOffset]         = useState(0)
   const [updatingId, setUpdatingId] = useState<string | null>(null)
+  const [pendingId, setPendingId] = useState<string | null>(null)
   const [selectedLeadId, setSelectedLeadId] = useState<string | null>(null)
   const LIMIT = 50
 
@@ -104,7 +106,31 @@ export function Leads() {
     }
   }
 
+  const toggleReview = async (lead: Lead) => {
+    if (pendingId) return
+    setPendingId(lead.id)
+    try {
+      await reviewLead(lead.id, !lead.reviewed_by_qa)
+      await fetchLeads()
+    } catch (e) {
+      const message = e instanceof Error ? e.message : 'Failed to update review status'
+      toast.error(message || 'Failed to update review status')
+    } finally {
+      setPendingId(null)
+    }
+  }
+
   const sortedLeads = [...(leads || [])].sort((a, b) => (b.score || 0) - (a.score || 0))
+  const tableHeaders = [
+    'Score',
+    ...(canReview ? ['Reviewed'] : []),
+    'Contact',
+    'Route',
+    'Tier',
+    'Status',
+    'Departure',
+    'Action',
+  ]
 
   return (
     <>
@@ -222,7 +248,7 @@ export function Leads() {
               <table className="w-full text-sm">
                 <thead>
                   <tr className="border-b border-gray-100 bg-gray-50">
-                    {['Score', 'Contact', 'Route', 'Tier', 'Status', 'Departure', 'Action'].map(h => (
+                    {tableHeaders.map(h => (
                       <th key={h} className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wide">{h}</th>
                     ))}
                   </tr>
@@ -231,6 +257,24 @@ export function Leads() {
                   {sortedLeads.map(lead => (
                     <tr key={lead.id} className="hover:bg-gray-50 transition-colors cursor-pointer" onClick={() => setSelectedLeadId(lead.id)}>
                       <td className="px-4 py-3"><ScoreBadge score={lead.score} /></td>
+                      {canReview && (
+                        <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
+                          <button
+                            type="button"
+                            disabled={pendingId === lead.id}
+                            onClick={() => toggleReview(lead)}
+                            className="inline-flex items-center justify-center p-1 rounded hover:bg-gray-100 disabled:opacity-50"
+                            aria-label={lead.reviewed_by_qa ? 'Mark as unreviewed' : 'Mark as reviewed'}
+                            title={lead.reviewed_by_qa ? 'Reviewed — click to clear' : 'Not reviewed — click to mark'}
+                          >
+                            {lead.reviewed_by_qa ? (
+                              <Check className="w-4 h-4 text-green-600" aria-hidden />
+                            ) : (
+                              <X className="w-4 h-4 text-red-600" aria-hidden />
+                            )}
+                          </button>
+                        </td>
+                      )}
                       <td className="px-4 py-3">
                         <div className="font-medium text-gray-900">
                           {lead.visitor_name ?? <span className="text-gray-400 italic text-xs">Anonymous</span>}
```

### git status + git diff --stat (owned files)

```
 M bbc-admin-app/src/features/leads/components/lead-detail-drawer.tsx
 M bbc-admin-app/src/features/leads/index.tsx
?? docs/reports/PR1_QA_REVIEW_INDICATOR_REPORT.md

 .../leads/components/lead-detail-drawer.tsx        |  2 +-
 bbc-admin-app/src/features/leads/index.tsx         | 50 ++++++++++++++++++++--
 2 files changed, 48 insertions(+), 4 deletions(-)
```

(Report path is new/untracked; not staged.)

---

## 4. Build result + manual checklist

### Build

```
cd bbc-admin-app
npm ci          # OK (533 packages)
npm run build   # PASS — tsc -b && vite build, BUILD_EXIT=0
```

### Manual checklist

| Check | Result |
|-------|--------|
| Build / TypeScript | PASS |
| Reviewed column for `canReview` roles | Code present; not exercised in browser (no local admin session against API in this run) |
| Hidden for non-review roles | Gated on existing `canReview` |
| Click toggles + refresh via `fetchLeads` | Wired; not live-toggled (no prod lead writes) |
| Counter updates after table toggle | Via same `fetchLeads` → `review_stats` |
| Unreviewed filter row disappear | Expected after `fetchLeads`; not live-verified |
| Drawer honest error toast | Code change only |
| Toggle a real lead then toggle back | **Skipped** — backend not exercised; avoided production writes |

---

## 5. A5 verdict (summary)

| Question | Answer |
|----------|--------|
| On list `Lead` type? | **Yes** — `@/lib/types` `reviewed_by_qa?: boolean` |
| Returned by backend list? | **Yes** — `select("*", ...)` |
| Local type assertion? | **No — not needed** |
| Edited `types.ts` / `bbc/types.ts`? | **No** |

---

## 6. Known-bug note — toggle write path

**Drawer review success does not refresh the leads table or the reviewed counter.**

- Drawer `onSuccess` calls `queryClient.invalidateQueries({ queryKey: ['leads'] })`.
- The leads **page** does **not** use React Query for the list; it uses `useState` + `fetchLeads`.
- Therefore a review toggle **inside the drawer** updates the drawer query (`['lead', leadId]`) but leaves the table icons / `reviewedCount` stale until the user hits Refresh or changes a filter (or toggles from the new table button, which correctly calls `fetchLeads()`).

**Out of PR1 scope** to rewire the list onto React Query or pass an `onReviewed` callback. Table-path toggle is correct; drawer→list sync remains a pre-existing gap.

Secondary: `ApiError` message is often the raw HTTP body text (may be JSON), not a parsed `detail` string — toasts are honest but not always pretty.

---

## 7. Anything that didn’t fit HEAD / prompt assumptions

1. Prompt cited `lib/bbc/types.ts` as the list Lead type; runtime list uses `@/lib/types`. `bbc/types.ts` Lead is a separate demo shape.
2. Review filter, counters, drawer QA UI, and `reviewLead` already existed at HEAD — PR1 only adds the table indicator + toggle + honest drawer errors.
3. `activeTab` is in `fetchLeads` deps but not sent as a query param (pre-existing; untouched).
4. Workspace had many unrelated dirty files; this PR only touched the two owned source files + this report.

---

## Final

| Item | Value |
|------|--------|
| Implementation | **Completed** |
| Stopped early? | No |
| A5 | Typed — no assertion |
| Build | **PASS** |
| Commit | **None** (per absolute rules) |
| Blockers | None for code; live QA toggle not verified (no session / no prod writes) |
