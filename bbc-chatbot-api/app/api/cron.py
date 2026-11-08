"""
Cron endpoint — process abandoned conversations.
Auth: CRON_SECRET header (not JWT).
"""

import hmac
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from config.settings import settings
from app.db import supabase as db
from app.services.crm import (
    CRM_PUSH_HEALTH,
    format_phone_international,
    submit_abandoned_to_crm,
)
from app.services.lead_service import get_or_create_lead

logger = logging.getLogger(__name__)
router = APIRouter()


# Conversations pushed to the CRM in this process today: the second guard,
# the one that does not depend on a database write succeeding. Reset lazily on
# date change. Per-process, like the health counters — it does not survive a
# restart, and that is accepted: it caps the damage of a mark that fails, it
# does not replace the flag.
_pushed_today: set = set()
_pushed_today_date: str = ""


def _day_guard_blocks(cid: str) -> bool:
    """True when this process already pushed this conversation today.

    The created_in_crm flag is the real guard; this is the one that still
    works when the flag write dies — which is how one client ended up with six
    CRM records on 21 Aug 2026."""
    global _pushed_today, _pushed_today_date
    from datetime import datetime, timezone
    _today_str = datetime.now(timezone.utc).date().isoformat()
    if _pushed_today_date != _today_str:
        _pushed_today = set()
        _pushed_today_date = _today_str
    return cid in _pushed_today


def _contact_ids(conv: dict) -> list[str]:
    """Every identifier that names this human: phone AND/OR email.

    Matching on the PAIR would split one person into several CRM rows
    the moment a second form omitted the email — so each identifier is
    its own link and rows sharing ANY identifier become one group."""
    from app.services.crm import format_phone_international

    ids = []
    phone = format_phone_international(conv.get("visitor_phone") or "")
    if phone:
        ids.append(f"p:{phone}")
    email = (conv.get("visitor_email") or "").strip().lower()
    if email:
        ids.append(f"e:{email}")
    return ids


def _richness(conv: dict) -> tuple:
    """Which of two conversations for the SAME contact to push.

    More messages first (a real conversation beats a bare form), then a
    name on file, then the newest. Deterministic — no coin flips over
    which row reaches the consultant."""
    return (
        int(conv.get("message_count") or 0),
        1 if (conv.get("visitor_name") or "").strip() else 0,
        str(conv.get("created_at") or ""),
    )


def pick_richest_by_contact(convs: list[dict]) -> list[dict]:
    """One conversation per human — the richest — carrying `_twin_count`.

    Union by shared identifier (phone or email), so "same phone, email
    only on one row" is still ONE person. Conversations with no usable
    identifier pass through untouched (the contract refuses them later)."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    passthrough: list[dict] = []
    keyed: list[tuple[str, dict]] = []
    for conv in convs:
        ids = _contact_ids(conv)
        if not ids:
            passthrough.append(conv)
            continue
        for other in ids[1:]:
            union(ids[0], other)
        keyed.append((ids[0], conv))

    groups: dict[str, list[dict]] = {}
    for anchor, conv in keyed:
        groups.setdefault(find(anchor), []).append(conv)

    chosen: list[dict] = []
    for members in groups.values():
        best = max(members, key=_richness)
        if len(members) > 1:
            best = dict(best)
            best["_twin_count"] = len(members) - 1
        chosen.append(best)
    return chosen + passthrough


async def _persist_refusal(lead_id: str, error: str) -> bool:
    """Only a real GATE refusal becomes work-list state. A transient
    HTTP/timeout failure must stay retryable — writing it as a gate
    reason would freeze a dialable lead into a permanent 'refused' row."""
    if not (error or "").startswith("gate:"):
        return False
    await db.update_lead_crm_push_state(
        lead_id, gate_reason=error[len("gate:"):]
    )
    return True


async def _contacts_already_in_crm() -> set[str] | None:
    """Every contact id (p:<phone>, e:<email>) that reached the CRM in the
    last 30 days, from ANY conversation. None = the read failed.

    The created_in_crm flag is per conversation. John Arana wrote twice on
    15 Oct (same phone, a typo in the email): the second chat went to the
    CRM that day, the first one went again on 4 Nov. One human, two rows."""
    rows = await db.get_crm_contact_rows(days=30)
    if rows is None:
        return None
    ids: set[str] = set()
    for row in rows:
        ids.update(_contact_ids(row))
    return ids


def _hold_for_crm_lookup(cid: str, in_crm: set[str] | None, where: str) -> str | None:
    """Why an AUTOMATIC push must not happen now, or None to go ahead.

    'lookup_failed' — we could not check; wait for the next tick rather
    than risk a second CRM row (a failed read is not an empty CRM).
    'contact_already_in_crm' — this human is already there."""
    if in_crm is None:
        CRM_PUSH_HEALTH["crm_contact_lookup_failed"] += 1
        logger.warning(
            f"[{where}][{cid}] CRM contact lookup failed — holding the push "
            "this cycle rather than risking a duplicate"
        )
        return "lookup_failed"
    return None


def _contact_in_crm(conv: dict, in_crm: set[str]) -> bool:
    """True when this conversation's phone OR email is already in the CRM."""
    return any(i in in_crm for i in _contact_ids(conv))


