# Engaged Ownership — List Filter Fix Report

**Date:** 2026-07-20  
**Branch:** `feat/crm-embed-widget` (working tree; uncommitted)  
**Status:** Implementation complete — **NO commits / NO staging.**

---

## Verdict

The bug was exactly as described: `"My conversations"` filtered only on `assigned_agent_id`. After AI fallback that column is nulled while `metadata.engaged_agent_id` survives — so engaged chats disappeared from the operator list. Live `fall_back_to_ai` already preserves metadata → **no `handoff.py` change**. Core fix is a one-site OR in `get_conversations`.

---

## 1. Phase A answers

### A1 — `get_conversations` query builder (before)

```280:316:bbc-chatbot-api/app/db/supabase.py
async def get_conversations(
    tunnel: Optional[str] = None,
    status: Optional[str] = None,
    status_in: Optional[list[str]] = None,
    search: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_id_is_null: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list, int]:
    ...
            if agent_id:
                q = q.eq("assigned_agent_id", agent_id)   # ← BUG: engaged ignored
            elif agent_id_is_null:
                q = q.is_("assigned_agent_id", "null")
            if search:
                q = q.or_(visitor_name/email/phone ilike...)
```

Uses supabase-py `.eq()` / `.or_()` / `.is_()` / `.in_()`. Tunnel + status are separate `.eq()` / `.in_()` calls (AND with everything else in PostgREST).

### A2 — List endpoint (`conversations.py`)

```44:67:bbc-chatbot-api/app/api/conversations.py
        if assigned_to == "me":
            agent_id_filter = user.get("id")
        elif assigned_to == "none":
            agent_id_is_null = True
        # "all" or None → no agent filter

        if assigned_to == "me" and status == "active":
            status_in = ["active", "needs_agent"]
            list_status = None

        rows, total = await db.get_conversations(
            tunnel=tunnel, status=list_status, status_in=status_in, search=search,
            agent_id=agent_id_filter, agent_id_is_null=agent_id_is_null,
            limit=limit, offset=offset,
        )
```

No change required in the endpoint — it already passes `user.id` as `agent_id` for `"me"`. The builder now interprets that as assigned **OR** engaged.

### A3 — Live `fall_back_to_ai` (HEAD) — **preserves metadata**

```198:216:bbc-chatbot-api/app/services/handoff.py
async def fall_back_to_ai(conversation_id: str) -> None:
    _conv = await db.get_conversation_simple(conversation_id)
    _meta = dict((_conv or {}).get("metadata") or {})
    ...
    _meta.pop("agent_assign_count", None)
    _meta.pop("agent_cooldown_until", None)
    _meta.pop("announce_pending", None)
    _meta.pop("agent_assigned_at", None)
    # engaged_agent_id is NOT popped

    await db.update_conversation(conversation_id, {
        "mode": "ai",
        "assigned_agent_id": None,
        "metadata": _meta,   # writes full meta back → engaged survives
    })
```

→ **§3.0: do not touch `handoff.py`.** Confirmed by `test_fall_back_preserves_engaged_agent_id`.

### A4 — `engaged_agent_id` written on operator message

```293:310:bbc-chatbot-api/app/api/conversations.py
    # ENGAGEMENT — ...
    #   - engaged_agent_id: the agent who speaks OWNS the conversation
    if _conv_meta.get("engaged_agent_id") != _user_id:
        _conv_meta["engaged_agent_id"] = _user_id
        _meta_changed = True
    ...
    if _engage_update:
        await db.update_conversation(conversation_id, _engage_update)
```

Also set on new-cycle handoff in `perform_handoff_to_agent` (`handoff.py` ~L288).

### A5 — OR syntax expressible?

Yes. Codebase already uses `.or_("col.eq.X,...")` extensively (search, tunnel_scope, etc.). Applied form:

```python
q.or_(
    f"assigned_agent_id.eq.{agent_id},"
    f"metadata->>engaged_agent_id.eq.{agent_id}"
)
```

`if agent_id:` guards empty values. Chained `.eq("tunnel")` / `.eq("status")` / `.in_("status")` remain **AND**ed with the OR (PostgREST default). Two `.or_()` calls (agent + search) AND together →  
`(assigned=me OR engaged=me) AND (name|email|phone ilike)`.

### A6 — Name search / `visitor_name`

List search only hits `conversations.visitor_name|email|phone`. Lead names live on `leads` (+ nested join in leads APIs). Empty `visitor_name` on some fallback rows makes name-search fail for "Anthony Chargin" — **separate issue**. Not fixed here (would need write path outside owned files or a leads JOIN). Core fix lets Steve see the chat in **My** without searching by name.

**STOP conditions:** none triggered (filter did not already include engaged; OR is expressible; marker survives fallback).

---

## 2. Phase B — the filter change

### Before
```python
if agent_id:
    q = q.eq("assigned_agent_id", agent_id)
```

