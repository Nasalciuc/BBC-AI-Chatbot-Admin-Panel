# Teams — Phase 2 Report (team-scoped visibility + team_id stamping / frozen history)

**Date:** 2026-07-20
**Branch:** `feat/teams-phase-1` (working tree; **uncommitted**)
**Scope:** BACKEND-ONLY. **NO commits / NO staging / NO prod side effects.**

Narrows the Phase-1 supervisor read flip from "whole tunnel" to "own team", and adds the
`conversation.team_id` / `lead.team_id` stamping that makes team filtering meaningful.

---

## 1. Phase A answers (A1–A8, verbatim current code)

### A1 — Where `assigned_agent_id` is SET (assignment sites)
Every assignment funnels through **one chokepoint**: `perform_handoff_to_agent` (`handoff.py`). Confirmed callers:
- `conversations.py::send_agent_message` (L246) — `if not _already_mine: perform_handoff_to_agent(... "manual_claim")`
- `conversations.py::claim_conversation` (L345)
- `conversations.py::close_conversation` auto-assign (L401, `"auto_assign"`)
- `conversations.py::reassign_conversation` (L457, `"manual_claim"`)
- `handoff.py::get_handoff_response` (L170, `"visitor_request"`)

The single pre-Phase-2 write was:
```python
await db.update_conversation(conversation_id, {
    "mode": "human", "assigned_agent_id": agent_id, "metadata": _meta,
})
```
No site sets `assigned_agent_id` directly outside this function → **stamping in `perform_handoff_to_agent` covers all assignment points.**

`fall_back_to_ai` (L212) writes `{"mode":"ai","assigned_agent_id":None,"metadata":_meta}` — **does NOT touch `team_id`.** ✅ (freeze preserved)

### A2 — Reading an operator's `team_id`
`get_user_by_id(user_id)` does `select("*")` → row includes `team_id`. Used directly.

### A3 — Conversations list builder
`supabase.py::get_conversations` (L280). Engaged-ownership OR already present:
```python
if agent_id:
    q = q.or_(f"assigned_agent_id.eq.{agent_id},metadata->>engaged_agent_id.eq.{agent_id}")
```
PostgREST ANDs separate filter calls, so a new `.in_("team_id", team_ids)` AND-composes with tunnel/status/agent/search.

### A4 — `leads.py` access + PM
```python
if user.get("role") not in ("owner", "admin", "supervisor", "qa"):
    raise HTTPException(403, "Leads access restricted to admin/owner")
```
**`project_manager` is NOT in the list → PM 403s on leads (Phase-1 trap intact).** STOP-condition "PM already in leads list" **NOT** triggered. Team filter for leads therefore applies to **supervisor** only.

### A5 — Users list
`users.py::list_users` → `db.get_users(role, search, limit, offset)`; `CAN_LIST_USERS = PRIVILEGED | {"supervisor","project_manager"}`. No team scoping before.

### A6 — Message-read path (surprise finding)
Two spots, and **the backend currently blocks supervisor message content entirely** (not "whole tunnel" — that was FE-only):
- `get_conversation_messages` (`/messages`, L139): `if role == "supervisor": 403` (blanket).
- `get_conversation` (detail, L159): `if role == "supervisor": conv["messages"] = []`.
- **PM was NOT handled in either** → a PM could read any conversation's messages (Phase-1 gap).

### A7 — Lead creation site
`supabase.py::ensure_lead_for_conversation` (L2087) — `insert({"conversation_id": conv_id})`. Called via `lead_service.get_or_create_lead`. Stamp `team_id` here.

### A8 — supabase-py filter syntax
`.eq()`, `.in_("col", list)`, and `.or_("a.eq.x,b.eq.y")`. Separate calls AND together. Team filter uses `.in_("team_id", team_ids)` (clean, precedence-safe). The users self-inclusion uses `.or_(f"team_id.in.({ids}),id.eq.{self}")`.

**STOP conditions:** none triggered (no existing team filtering; assignment is a single chokepoint; PM not in leads list).

---

## 2. Where `team_id` is stamped (frozen history)

### 2.1 Conversation — `handoff.py::perform_handoff_to_agent`
```python
_update = {"mode":"human","assigned_agent_id":agent_id,"metadata":_meta}
_operator = await db.get_user_by_id(agent_id)
_operator_team_id = (_operator or {}).get("team_id")
if _operator_team_id:
    _update["team_id"] = _operator_team_id
await db.update_conversation(conversation_id, _update)
```
- Stamps the operator's **current** team at every (re)assignment.
- **Never overwrites with NULL:** if the operator has no team, `team_id` is omitted → a previously-stamped team persists.
- Covers claim / reassign / auto-assign / visitor-request / agent-first-message (all route here).

### 2.2 Fallback does NOT clear it
`fall_back_to_ai` nulls `assigned_agent_id` only; `team_id` is never in its payload. **Verified by test #2** (`test_fallback_does_not_clear_team_id`).