async def run_aaa_backfill(days: int = 30) -> dict:
    """The contacts the old guards blocked — pushed once, safely.

    The full 30-day window, active AND closed, any mode: skips leads
    already in the CRM (idempotency lives on the flag), dedups by contact
    so one human never becomes two CRM rows, and pushes the rest through
    the defaults path with normal proof discipline (2xx + crm_id only).
    A gate/contract refusal is NOT a loss: the row keeps its reason and
    surfaces in the panel's "CRM pending" work-list for a human.

    ON DEMAND ONLY. It used to run on the first abandoned-CRM tick of every
    process — i.e. after every deploy or restart — and each time it pushed
    the operator-handled chats of the last 30 days (5 waves Sep–Nov, 72 on
    4 Nov 2026). Nothing calls it automatically any more."""
    convs = pick_richest_by_contact(
        await db.get_recent_contact_conversations(days=days)
    )
    pushed = 0
    work_list = 0
    skipped = 0
    in_crm = await _contacts_already_in_crm() if convs else set()
    for conv in convs:
        cid = conv["id"]
        try:
            if _day_guard_blocks(cid):
                logger.warning(
                    f"[cron][{cid}] already pushed today in this process — "
                    "skipping to avoid a CRM duplicate (flag write likely failed)"
                )
                skipped += 1
                continue
            lead = await get_or_create_lead(cid)
            if not lead:
                # Same reasoning as run_abandoned_crm: a failed read is not an
                # absent lead. Skipping costs one cycle; guessing costs a
                # duplicate CRM record and a consultant's phone call.
                logger.warning(f"[aaa-backfill][{cid}] lead read failed — skipping")
                skipped += 1
                continue
            if lead.get("created_in_crm"):
                skipped += 1
                continue
            if _hold_for_crm_lookup(cid, in_crm, "aaa-backfill"):
                skipped += 1
                continue
            if _contact_in_crm(conv, in_crm):
                CRM_PUSH_HEALTH["contact_already_in_crm"] += 1
                await _persist_refusal(lead["id"], "gate:contact_already_in_crm")
                work_list += 1
                continue
            if not conv.get("last_user_message_at"):
                conv["_no_engagement"] = True
            from app.security.form_trap import holds_crm
            if holds_crm(conv.get("metadata")):
                skipped += 1
                continue
            result = await submit_abandoned_to_crm(conv, lead)
            if result.success and result.request_id:
                _marked = await db.mark_lead_created_in_crm(
                    lead["id"], crm_lead_id=result.request_id
                )
                if _marked is None:
                    logger.error(
                        f"[aaa-backfill][{cid}] CRM ACCEPTED "
                        f"crm_id={result.request_id} but the flag write FAILED "
                        "— manual reconciliation needed"
                    )
                    skipped += 1
                    continue
                _pushed_today.add(cid)
                in_crm.update(_contact_ids(conv))
                pushed += 1
            else:
                # Visible state, never a silent drop: a GATE refusal rides
                # on the lead into the "CRM pending" work-list. A transient
                # failure stays retryable instead of freezing as a refusal.
                if await _persist_refusal(lead["id"], result.error or ""):
                    work_list += 1
                else:
                    skipped += 1
        except Exception as e:
            logger.warning(f"[cron][aaa-backfill] conv={cid}: {e}")
            skipped += 1
    logger.info(
        f"AAA backfill: {pushed} pushed, {work_list} work-list, {skipped} skipped"
    )
    return {
        "pushed": pushed,
        "work_list": work_list,
        "skipped": skipped,
        "scanned": len(convs),
    }


