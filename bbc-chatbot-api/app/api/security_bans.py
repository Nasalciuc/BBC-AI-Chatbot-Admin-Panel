"""Admin API — Security → Bans (PR-11).

The history comes from security_bans (the audit trail); the live state is the
ban store. Unban removes the live ban and stamps the row with who did it.
Owner/admin/dev only, behind the log-first role gate.
"""

import datetime as _dt
import logging

from fastapi import APIRouter, Depends

from app.db.supabase import _run_sync, get_client
from app.security.auth import get_current_user
from app.security.gates import MANAGEMENT, enforce_or_log

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/admin/security/bans")
async def list_bans(user: dict = Depends(get_current_user)):
    if not enforce_or_log(MANAGEMENT, user, "list_bans"):
        return {"success": False, "data": [], "count": 0, "error": "Forbidden"}
    try:
        res = await _run_sync(
            lambda: get_client().table("security_bans")
            .select("id,identity,jail,reason,first_seen_at,last_seen_at,expires_at,ban_count,unbanned_by,unbanned_at")
            .order("last_seen_at", desc=True).limit(200).execute(),
            label="security_bans",
        )
        rows = getattr(res, "data", None) or []
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        logger.warning(f"[bans] list failed: {type(e).__name__}: {e}")
        return {"success": False, "data": [], "count": 0, "error": "Bans history unavailable"}


@router.post("/admin/security/bans/{ban_id}/unban")
async def unban(ban_id: str, user: dict = Depends(get_current_user)):
    if not enforce_or_log(MANAGEMENT, user, "unban", f"ban={ban_id}"):
        return {"success": False, "data": None, "count": 0, "error": "Forbidden"}
    from app.security.ban_store import get_store

    try:
        res = await _run_sync(
            lambda: get_client().table("security_bans").select("identity").eq("id", ban_id).limit(1).execute(),
            label="security_bans",
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return {"success": False, "data": None, "count": 0, "error": "Not found"}
        identity = rows[0]["identity"]
        store = get_store()
        if store is not None and not await store.unban(identity):
            # The live ban is still there: recording it as lifted would hide
            # the Unban button and leave the customer blocked for up to a week.
            return {"success": False, "data": None, "count": 0, "error": "Unban failed"}
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        actor = user.get("email") or user.get("id") or "unknown"
        await _run_sync(
            lambda: get_client().table("security_bans")
            .update({"unbanned_by": actor, "unbanned_at": now}).eq("id", ban_id).execute(),
            label="security_bans",
        )
        return {"success": True, "data": {"id": ban_id}, "count": 1}
    except Exception as e:
        logger.warning(f"[bans] unban failed: {type(e).__name__}: {e}")
        return {"success": False, "data": None, "count": 0, "error": "Unban failed"}



# ── PR-15: the guard report and the chat lock ─────────────────

@router.get("/admin/security/trap-report")
async def trap_report(user: dict = Depends(get_current_user)):
    """What the shadow traps would have caught, and how many were real
    clients. The owner turns a trap's *_ENFORCE on only where that is 0."""
    if not enforce_or_log(MANAGEMENT, user, "trap_report"):
        return {"success": False, "data": {}, "count": 0, "error": "Forbidden"}
    from app.security.behavior import BEHAVIOR_HEALTH, build_trap_report

    report = await build_trap_report()
    return {"success": True, "data": {"traps": report, "counters": BEHAVIOR_HEALTH}, "count": len(report)}


@router.post("/admin/conversations/{conversation_id}/unlock")
async def unlock_conversation(conversation_id: str, user: dict = Depends(get_current_user)):
    """Lift a chat lock (metadata.locked = "abuse"), re-reading the fresh
    metadata so nothing else written meanwhile is lost."""
    if not enforce_or_log(MANAGEMENT, user, "unlock", f"conv={conversation_id}"):
        return {"success": False, "data": None, "count": 0, "error": "Forbidden"}
    from app.db import supabase as db

    conv = await db.get_conversation_simple(conversation_id)
    if not conv:
        return {"success": False, "data": None, "count": 0, "error": "Not found"}
    meta = dict(conv.get("metadata") or {})
    for k in ("locked", "locked_reason", "locked_at"):
        meta.pop(k, None)
    meta["unlocked_by"] = user.get("email") or user.get("id") or "unknown"
    meta["unlocked_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    await db.update_conversation(conversation_id, {"metadata": meta})
    return {"success": True, "data": {"id": conversation_id}, "count": 1}
