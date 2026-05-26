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


@router.post("/cron/abandoned-crm")
async def process_abandoned_conversations(request: Request):
    """Find conversations abandoned >30 min, submit to CRM with defaults, close."""

    if not settings.cron_secret or not settings.cron_secret.strip():
        raise HTTPException(status_code=503, detail="Cron endpoint not configured")

    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {settings.cron_secret}":
        raise HTTPException(status_code=401, detail="Invalid cron token")

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
                results.append({"id": cid, "status": "skipped", "reason": "already_in_crm"})
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

    success_count = sum(1 for r in results if r["status"] == "success")
    return {"processed": len(results), "success": success_count, "results": results}