async def run_abandoned_crm() -> dict:
    """Find conversations abandoned >30 min, submit to CRM with defaults, close."""
    if not settings.abandoned_cron_enabled:
        logger.info("[cron] abandoned-CRM sweep disabled by settings")
        return {"disabled": True}
    # The AAA backfill is NOT started from here any more: a per-process
    # "first tick" is every deploy and every restart (see run_aaa_backfill).

    abandoned = await db.get_abandoned_conversations(settings.abandoned_timeout_minutes)
    logger.info(f"[cron] Found {len(abandoned)} abandoned conversations")

    # One human = one CRM row, in the live sweep too. Without this the
    # backfill's careful dedup is undone minutes later by the next tick.
    _push_owners = {c["id"] for c in pick_richest_by_contact(abandoned)}
    # …and one human = one CRM row ACROSS conversations: the flag is per
    # conversation, so a second chat from the same person looks new.
    # Loaded lazily — only when a conversation actually reaches the push.
    _in_crm: set[str] | None = set()
    _in_crm_loaded = False

    results = []
    for conv in abandoned:
        cid = conv["id"]
        try:
            # The select is WIDE (closed + human) so no dialable lead is
            # orphaned — but width feeds the PUSH only. Closing, unassigning
            # and posting a closing message are never done to a chat a human
            # owns or to one that is already closed.
            _human_owned = conv.get("mode") == "human"
            _already_closed = conv.get("status") == "closed"
            _may_close = not _human_owned and not _already_closed
            phone = format_phone_international(conv.get("visitor_phone", ""))
            email = (conv.get("visitor_email") or "").strip()

            if not phone or len(phone) < 8:
                results.append({"id": cid, "status": "skipped", "reason": "phone_invalid"})
                continue
            # BUSINESS RULE: every captured contact is dialable. Email is
            # optional when the phone is valid (hygiene on a PRESENT email
            # runs inside submit_abandoned_to_crm); name is never required
            # — the payload defaults to "Customer".
            if email and "@" not in email:
                results.append({"id": cid, "status": "skipped", "reason": "email_invalid"})
                continue

            if _day_guard_blocks(cid):
                logger.warning(
                    f"[cron][{cid}] already pushed today in this process — "
                    "skipping to avoid a CRM duplicate (flag write likely failed)"
                )
                results.append({"id": cid, "status": "skipped", "reason": "pushed_today"})
                continue
            lead = await get_or_create_lead(cid)
            if not lead:
                # A read that FAILED is not a lead that does not exist. Both
                # created_in_crm guards below were conditioned on `lead` being
                # truthy, so a single transient error — and the SSL drops in
                # these logs are transient and frequent — skipped every guard
                # and fell through to the push. That is how eight clients who
                # were already marked on 20 Aug got a second CRM record today,
                # hours after consultants had cleaned the first ones by hand.
                # The conversation is not going anywhere; the next cycle can
                # read it properly.
                logger.warning(
                    f"[cron][{cid}] lead read failed — skipping this cycle "
                    "rather than risking a duplicate CRM record"
                )
                results.append({"id": cid, "status": "skipped", "reason": "lead_read_failed"})
                continue
            if lead.get("created_in_crm") and not _may_close:
                # In the CRM already, and this conversation must not be
                # touched (a human owns it, or it is already closed):
                # nothing left to do.
                results.append({"id": cid, "status": "already_done"})
                continue
            if lead.get("created_in_crm"):
                # Close-only: CRM done but conv still active (zombie)
                _meta = conv.get("metadata") or {}
                _site = _meta.get("site")
                # Check flag instead of fragile string match
                _conv_meta = conv.get("metadata") or {}
                from app.services.closing import (
                    has_closing_been_sent,
                    claim_closing_sent,
                    compute_closing_text,
                )
                if not has_closing_been_sent(_conv_meta):
                    _claimed = await claim_closing_sent(cid)
                    if _claimed:
                        _closing = compute_closing_text(_site)
                        await db.add_message(cid, "ai", _closing, model_used="template", cost=0.0)
                await db.update_conversation(cid, {
                    "status": "closed",
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "ai",
                    "assigned_agent_id": None,
                })
                logger.info(f"[cron][{cid}] Closed existing CRM lead")
                results.append({"id": cid, "status": "closed_existing_crm"})
                continue

            # The `if not lead:` recreate-and-push branch that used to live here
            # is gone. It was only ever reached when the read above failed — and
            # on that path we had already lost the one thing that says "this
            # lead is in the CRM already". Creating a lead is the pipeline's
            # job, not this sweep's.

            if cid not in _push_owners:
                # A richer conversation for this same human owns the push.
                # Visible state, no CRM call, no close (closing it would
                # orphan the row silently).
                await _persist_refusal(lead["id"], "gate:duplicate_contact")
                results.append({"id": cid, "status": "dedup_twin"})
                continue

            from app.security.form_trap import holds_crm
            if holds_crm(conv.get("metadata")):
                # PR-12: a form_trap conversation waits for a human push.
                results.append({"id": cid, "status": "hold_form_trap"})
                continue
            if not _in_crm_loaded:
                _in_crm = await _contacts_already_in_crm()
                _in_crm_loaded = True
            if _hold_for_crm_lookup(cid, _in_crm, "cron"):
                results.append({"id": cid, "status": "skipped", "reason": "crm_lookup_failed"})
                continue
            if _contact_in_crm(conv, _in_crm):
                # Visible in "CRM pending" with its reason; the panel button
                # still pushes it if a human decides it is a new trip.
                CRM_PUSH_HEALTH["contact_already_in_crm"] += 1
                await _persist_refusal(lead["id"], "gate:contact_already_in_crm")
                results.append({"id": cid, "status": "contact_already_in_crm"})
                continue

            crm_result = await submit_abandoned_to_crm(conv, lead)

            if crm_result.success:
                # The returned id is the receipt — stored with the flag.
                marked = await db.mark_lead_created_in_crm(
                    lead["id"], crm_lead_id=crm_result.request_id
                )
                if marked is None:
                    # The CRM row EXISTS but our flag write died. Closing now
                    # would hide it; leaving the flag false without closing
                    # means the next tick re-pushes a DUPLICATE. Neither is
                    # acceptable silently — shout and stop touching this row.
                    logger.error(
                        f"[cron][{cid}] CRM ACCEPTED crm_id={crm_result.request_id} "
                        "but the flag write FAILED — manual reconciliation needed"
                    )
                    results.append({
                        "id": cid, "status": "flag_write_failed",
                        "crm_id": crm_result.request_id,
                    })
                    continue
                if _may_close:
                    await db.update_conversation(cid, {
                        "status": "closed",
                        "closed_at": datetime.now(timezone.utc).isoformat(),
                        "mode": "ai",
                        "assigned_agent_id": None,
                    })
                logger.info(
                    f"[cron][{cid}] Success: CRM submitted"
                    f"{' + closed' if _may_close else ' (left open — human/closed)'}"
                )
                _pushed_today.add(cid)
                if _in_crm is not None:
                    _in_crm.update(_contact_ids(conv))
                results.append({"id": cid, "status": "success"})
            else:
                logger.error(f"[cron][{cid}] CRM fail: {crm_result.error}")
                # A gate refusal becomes work-list state; a transient HTTP
                # failure stays retryable (see _persist_refusal).
                await _persist_refusal(lead["id"], crm_result.error or "")
                results.append({"id": cid, "status": "crm_failed", "error": crm_result.error})

        except Exception as e:
            logger.error(f"[cron][{cid}] Error: {e}")
            results.append({"id": cid, "status": "error", "error": str(e)})

    success_count = sum(
        1 for r in results if r["status"] in ("success", "closed_existing_crm")
    )
    return {"processed": len(results), "success": success_count, "results": results}