### 2.3 Lead — `supabase.py::ensure_lead_for_conversation`
On create, reads the conversation's `team_id` and includes it in the insert (omitted if NULL). Existing leads are returned untouched.

---

## 3. Filters added + precedence reasoning

A shared resolver returns the scope contract:
```python
async def _resolve_team_scope(user):   # conversations.py
    None  → not team-scoped (owner/admin/dev/qa/sales/support) — unchanged
    []    → team-scoped but owns no active team → FAIL CLOSED (empty)
    [ids] → scoped to these active team ids
```
(leads.py / users.py resolve `get_team_ids_for_supervisor` / `get_team_ids_for_pm` inline; PM never reaches leads.)

### 3.1 Conversations list (`conversations.py` + `get_conversations`)
- supervisor/PM: resolve team ids; **`[]` → return `{data:[], count:0}` without querying** (fail closed).
- else pass `team_ids` → builder adds `q.in_("team_id", team_ids)`.
- **Precedence:** every filter is a separate PostgREST call (`.eq("tunnel")`, `.in_("status")`, `.or_(agent…)`, `.or_(search…)`, `.in_("team_id")`). PostgREST joins top-level filters with **AND**. The team constraint is therefore **always intersected**, never unioned — a supervisor cannot widen results by changing `tunnel`/`status`/`assigned_to`. **Verified by test #10** (`?tunnel=support&status=active&assigned_to=all` → `team_ids` still applied).

### 3.2 Message-read guard (`conversations.py`, A6)
- `/messages` and detail: for `supervisor`/`project_manager`, allow content **only if** `conversation.team_id ∈ their team ids`; else 403 (`/messages`) / metadata-only blanked messages (detail). Fail closed (no team → denied). **owner/admin/dev/qa unchanged.**
- Net effect: this **opens** own-team reads (previously blanket-blocked for supervisor) and **tightens** PM (previously unguarded).

### 3.3 Leads list (`leads.py` + `get_leads`)
- **PM:** unchanged (403 before scoping).
- **supervisor:** resolve team ids; `[]` → empty; else `q.in_("team_id", team_ids)` on the leads table. Review-stats counts use the same `team_ids`.
- owner/admin/qa unchanged.

### 3.4 Users list (`users.py` + `get_users`)
- supervisor/PM: `q.or_(f"team_id.in.({ids}),id.eq.{self}")` → team members **plus themselves**. No team → sentinel id set so **only self** is returned (fail closed).
- owner/admin (`CAN_LIST_USERS`): unchanged (all users — needed to assign people to teams).

**Byte-for-byte no-regression detail (leads):** `team_ids` is passed to `get_leads`/`get_leads_review_counts` **only when set** (`**_team_kw`). For owner/admin/qa the DB call signature is identical to pre-Phase-2 — this keeps `test_leads_api.py`'s exact `assert_called_once_with(...)` green.

---

## 4. Legacy NULL-team fallback (§3.3) — **DEFERRED**

Not implemented. The filter is strictly `conversation.team_id IN (...)` / `lead.team_id IN (...)`.

