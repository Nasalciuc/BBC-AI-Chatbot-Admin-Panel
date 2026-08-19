"""
Cron endpoint — process abandoned conversations.
Auth: CRON_SECRET header (not JWT).
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from config.settings import settings
from app.db import supabase as db
from app.services.crm import format_phone_international, submit_abandoned_to_crm
from app.services.lead_service import get_or_create_lead

logger = logging.getLogger(__name__)
router = APIRouter()


# One-time AAA backfill — flips after the first run in this process;
# true idempotency is the created_in_crm flag (a second run pushes nothing).
_AAA_BACKFILL_RAN = False


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


async def run_aaa_backfill(days: int = 30) -> dict:
    """The contacts the old guards blocked — pushed once, safely.

    The full 30-day window, active AND closed, any mode: skips leads
    already in the CRM (idempotency lives on the flag), dedups by contact
    so one human never becomes two CRM rows, and pushes the rest through
    the defaults path with normal proof discipline (2xx + crm_id only).
    A gate/contract refusal is NOT a loss: the row keeps its reason and
    surfaces in the panel's "CRM pending" work-list for a human."""
    convs = pick_richest_by_contact(
        await db.get_recent_contact_conversations(days=days)
    )
    pushed = 0
    work_list = 0
    skipped = 0
    for conv in convs:
        cid = conv["id"]
        try:
            lead = await get_or_create_lead(cid)
            if not lead or lead.get("created_in_crm"):
                skipped += 1
                continue
            if not conv.get("last_user_message_at"):
                conv["_no_engagement"] = True
            result = await submit_abandoned_to_crm(conv, lead)
            if result.success and result.request_id:
                await db.mark_lead_created_in_crm(
                    lead["id"], crm_lead_id=result.request_id
                )
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
    global _AAA_BACKFILL_RAN
    if not _AAA_BACKFILL_RAN:
        _AAA_BACKFILL_RAN = True
        try:
            await run_aaa_backfill()
        except Exception as e:
            logger.error(f"[cron] AAA backfill failed (non-fatal): {e}")

    abandoned = await db.get_abandoned_conversations(settings.abandoned_timeout_minutes)
    logger.info(f"[cron] Found {len(abandoned)} abandoned conversations")

    # One human = one CRM row, in the live sweep too. Without this the
    # backfill's careful dedup is undone minutes later by the next tick.
    _push_owners = {c["id"] for c in pick_richest_by_contact(abandoned)}

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
            name = (conv.get("visitor_name") or "").strip() or "Customer"

            lead = await get_or_create_lead(cid)
            if lead and lead.get("created_in_crm") and not _may_close:
                # In the CRM already, and this conversation must not be
                # touched (a human owns it, or it is already closed):
                # nothing left to do.
                results.append({"id": cid, "status": "already_done"})
                continue
            if lead and lead.get("created_in_crm"):
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
                logger.info(f"[cron][{cid}] Closed existing CRM lead ({name})")
                results.append({"id": cid, "status": "closed_existing_crm", "name": name})
                continue

            if not lead:
                lead = await db.ensure_lead_for_conversation(cid)
                if not lead:
                    results.append({"id": cid, "status": "error", "error": "lead_create_failed"})
                    continue

            if cid not in _push_owners:
                # A richer conversation for this same human owns the push.
                # Visible state, no CRM call, no close (closing it would
                # orphan the row silently).
                await _persist_refusal(lead["id"], "gate:duplicate_contact")
                results.append({"id": cid, "status": "dedup_twin"})
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
                    f"[cron][{cid}] Success: CRM submitted ({name})"
                    f"{' + closed' if _may_close else ' (left open — human/closed)'}"
                )
                results.append({"id": cid, "status": "success", "name": name})
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
    """Backstop sweep: fall back conversations past the agent response deadline."""
    from app.api.agent import _enforce_response_deadline

    swept = await _enforce_response_deadline()
    logger.info(f"[cron][agent-response-sweep] swept={swept}")
    return {"success": True, "swept": swept}


async def run_cleanup_stale_ready() -> dict:
    """Auto-reset is_ready for operators who disconnected without toggling off."""
    stale = await db.reset_stale_ready_users(settings.agent_timeout_seconds)
    names = [u.get("name") or u["id"] for u in stale]
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


@router.post("/cron/abandoned-crm")
async def process_abandoned_conversations(request: Request):
    """Find conversations abandoned >30 min, submit to CRM with defaults, close."""

    if not settings.cron_secret or not settings.cron_secret.strip():
        raise HTTPException(status_code=503, detail="Cron endpoint not configured")

    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {settings.cron_secret}":
        raise HTTPException(status_code=401, detail="Invalid cron token")

    return await run_abandoned_crm()


@router.post("/cron/agent-response-sweep")
async def agent_response_sweep(request: Request):
    """Backstop sweep: fall back conversations whose assigned agent sent zero messages
    within the response deadline. Covers the window when no agents are online
    (heartbeats don't run → stale cleanup doesn't run → conversations pile up).
    Auth: same Bearer CRON_SECRET as abandoned-crm."""
    if not settings.cron_secret or not settings.cron_secret.strip():
        raise HTTPException(status_code=503, detail="Cron endpoint not configured")

    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {settings.cron_secret}":
        raise HTTPException(status_code=401, detail="Invalid cron token")

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
    if not settings.cron_secret or not settings.cron_secret.strip():
        raise HTTPException(status_code=503, detail="Cron endpoint not configured")

    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {settings.cron_secret}":
        raise HTTPException(status_code=401, detail="Invalid cron token")

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