async def run_agent_sweep() -> dict:
    """The ONE place the stale + deadline sweep runs.

    Until 1 Sep this was a backstop behind a sweep that every heartbeat also
    performed — 35 agents × every 5s. Now the heartbeat only writes presence
    and reads the queue; this job does the sweeping, once per
    agent_sweep_interval_seconds. Both passes: offline agents (stale
    last_seen_at → fall back) and online-but-silent agents (first-response
    deadline, one query for the whole set)."""
    from app.api.agent import _cleanup_stale_conversations

    swept = await _cleanup_stale_conversations()
    logger.info(f"[cron][agent-sweep] swept={swept}")
    return {"success": True, "swept": swept}


async def run_cleanup_stale_ready() -> dict:
    """Auto-reset is_ready for operators who disconnected without toggling off."""
    stale = await db.reset_stale_ready_users(settings.agent_timeout_seconds)
    names = [u.get("name") or u["id"] for u in stale]
    if stale:
        # They left without switching off: their open tasks move, as on Not ready.
        from app.services.tasks_service import leave_shift
        for u in stale:
            await leave_shift(u["id"])
    if names:
        logger.info(f"[cron] Auto-reset is_ready: {names}")
    return {"reset": len(stale), "names": names}


async def run_close_stale_presence() -> dict:
    """Auto-close conversations where client left website >5 min ago."""
    stale = await db.get_stale_presence_left_conversations(timeout_minutes=5)
    closed = 0
    for conv in stale:
        cid = conv["id"]
        try:
            await db.update_conversation(cid, {"status": "closed"})
            logger.info(f"[cron] Auto-closed {cid} ({conv.get('visitor_name','?')}): client left >5min")
            closed += 1
        except Exception as e:
            logger.error(f"[cron] Failed to close {cid}: {e}")
    return {"closed": closed}


async def run_attention_emails() -> dict:
    """One-shot attention emails for returning / needs-attention chats.

    Skips plain AI-handled Fresh chats. Deduped via claim_attention_email
    so a chat emails at most once.
    """
    if not settings.attention_email_enabled:
        return {"sent": 0, "skipped": 0, "disabled": True}

    from app.services.email import send_attention_email

    candidates = await db.get_attention_email_candidates()
    sent = 0
    skipped = 0
    for conv in candidates:
        tag = db.derive_conversation_tag(conv)
        # Attention-worthy only: returning customer (Main Queue) or
        # needs_agent. Never email every Fresh AI chat.
        if tag not in ("main_queue",) and conv.get("status") != "needs_agent":
            skipped += 1
            continue
        # needs_agent with Fresh sticky-less still deserves email
        if tag == "fresh" and conv.get("status") != "needs_agent":
            skipped += 1
            continue
        cid = conv["id"]
        claimed = await db.claim_attention_email(cid)
        if not claimed:
            skipped += 1
            continue
        ok = await send_attention_email(
            tag=tag if tag != "fresh" else "main_queue",
            chat_number=conv.get("chat_number"),
            created_at=conv.get("created_at"),
            customer_name=conv.get("visitor_name"),
            conversation_id=cid,
        )
        if ok:
            sent += 1
        else:
            skipped += 1
    return {"sent": sent, "skipped": skipped, "candidates": len(candidates)}


