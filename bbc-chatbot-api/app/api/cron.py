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


async def run_abandoned_crm() -> dict:
    """Find conversations abandoned >30 min, submit to CRM with defaults, close."""
    abandoned = await db.get_abandoned_conversations(settings.abandoned_timeout_minutes)
    logger.info(f"[cron] Found {len(abandoned)} abandoned conversations")

    results = []
    for conv in abandoned:
        cid = conv["id"]
        try:
            phone = format_phone_international(conv.get("visitor_phone", ""))
            email = (conv.get("visitor_email") or "").strip()
            name = (conv.get("visitor_name") or "").strip()

            if not phone or len(phone) < 8:
                results.append({"id": cid, "status": "skipped", "reason": "phone_invalid"})
                continue
            if not email or "@" not in email:
                results.append({"id": cid, "status": "skipped", "reason": "email_invalid"})
                continue
            if not name or len(name) < 2:
                results.append({"id": cid, "status": "skipped", "reason": "name_invalid"})
                continue

            lead = await get_or_create_lead(cid)
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

            crm_result = await submit_abandoned_to_crm(conv, lead)

            if crm_result.success:
                await db.mark_lead_created_in_crm(lead["id"])
                await db.update_conversation(cid, {
                    "status": "closed",
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "ai",
                    "assigned_agent_id": None,
                })
                logger.info(f"[cron][{cid}] Success: CRM submitted + closed ({name})")
                results.append({"id": cid, "status": "success", "name": name})
            else:
                logger.error(f"[cron][{cid}] CRM fail: {crm_result.error}")
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