### After
```python
if agent_id:
    # Assigned OR engaged: after AI fallback assigned_agent_id is
    # nulled but metadata.engaged_agent_id is preserved. PostgREST
    # ANDs chained filters, so tunnel/status stay AND-ed with this OR.
    q = q.or_(
        f"assigned_agent_id.eq.{agent_id},"
        f"metadata->>engaged_agent_id.eq.{agent_id}"
    )
```

### Precedence / leak reasoning

| Path | Filter | Leak risk |
|------|--------|-----------|
| `assigned_to=me` | `(assigned=STEVE OR engaged=STEVE) AND tunnel? AND status?` | OTHER agent's engaged id never appears in the OR string |
| `assigned_to=all` | no agent filter (unchanged) | n/a |
| `assigned_to=none` | `assigned_agent_id IS NULL` (unchanged) | engaged-fallback chats stay out of the unassigned queue (correct — they are owned) |

### §3.0 handoff.py

**Not changed.** Live version already writes `_meta` back with `engaged_agent_id` intact.

### `conversations.py`

**Not changed.** Endpoint already passes the right `agent_id`; builder semantics updated.

---

## 3. Git status / diff (owned only)

```
 M bbc-chatbot-api/app/db/supabase.py
?? bbc-chatbot-api/tests/test_engaged_ownership.py
?? docs/reports/ENGAGED_OWNERSHIP_REPORT.md
```

```
 bbc-chatbot-api/app/db/supabase.py | 8 +++++++-
 1 file changed, 7 insertions(+), 1 deletion(-)
```

No edits to `auth.py`, `settings.py`, `vercel.json`, frontend, or `agent.py`.

---

## 4. Test results

```
python -m pytest -q tests/test_engaged_ownership.py -v
→ 8 passed
```

| # | Case | Result |
|---|------|--------|
| 1 | OR includes engaged arm (fallback visibility) | PASS |
| 2 | Still-assigned covered by assigned arm | PASS |
| 3 | OTHER agent id never in filter (no leak) | PASS |
| 4 | tunnel + status still `.eq()` AND-ed with OR | PASS |
| 5 | no agent_id → no agent filter (`all`) | PASS |
| 6 | `agent_id_is_null` → `is_ null` only (`none`) | PASS |
| — | list endpoint `me` → `agent_id=user.id` | PASS |
| 7 | `fall_back_to_ai` keeps `engaged_agent_id` | PASS |

Full `tests/` run: **170 passed**; 3 pre-existing `test_generator.py` failures (SmartRouting / BudgetGuard — unrelated). Additional failures under `scripts/test_pipeline_performance.py` are live-API scripts picked up by collection, not this PR.

---

## 5. Steve / Anthony Chargin manual trace

1. Chat auto-assigned to Steve → he responds at 19:52:28 → message path sets `metadata.engaged_agent_id = Steve`.
2. Timeout 19:57:21 → `fall_back_to_ai` nulls `assigned_agent_id`, sets `mode=ai`, writes metadata **with engaged_agent_id still Steve**.
3. **Before fix:** `assigned_to=me` → `eq(assigned_agent_id, Steve)` → row missing → gone from active/closed My lists.
4. **After fix:** same request → `or_(assigned.eq.Steve, metadata->>engaged_agent_id.eq.Steve)` → row returned; `enrich_conversations_agent_info` marks `agent_state=fallback`.
5. Steve opens it → claim 409 guard sees `assigned_agent_id=NULL` → can re-claim; sticky auto-assign already skips other agents via engaged id.

---

## 6. §3.2 follow-up — name search

Worth a **separate small PR**: populate `conversations.visitor_name` when extraction / lead creation learns the name (write path likely in pipeline / lead service — **not** this PR’s files). Optionally extend search to join leads — larger, defer. Core fix already unblocks the operator without name search.

**Also note (out of scope):** `get_conversation_counts` `my_active` / `my_closed` still count by `assigned_agent_id` only — tab badges may under-count after fallback until a follow-up mirrors this OR. List data is fixed; badges are cosmetic.

---

## 7. Sequencing for the owner

1. **Land this PR first** (touches `supabase.py` list builder).
2. Teams backend PR rebases on top (same files).
3. Optional later: counts OR + `visitor_name` population PR.

---

## 8. What didn’t fit HEAD

1. Prompt suggested possible `conversations.py` change — none needed; wiring already correct.
2. Prompt mentioned two historical `fall_back_to_ai` versions — HEAD is the preserving one.
3. Counts left unchanged intentionally (minimal scope); documented as follow-up.
4. Did not add a leads JOIN for search (§3.2 deferred).

---

## Final

| Item | Value |
|------|--------|
| Core fix | `get_conversations` agent filter → assigned OR engaged |
| handoff.py | Untouched (preserves engaged) |
| conversations.py | Untouched |
| Tests | **8/8 PASS** |
| Commit | **None** — awaiting owner approval |