def _require_cron(request: Request) -> None:
    """CRON_SECRET bearer, compared in constant time."""
    secret = (settings.cron_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Cron endpoint not configured")
    auth = request.headers.get("Authorization", "")
    if not hmac.compare_digest(auth.encode("utf-8"), f"Bearer {secret}".encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid cron token")


@router.post("/cron/abandoned-crm")
async def process_abandoned_conversations(request: Request):
    """Find conversations abandoned >30 min, submit to CRM with defaults, close."""

    _require_cron(request)

    # Same lock as the internal scheduler's tick: a manual run waits for a
    # tick in progress instead of pushing the same contact alongside it.
    from app.services.scheduler import job_lock

    async with job_lock("abandoned_crm"):
        return await run_abandoned_crm()


@router.post("/cron/agent-response-sweep")
async def agent_response_sweep(request: Request):
    """Backstop sweep: fall back conversations whose assigned agent sent zero messages
    within the response deadline. Covers the window when no agents are online
    (heartbeats don't run → stale cleanup doesn't run → conversations pile up).
    Auth: same Bearer CRON_SECRET as abandoned-crm."""
    _require_cron(request)

    return await run_agent_sweep()


@router.post("/cron/daily-learning")
async def daily_learning(
    request: Request, bootstrap: bool = False, force: bool = False
):
    """Analyze recent conversations and propose lessons for human approval.

    Daily: the last 24h. `?bootstrap=true`: every closed conversation ever — run
    once after deploy to seed the lesson list from history.
    `?force=true`: bypass the already-ran guard. Evidence-unsafe — chunks that
    succeeded in a prior partial will be reinforced again. Rare, deliberate.
    Auth: same Bearer CRON_SECRET as abandoned-crm."""
    _require_cron(request)

    from app.services.learning import run_learning

    return await run_learning(bootstrap=bootstrap, force=force)


async def run_crm_orphan_backstop() -> dict:
    """The 24-Paulettes fix, human-gated for history.

    Every scheduler tick: gold leads (score>=70) the CRM never received,
    created between 48h and 1h ago, get ONE push attempt (max 3 per lead)
    through the one true push path — gate, then flag only on 2xx+id.
    FRESH failures only: anything older than 48h (including the current
    backlog of orphans) is never auto-pushed — it sits in the panel's
    "CRM pending" work-list where a human decides (dates may be past,
    duplicates may exist under a corrected email). No surprise CRM influx.
    """
    from datetime import timedelta
    from types import SimpleNamespace

    from app.services.crm import push_lead_to_crm

    now = datetime.now(timezone.utc)
    orphans = await db.get_crm_orphan_leads(
        min_score=70,
        older_than_iso=(now - timedelta(hours=1)).isoformat(),
        younger_than_iso=(now - timedelta(hours=48)).isoformat(),
        max_attempts=3,
        limit=20,
    )
    if orphans:
        logger.info(f"[cron][crm-backstop] {len(orphans)} fresh gold orphan(s)")

    results = []
    for lead in orphans:
        lead_id = lead["id"]
        try:
            conv = None
            if lead.get("conversation_id"):
                conv = await db.get_conversation_simple(lead["conversation_id"])
            conv = conv or {}
            visitor = SimpleNamespace(
                name=conv.get("visitor_name") or "",
                email=conv.get("visitor_email") or "",
                phone=conv.get("visitor_phone") or "",
            )
            from app.security.form_trap import holds_crm
            if holds_crm(conv.get("metadata")):
                continue  # PR-12: waits for a human push from the panel
            result = await push_lead_to_crm(
                lead,
                visitor,
                lead.get("conversation_id") or lead_id,
                conv_metadata=conv.get("metadata"),
                suid=conv.get("visitor_id"),
            )
            if result.success:
                results.append({"id": lead_id, "status": "pushed", "crm_id": result.request_id})
            else:
                # Attempt spent either way — 3 strikes and the lead stays
                # in the work-list instead of burning CRM calls forever.
                await db.update_lead_crm_push_state(
                    lead_id,
                    bump_attempts_from=int(lead.get("crm_push_attempts") or 0),
                )
                results.append({"id": lead_id, "status": "failed", "error": result.error})
        except Exception as e:
            logger.error(f"[cron][crm-backstop] lead={lead_id}: {e}")
            results.append({"id": lead_id, "status": "error", "error": str(e)})

    return {"scanned": len(orphans), "results": results}


_DB_ALERT: dict = {"last_sent_at": None, "over_streak": 0, "episode_open": False}
_HB_ALERT: dict = {"last_sent_at": None, "over_streak": 0, "episode_open": False}
DB_ALERT_COOLDOWN_MINUTES = 60
HB_ALERT_COOLDOWN_MINUTES = 360


async def run_db_saturation_alert() -> dict:
    """Shout BEFORE the panel breaks.

    Two signals, judged separately on the last 60s: the executor was pinned
    near its ceiling (incident, cooldown 60 min), or the heartbeat is slow
    while the pool is idle (warning, cooldown 6 h). We learned about the
    31 Aug saturation from an operator in a group chat; this closes that
    gap. Resets peak_in_flight after evaluating so each window is judged
    on its own."""
    from datetime import datetime, timezone, timedelta
    from app.db.supabase import DB_HEALTH, db_health_snapshot, DASHBOARD_CACHE_HEALTH
    from app.api.agent import heartbeat_health_snapshot
    from app.pipeline.orchestrator import BG_HEALTH
    from config.settings import settings as _s
    from app.services.email import send_ops_alert_email

    db_s: dict = {}
    hb_s: dict = {}
    _sent = False
    saturated = False
    slow = False
    now = None
    last = None
    kind = ""
    hb_n = 0
    windows = 3
    subject = ""
    body = ""
    cc_super = False
    ceiling = 1

    try:
        db_s = db_health_snapshot(window_seconds=60)
        hb_s = heartbeat_health_snapshot(window_seconds=60)
        ceiling = max(1, db_s["workers"] - 2)
        saturated = db_s["peak_in_flight"] >= ceiling
        hb_n = hb_s["n"]
        slow = hb_n >= 20 and hb_s["p95_ms"] > 1500
        # window reset: peak is per evaluation window
        DB_HEALTH["peak_in_flight"] = DB_HEALTH["in_flight"]
        # Sustained, not spiky: peak resets every window, so a one-second burst
        # in one window used to fire the alert — 96 emails a day to super@ on
        # a steady load. Count consecutive windows over the ceiling instead.
        if saturated:
            _DB_ALERT["over_streak"] += 1
        else:
            if _DB_ALERT["episode_open"]:
                logger.info("[db-alert] saturation episode ended")
            _DB_ALERT["over_streak"] = 0
            _DB_ALERT["episode_open"] = False

        if hb_n == 0 or (hb_n >= 20 and hb_s["p95_ms"] <= 1500):
            if _HB_ALERT["episode_open"]:
                logger.info("[db-alert] heartbeat-slow episode ended")
            _HB_ALERT["over_streak"] = 0
            _HB_ALERT["episode_open"] = False
        elif slow:
            _HB_ALERT["over_streak"] += 1
        else:
            # 0 < n < 20: no verdict on this window — but a gap breaks the run.
            # "Sustained 3×60s" means three consecutive windows, not three slow
            # windows scattered across a quiet hour. The episode itself stays as
            # it is: a thin window neither opens nor closes it.
            _HB_ALERT["over_streak"] = 0

        windows = _s.db_alert_sustained_windows
        if saturated:
            if _DB_ALERT["over_streak"] < windows:
                return {
                    "state": "watching",
                    "streak": _DB_ALERT["over_streak"],
                    **db_s,
                    "heartbeat": hb_s,
                }
            now = datetime.now(timezone.utc)
            last = _DB_ALERT["last_sent_at"]
            if last and now - last < timedelta(minutes=DB_ALERT_COOLDOWN_MINUTES):
                return {"state": "suppressed", **db_s, "heartbeat": hb_s}
            kind = "saturated"
        elif slow:
            if _HB_ALERT["over_streak"] < windows:
                return {
                    "state": "watching",
                    "streak": _HB_ALERT["over_streak"],
                    **db_s,
                    "heartbeat": hb_s,
                }
            now = datetime.now(timezone.utc)
            last = _HB_ALERT["last_sent_at"]
            if last and now - last < timedelta(minutes=HB_ALERT_COOLDOWN_MINUTES):
                return {"state": "suppressed", **db_s, "heartbeat": hb_s}
            kind = "slow"
        else:
            return {"state": "ok", **db_s, "heartbeat": hb_s}

        # episode_open and last_sent_at are written ONLY after a confirmed send.
        # send_ops_alert_email returns False on a Postmark failure (it does not
        # raise); marking the episode first meant a failed send suppressed the
        # retry for an hour and stripped cc_super from it.
        body = (
            f"peak_in_flight={db_s['peak_in_flight']}/{db_s['workers']}\n"
            f"heartbeat (60s): n={hb_s['n']} p50={hb_s['p50_ms']}ms "
            f"p95={hb_s['p95_ms']}ms p99={hb_s['p99_ms']}ms "
            f"max={hb_s['max_ms']}ms slow_count={hb_s['slow_count']}\n"
            f"db (60s): n={db_s['n']} p50={db_s['p50_ms']}ms "
            f"p95={db_s['p95_ms']}ms p99={db_s['p99_ms']}ms "
            f"max={db_s['max_ms']}ms slow_count={db_s['slow_count']}\n"
            f"disconnects={db_s['disconnects']} "
            f"retries_ok={db_s['retries_ok']} retries_failed={db_s['retries_failed']}\n"
            f"by_label={db_s['by_label']}  # cumulative since boot — volume, not latency\n"
            f"slow_by_label={db_s['slow_by_label']}  # cumulative since boot — volume, not latency\n"
            f"background: running={BG_HEALTH['running']} peak={BG_HEALTH['peak_running']} "
            f"waited={BG_HEALTH['waited']}\n"
            f"dashboard_cache: {DASHBOARD_CACHE_HEALTH}\n\n"
            "The panel starts failing when the pool pins near its ceiling. "
            "Check /health.db by_label to see which caller is loudest."
        )
        if kind == "saturated":
            subject = (
                f"DB executor saturated [{windows}×60s] "
                f"({db_s['peak_in_flight']}/{db_s['workers']})"
            )
        else:
            subject = (
                f"Heartbeat slow — pool idle "
                f"({db_s['peak_in_flight']}/{db_s['workers']}), "
                f"DB p99 {db_s['p99_ms']} ms"
            )
        # Ops only. super@ already gets customer-path alerts; this is infra.
        try:
            _sent = await send_ops_alert_email(
                subject=subject,
                cc_super=cc_super,
                body=body,
            )
        except Exception as e:
            logger.warning(f"[db-alert] email failed: {e}")
            return {"state": "email_failed"}
        if not _sent:
            logger.warning(
                "[db-alert] email not sent (transport returned False) — will retry next window"
            )
            return {"state": "email_failed"}
        if kind == "saturated":
            _DB_ALERT["episode_open"] = True
            _DB_ALERT["last_sent_at"] = now
            return {"state": "alerted", **db_s, "heartbeat": hb_s}
        _HB_ALERT["episode_open"] = True
        _HB_ALERT["last_sent_at"] = now
        return {"state": "warned", **db_s, "heartbeat": hb_s}
    except Exception as e:
        logger.error(f"[db-alert] failed: {e}", exc_info=True)
        return {"state": "error"}


# Anti-spam state for the queue alert. Per-instance, like the other counters.
_QUEUE_ALERT: dict = {"last_sent_at": None, "last_count": 0, "active": False}
QUEUE_ALERT_COOLDOWN_MINUTES = 15
QUEUE_ALERT_AFTER_SECONDS = 120


async def run_queue_stall_alert() -> dict:
    """Shout when the line stops moving.

    Ten people notified is nine people assuming somebody else will take it —
    the bystander effect, which is exactly what a shared queue invites. So the
    system watches instead: a conversation waiting more than two minutes with no
    owner emails super@ and raises a banner. At most one alert per fifteen
    minutes however many are waiting; a second only if the number grew. When the
    queue empties after an alert, ONE recovery email — otherwise nobody knows it
    is over.
    """
    from datetime import datetime, timezone, timedelta
    try:
        stats = await db.get_queue_stats()
        waiting = stats.get("waiting_now", 0)
        oldest = stats.get("oldest_seconds", 0)
        now = datetime.now(timezone.utc)
        if waiting == 0 or oldest < QUEUE_ALERT_AFTER_SECONDS:
            if _QUEUE_ALERT["active"]:
                _QUEUE_ALERT["active"] = False
                _QUEUE_ALERT["last_count"] = 0
                try:
                    from app.services.email import send_super_alert_email
                    await send_super_alert_email(
                        conversation_id="-",
                        visitor_name=None, visitor_phone=None, visitor_email=None,
                        tunnel="sales",
                        last_message="(queue recovered: nobody is waiting any more)",
                        chat_number=None,
                    )
                except Exception as e:
                    logger.warning(f"[queue-alert] recovery email failed: {e}")
                return {"state": "recovered"}
            return {"state": "ok", "waiting": waiting}
        _last = _QUEUE_ALERT["last_sent_at"]
        _cooled = (
            _last is None
            or now - _last >= timedelta(minutes=QUEUE_ALERT_COOLDOWN_MINUTES)
        )
        _grew = waiting > _QUEUE_ALERT["last_count"]
        if not (_cooled or _grew):
            return {"state": "suppressed", "waiting": waiting}
        try:
            from app.services.email import send_super_alert_email
            await send_super_alert_email(
                conversation_id="-",
                visitor_name=None, visitor_phone=None, visitor_email=None,
                tunnel="sales",
                last_message=(
                    f"(queue stalled: {waiting} waiting, oldest {oldest}s, "
                    "nobody has taken it)"
                ),
                chat_number=None,
            )
        except Exception as e:
            logger.warning(f"[queue-alert] email failed: {e}")
            return {"state": "email_failed", "waiting": waiting}
        _QUEUE_ALERT["last_sent_at"] = now
        _QUEUE_ALERT["last_count"] = waiting
        _QUEUE_ALERT["active"] = True
        return {"state": "alerted", "waiting": waiting, "oldest": oldest}
    except Exception as e:
        logger.error(f"[queue-alert] failed: {e}", exc_info=True)
        return {"state": "error"}


# ── AI cost reconciliation (PR-2) ─────────────────────────────────────────
# The admin key that can read the org's usage report is NOT on this service
# (it was breached twice). A private scheduled job holds it, calls Anthropic's
# usage/cost API, and POSTs the daily aggregates here with the CRON_SECRET.
# We compare the chatbot key's billed cost to the sum of ai_calls for that day
# and store the diff in ai_cost_daily; other keys' rows are kept so the owner
# can see how much of the bill is not the chatbot.
@router.post("/cron/ai-cost-report")
async def ai_cost_report(request: Request):
    _require_cron(request)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="Expected {'rows': [...]}")

    from app.ai.ledger import LEDGER_HEALTH

    chatbot_key = (settings.anthropic_api_key_id or "").strip()
    stored = 0
    ledger_failed = 0
    max_diff_pct = 0.0
    for r in rows:
        if not isinstance(r, dict):
            continue
        day = str(r.get("day", "")).strip()
        api_key_id = str(r.get("api_key_id", "")).strip()
        model = str(r.get("model", "")).strip()
        if not (day and api_key_id and model):
            continue
        billed = float(r.get("billed_cost_usd", 0) or 0)
        # Our ledger total for this day+model is only meaningful for our own key.
        ledger_cost = float(r.get("ledger_cost_usd", 0) or 0)
        if chatbot_key and api_key_id == chatbot_key:
            try:
                ledger_cost = await _ledger_cost_for(day, model)
            except Exception as e:
                # A failed read is not a 100% mismatch: skip the row rather
                # than overwrite a good one with ledger_cost_usd = 0.
                ledger_failed += 1
                logger.warning(f"[cost-report] ledger sum failed for {day}/{model}, row skipped: {type(e).__name__}: {e}")
                continue
        diff = round(billed - ledger_cost, 6)
        record = {
            "day": day, "api_key_id": api_key_id, "model": model,
            "billed_input_tokens": int(r.get("billed_input_tokens", 0) or 0),
            "billed_output_tokens": int(r.get("billed_output_tokens", 0) or 0),
            "billed_cost_usd": billed,
            "ledger_cost_usd": ledger_cost,
            "diff_usd": diff,
        }
        try:
            await db._run_sync(
                lambda rec=record: db.get_client().table("ai_cost_daily").upsert(
                    rec, on_conflict="day,api_key_id,model"
                ).execute(),
                idempotent=False, label="ai_cost_daily",
            )
            stored += 1
        except Exception as e:
            logger.warning(f"[cost-report] upsert failed for {day}/{model}: {type(e).__name__}: {e}")
        if chatbot_key and api_key_id == chatbot_key and billed > 0:
            max_diff_pct = max(max_diff_pct, abs(diff) / billed * 100.0)

    LEDGER_HEALTH["reconcile"] = {
        "stored": stored, "ledger_failed": ledger_failed, "max_diff_pct": round(max_diff_pct, 2),
    }
    if max_diff_pct > 15:
        from app.services.email import send_ops_alert_email

        await send_ops_alert_email(
            "AI cost reconciliation off",
            f"The chatbot key's billed cost differs from the ledger by {max_diff_pct:.1f}% "
            "on at least one model/day. See ai_cost_daily.",
        )
    return {"success": True, "data": {
        "stored": stored, "ledger_failed": ledger_failed, "max_diff_pct": round(max_diff_pct, 2),
    }}


_LEDGER_PAGE = 1000  # PostgREST's default max rows per response


async def _ledger_cost_for(day: str, model: str) -> float:
    """Sum of ai_calls.cost_usd for one UTC day and model.

    Reads every page over the half-open day [day, day+1). A failure raises:
    the caller skips the row instead of storing a false 0.
    """
    from datetime import date, timedelta

    start = date.fromisoformat(day)
    end = start + timedelta(days=1)
    total = 0.0
    offset = 0
    while True:
        res = await db._run_sync(
            lambda o=offset: db.get_client().table("ai_calls").select("cost_usd")
            .gte("created_at", f"{start.isoformat()}T00:00:00+00:00")
            .lt("created_at", f"{end.isoformat()}T00:00:00+00:00")
            .eq("model", model).order("id")
            .range(o, o + _LEDGER_PAGE - 1).execute(),
            label="ai_calls",
        )
        rows = getattr(res, "data", None) or []
        total += sum(float(r.get("cost_usd", 0) or 0) for r in rows)
        if len(rows) < _LEDGER_PAGE:
            break
        offset += _LEDGER_PAGE
    return round(total, 6)


# ── Weekly security report (PR-13) ────────────────────────────────────────
# Two levels of alert: the immediate ones (decoys, Basic, auth attacks, a new
# login place) go out as they happen; everything else is noise until it is
# summed here once a week — bans per jail, decoys touched, new login places,
# second-factor outcomes, role-gate refusals. Counts only, no PII.
@router.post("/cron/security-weekly")
async def security_weekly(request: Request):
    _require_cron(request)
    from datetime import timedelta

    from app.security.ban_guard import FAIL2BAN_HEALTH
    from app.security.decoys import DECOY_HEALTH
    from app.security.form_trap import FORM_TRAP_HEALTH
    from app.security.gates import GATE_HEALTH
    from app.security.mfa import MFA_HEALTH
    from app.security.visitor_token import VISITOR_TOKEN_HEALTH

    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    async def _rows(table: str, cols: str, ts_col: str) -> Optional[list]:
        try:
            res = await db._run_sync(
                lambda: db.get_client().table(table).select(cols).gte(ts_col, since).limit(5000).execute(),
                label=table,
            )
            return getattr(res, "data", None) or []
        except Exception as e:
            logger.warning(f"[security-weekly] {table} unavailable: {type(e).__name__}: {e}")
            return None

    bans = await _rows("security_bans", "jail", "last_seen_at")
    places = await _rows("login_locations", "user_id", "first_seen_at")
    by_jail: dict = {}
    for r in bans or []:
        by_jail[r.get("jail") or "?"] = by_jail.get(r.get("jail") or "?", 0) + 1

    report = {
        "since": since,
        "bans_by_jail": by_jail if bans is not None else "security_bans unavailable",
        "new_login_places": len(places) if places is not None else "login_locations unavailable",
        "since_restart": {
            "fail2ban": {k: FAIL2BAN_HEALTH[k] for k in ("would_ban", "banned", "blocked", "staff_alerts", "poisoned_skipped")},
            "decoys": dict(DECOY_HEALTH),
            "mfa": {k: MFA_HEALTH[k] for k in ("challenged", "enroll_required", "enrolled", "passed", "failed", "recovery_used", "new_place_alerts")},
            "role_gates": dict(GATE_HEALTH),
            "visitor_token": {k: VISITOR_TOKEN_HEALTH[k] for k in ("missing", "invalid", "expired", "mismatch", "refused")},
            "form_trap": dict(FORM_TRAP_HEALTH),
        },
    }
    lines = [f"Security week since {since[:10]}"]
    lines.append("Bans by jail: " + (", ".join(f"{k}={v}" for k, v in sorted(by_jail.items())) or "none")
                 if bans is not None else "Bans by jail: history unavailable (042 not applied)")
    lines.append(f"New login places: {report['new_login_places']}")
    for name, block in report["since_restart"].items():
        lines.append(f"{name}: " + ", ".join(f"{k}={v}" for k, v in block.items()))
    try:
        from app.services.email import send_ops_alert_email
        await send_ops_alert_email("weekly security report", "\n".join(lines))
    except Exception as e:
        logger.warning(f"[security-weekly] email failed: {type(e).__name__}: {e}")
    return {"success": True, "data": report, "count": 1}