**Consequence (accepted, forward-only):** conversations/leads created **before** this ships have `team_id = NULL` and will **not** appear for supervisors/PMs. Team monitoring starts from the moment stamping is live. Rationale: expressing `team_id IS NULL OR operator∈team` cleanly alongside the existing engaged-ownership `.or_()` risks OR/AND precedence ambiguity (the #1 leak risk of this PR); a wrong precedence could leak other teams' rows. Chosen safety over historical completeness. If backfill is wanted later, a one-off migration can stamp `team_id` from `assigned_agent_id`/`engaged_agent_id` → operator team.

---

## 5. `git status` + `git diff --stat` (owned files)

Phase-2 edits (mine):
```
 M app/api/conversations.py |  60 ++++--   (team scope + message-read guard)
 M app/api/leads.py         |  21 ++      (supervisor team filter; PM still 403)
 M app/api/users.py         |  43 ++      (supervisor/PM member scoping + self)
 M app/db/supabase.py       | 236 ++++--  (3 helpers + team_ids params + stamps)
 M app/services/handoff.py  |  15 ++      (stamp conversation.team_id on assign)
?? tests/test_team_scoping.py            (new — 18 tests)
```
The wider dirty tree (`main.py`, `models/admin.py`, `app/api/teams.py`, `bbc-admin-app/*`, other reports) belongs to **Phase 1 / Phase 3** already present in the working tree — **not touched by this PR.** No file outside the Phase-2 ownership list was edited.

---

## 6. Test results

**New `tests/test_team_scoping.py`: 18 passed.** Highlights:
- **#1 stamp on assign** — `team_id="alpha"` written in the same update as `assigned_agent_id`. ✅
- **#1b** — operator with no team → `team_id` omitted (no NULL overwrite). ✅
- **#2 fallback** — `assigned_agent_id=None` but **no `team_id`** in payload. ✅
- **#3 supervisor scoped** — `get_conversations(team_ids=["alpha"])`. ✅
- **#4 fail closed** — supervisor with no team → 200 empty, `get_conversations` **not awaited**. ✅
- **#5 PM scoped** — `team_ids=["beta","gamma"]`. ✅
- **#6 read guard** — own-team → 200 with messages; other-team → **403**, `get_messages_after` not awaited. ✅
- **#7 leads** — supervisor `team_ids=["alpha"]`; no-team → empty; **PM → 403**. ✅
- **#8 users** — supervisor `team_ids=["alpha"], include_self_id=<uid>`; PM `team_ids=["beta"]`. ✅
- **#9 regression** — owner conversations/leads `team_ids is None`; qa leads `assigned_to="all"`; sales conversations not scoped. ✅
- **#10 leak guard** — extra `tunnel/status/assigned_to` params do not drop the team filter. ✅

**Full suite: 205 passed, 11 failed.** The 11 are **pre-existing / unrelated**:
- 9 × `scripts/test_pipeline_performance.py` — perf scripts, "async def not natively supported" (collection/env, not pytest-asyncio marked; unrelated to this PR).
- 2 × `tests/test_generator.py::TestSmartRouting` — pre-existing (documented in the Phase-1 report).

The 4 `tests/test_leads_api.py` failures that briefly appeared mid-work (from the added `team_ids` kwarg) were resolved by passing `team_ids` **only when set** — those 4 are green again with no test-file edits.

### Manual trace (supervisor S, team Alpha; operators O1/O2 in Alpha; operator OX in team Beta)
1. O1 is assigned conv C → `perform_handoff_to_agent` reads O1.team_id=Alpha → `C.team_id=Alpha` stamped.
2. S lists `/conversations` → `get_team_ids_for_supervisor(S)=[Alpha]` → `get_conversations(team_ids=[Alpha])` → sees C, not OX's conv (team_id=Beta).
3. S opens C detail / `/messages` → C.team_id=Alpha ∈ [Alpha] → **200 with content**.
4. S tries OX's conv `/messages` → team_id=Beta ∉ [Alpha] → **403**.
5. If S supervises no team → lists return empty, all message reads 403 (fail closed).

---

## 7. Owner notes

- **This closes the Phase-1 interim widening.** Once shipped, a supervisor with `canReadMessages=true` reads **only their own team's** conversations, not the whole tunnel. (In fact the backend previously blocked supervisor message content entirely; Phase 2 is what actually enables — and scopes — supervisor reading.)
- **Populate teams before shipping.** Empty teams ⇒ empty supervisor/PM screens (by design, fail closed). Create teams + assign supervisors/operators first (Phase 1.5).
- **Forward-only history:** pre-stamp conversations/leads (team_id NULL) won't show for supervisors/PMs. Backfill is optional (see §4).
- **PM leads:** still 403 (unchanged). PM sees conversations + own-team users only.
- Prerequisites unchanged: Phase-1 merged + migration applied (already applied to prod DB).

---

## 8. Didn't fit HEAD / deviations

1. **Message-read reality vs brief:** the brief assumed supervisors currently read "whole tunnel". The **backend** actually blocked supervisor message content outright (FE flip was cosmetic/blank). Phase 2 implements the intended end state (own-team read allowed, others denied) and additionally **scopes PM**, which was previously unguarded on message endpoints.
2. **Legacy NULL-team fallback deferred** (§4) — safety over completeness.
3. **Leads `team_ids` passed conditionally** (`**_team_kw`) to preserve `test_leads_api.py`'s exact-call assertions (behavior identical for owner/admin/qa).
4. **`get_conversation_simple` + `get_users` select lists extended** with `team_id` (additive; needed by the read guard / member scoping). No response contract broken (extra field only).
5. **Users "no team" fail-closed** uses a zero-UUID sentinel in the `in.()` list so only the caller's own row returns — avoids a separate query path.
6. `get_operator_ids_for_teams` helper added per spec (§3.2) though the chosen `team_id`-based filters don't require operator-id expansion; kept for a potential future legacy backfill.

---

## Final

| Item | Value |
|------|-------|
| Stamp sites | `perform_handoff_to_agent` (conv), `ensure_lead_for_conversation` (lead) |
| Filters | conversations (list + read guard), leads (supervisor), users (supervisor/PM + self) |
| Fail-closed | no team → empty lists / 403 reads (never "see all") |
| PM on leads | **403 unchanged** |
| Legacy NULL-team | **deferred** (forward-only) |
| New tests | `test_team_scoping.py` **18/18** |
| Full suite | **205 passed, 11 pre-existing/unrelated failures** |
| Files edited | 5 owned + 1 new test — nothing outside ownership |
| Commit | **None** — awaiting owner approval |
