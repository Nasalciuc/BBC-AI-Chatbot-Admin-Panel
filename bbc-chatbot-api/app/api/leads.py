"""Admin API — leads CRUD."""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.db import supabase as db
from app.flags import flag
from app.models.admin import LeadFull, LeadReviewUpdate, LeadStatusUpdate
from app.security.auth import get_current_user
from app.services import lead_access

logger = logging.getLogger(__name__)
router = APIRouter()


def _enforce_tunnel(user: dict, tunnel: Optional[str]) -> Optional[str]:
    """Force tunnel filter for sales/support roles."""
    role = user.get("role", "sales")
    if role in ("owner", "admin", "dev", "supervisor", "qa"):
        return tunnel
    scope = user.get("tunnel_scope", role)
    if tunnel and tunnel != scope:
        raise HTTPException(status_code=403, detail="Access denied to this tunnel")
    return scope


@router.get("/leads")
async def list_leads(
    status: Optional[str] = Query(None, pattern="^(new|contacted|qualified|converted|lost)$"),
    tier:   Optional[str] = Query(None, pattern="^(gold|silver|bronze)$"),
    tunnel: Optional[str] = Query(None, pattern="^(sales|support)$"),
    search: Optional[str] = Query(None, max_length=100),
    include_drafts: bool = Query(False, description="Include leads not yet marked as Create Lead by an agent"),
    assigned_to: Optional[str] = Query(None, pattern="^(me|all|none)$"),
    reviewed: Optional[str] = Query(None, pattern="^(true|false)$"),
    crm_pending: bool = Query(False, description="CRM pending work-list: gold orphans + gate-refused leads the CRM never received"),
    view: Optional[str] = Query(None, pattern="^(not_in_crm|to_review|reviewed|all)$",
                                description="The Leads page's tabs. When set, level/chat_with/order apply and the "
                                            "response carries the tab counts."),
    level: Optional[str] = Query(None, pattern="^(ready|almost|early|contact)$"),
    chat_with: Optional[str] = Query(None, pattern="^(person|ai)$"),
    order: Optional[str] = Query(None, pattern="^(newest|oldest|closest)$"),
    tz_offset_minutes: int = Query(0, ge=-840, le=840,
                                   description="Minutes east of UTC: 'reviewed by you today' is the viewer's day."),
    limit:  int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    # project_manager is intentionally NOT in this list — PM has no leads
    # access (Phase 1 trap), so it 403s here regardless of team scoping.
    if user.get("role") not in ("owner", "admin", "supervisor", "qa"):
        raise HTTPException(status_code=403, detail="Leads access restricted to admin/owner")
    if view:
        return await _list_leads_view(
            user, view=view, search=search, level=level, chat_with=chat_with,
            order=order, limit=limit, offset=offset, tz_offset_minutes=tz_offset_minutes,
        )
    limit = min(limit, 200)
    try:
        tunnel = _enforce_tunnel(user, tunnel)

        user_role = user.get("role", "")
        user_id = user.get("id", "")

        # Phase 2: a supervisor sees only leads frozen to their own team(s).
        # Fail closed — a supervisor with no team sees an empty list.
        team_ids: Optional[list[str]] = None
        if user_role == "supervisor":
            team_ids = await db.get_team_ids_for_supervisor(user_id)
            if not team_ids:
                return {
                    "success": True, "data": [], "count": 0,
                    "review_stats": {"reviewed": 0, "total": 0},
                }

        if user_role == "qa":
            agent_filter = "all"
        elif user_role in ("sales", "support"):
            agent_filter = user_id
        elif assigned_to == "me":
            agent_filter = user_id
        elif assigned_to == "none":
            agent_filter = "none"
        else:
            agent_filter = "all"

        # team_ids is only ever set for a supervisor; for everyone else it
        # stays None and is omitted from the call so the DB signature is
        # byte-for-byte the pre-Phase-2 call (no regression for owner/admin/qa).
        _team_kw = {"team_ids": team_ids} if team_ids is not None else {}

        rows, total = await db.get_leads(
            status=status,
            tier=tier,
            tunnel=tunnel,
            search=search,
            assigned_to=agent_filter,
            include_drafts=include_drafts,
            reviewed_filter=reviewed,
            crm_pending=crm_pending,
            limit=limit,
            offset=offset,
            **_team_kw,
        )
        # Supervisors (QA) never receive raw customer PII.
        if user_role == "supervisor":
            from app.security.pii import mask_visitor_row
            rows = [mask_visitor_row(dict(r)) for r in rows]
        response: dict = {"success": True, "data": rows, "count": total}
        # review_stats counts the curated (created_in_crm=true) universe —
        # meaningless over the CRM-pending work-list, so skip it there.
        if user.get("role") in ("owner", "admin", "supervisor", "qa") and not crm_pending:
            reviewed_n, total_n = await db.get_leads_review_counts(
                status=status,
                tier=tier,
                tunnel=tunnel,
                search=search,
                assigned_to=agent_filter,
                include_drafts=include_drafts,
                **_team_kw,
            )
            response["review_stats"] = {"reviewed": reviewed_n, "total": total_n}
        return response
    except Exception as e:
        return {"success": False, "data": [], "count": 0, "error": str(e)}


async def _list_leads_view(
    user: dict, *, view: str, search: Optional[str], level: Optional[str],
    chat_with: Optional[str], order: Optional[str], limit: int, offset: int,
    tz_offset_minutes: int = 0,
) -> dict:
    """The Leads page: Not in the CRM / To review / Reviewed / All, with their counts.

    Same scope as the classic list: a supervisor sees only leads frozen to
    their own team(s), masked; a supervisor with no team sees nothing. A
    failed read is a 502 — never an empty list that reads as "no leads"."""
    from app.services.leads_view import build_leads_view

    role = user.get("role", "")
    try:
        rows = await db.get_leads_universe()
        names = await db.get_user_names()
    except Exception as e:
        logger.error(f"[leads_view] read failed: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="Couldn't load the leads") from e
    if role == "supervisor":
        team_ids = set(await db.get_team_ids_for_supervisor(user.get("id", "")) or [])
        rows = [r for r in rows if r.get("team_id") in team_ids] if team_ids else []
    now = datetime.now(timezone.utc)
    local = now + timedelta(minutes=tz_offset_minutes)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(minutes=tz_offset_minutes)
    built = build_leads_view(
        rows, view=view, names=names, search=search, level=level, with_=chat_with,
        order=order, limit=limit, offset=offset, viewer_id=user.get("id"), day_start=day_start,
        masked=role == "supervisor",
    )
    page = built["rows"]
    if role == "supervisor":
        from app.security.pii import mask_visitor_row
        page = [mask_visitor_row(dict(r)) for r in page]
    response = {
        "success": True, "data": page, "count": built["count"],
        "counts": built["counts"], "order": built["order"],
    }
    if "summary" in built:
        response["summary"] = built["summary"]
    if "review" in built:
        response["review"] = built["review"]
    return response


@router.get("/leads/{lead_id}/earlier")
async def earlier_leads(lead_id: str, user: dict = Depends(get_current_user)):
    """Other chats with the same phone or e-mail ("Earlier with us").
    Owner, admin and QA: the lookup is by contact details a supervisor
    doesn't see."""
    if user.get("role") not in ("owner", "admin", "qa"):
        raise HTTPException(status_code=403, detail="Not authorized")
    lead = await db.get_lead_full(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    try:
        rows = await db.get_earlier_leads(lead_id, lead.get("visitor_email"), lead.get("visitor_phone"))
    except Exception as e:
        logger.error(f"[earlier_leads] read failed for {lead_id}: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="Couldn't look up earlier chats") from e
    return {"success": True, "data": rows, "count": len(rows)}


@router.get("/leads/{lead_id}", response_model=LeadFull)
async def get_lead(lead_id: str, user: dict = Depends(get_current_user)):
    if user.get("role") not in ("owner", "admin", "supervisor", "qa"):
        raise HTTPException(status_code=403, detail="Not authorized")
    lead = await db.get_lead_full(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    # Supervisors (QA) never receive raw customer PII.
    if user.get("role") == "supervisor":
        from app.security.pii import mask_visitor_row
        lead = mask_visitor_row(dict(lead))
    return lead


@router.patch("/leads/{lead_id}/status")
async def update_lead_status(
    lead_id: str,
    body: LeadStatusUpdate,
    user: dict = Depends(get_current_user),
):
    if user.get("role") not in ("owner", "admin", "supervisor"):
        raise HTTPException(status_code=403, detail="Not authorized")
    valid = {"new", "contacted", "qualified", "converted", "lost"}
    if body.status not in valid:
        raise HTTPException(400, f"Invalid status. Must be one of: {valid}")
    payload: dict = {"status": body.status}
    if body.status == "contacted": payload["contacted_at"] = datetime.utcnow().isoformat()
    if body.status == "converted": payload["converted_at"] = datetime.utcnow().isoformat()
    if body.notes: payload["notes"] = body.notes
    result = await db.update_lead(lead_id, payload)
    if not result:
        raise HTTPException(404, "Lead not found")
    db.invalidate_leads_universe()
    return result


@router.patch("/leads/{lead_id}/review")
async def update_lead_review(
    lead_id: str,
    body: LeadReviewUpdate,
    user: dict = Depends(get_current_user),
):
    if user.get("role") not in ("owner", "admin", "supervisor", "qa"):
        raise HTTPException(status_code=403, detail="Not authorized to review leads")

    update_data: dict = {
        "reviewed_by_qa": body.reviewed,
        "reviewed_at": datetime.now(timezone.utc).isoformat() if body.reviewed else None,
        "reviewed_by": user.get("id") if body.reviewed else None,
    }
    if body.qa_notes is not None:
        update_data["qa_notes"] = body.qa_notes

    result = await db.update_lead(lead_id, update_data)
    if not result:
        raise HTTPException(status_code=404, detail="Lead not found")
    db.invalidate_leads_universe()
    return {"success": True, "reviewed": body.reviewed, "data": result}


@router.patch("/leads/{lead_id}")
async def update_lead(
    lead_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
):
    if user.get("role") == "qa":
        raise HTTPException(403, "QA role cannot modify leads")
    # flexible_dates left with PR-14 (dates_flexibility replaced it in PR-7;
    # migration 050 drops the column).
    allowed = {"score", "tier", "cabin_class", "passengers", "dates_flexibility", "notes", "intent_signals"}
    payload = {k: v for k, v in body.items() if k in allowed}
    if not payload:
        raise HTTPException(400, "No valid fields to update")
    if user.get("role") == "sales":
        # An agent edits their own leads, not a colleague's (hole B, app.services.lead_access).
        existing = await db.get_lead_full(lead_id)
        if not existing:
            raise HTTPException(404, "Lead not found")
        await lead_access.check(user, existing, "lead edit")
    if "dates_flexibility" in payload and payload["dates_flexibility"] not in ("exact", "flexible", "window", "unknown"):
        raise HTTPException(400, "dates_flexibility must be exact, flexible, window or unknown")
    result = await db.update_lead(lead_id, payload)
    if not result:
        raise HTTPException(404, "Lead not found")
    db.invalidate_leads_universe()
    return result


@router.get("/leads/check/existing")
async def check_existing_lead(
    email: Optional[str] = Query(None, max_length=255),
    phone: Optional[str] = Query(None, max_length=50),
    user: dict = Depends(get_current_user),
):
    """Check if a lead already exists in the system by email or phone.
    Used for: CRM duplicate detection, returning visitor identification.
    Returns: { exists: bool, lead: {...}, assigned_agent_id: "...", tunnel: "..." }

    Management only (owner, admin, dev): it answers a client's name and contact for an e-mail or a phone, which
    an agent may not read for a chat that isn't theirs (lead_access, the blind line).
    """
    from app.security.gates import MANAGEMENT

    if (user.get("role") or "") not in MANAGEMENT:
        raise HTTPException(403, "The contact check is for management")
    if not email and not phone:
        return {"success": False, "error": "Must provide email or phone", "exists": False}
    existing = None
    try:
        existing = await db.get_existing_lead_by_contact(email=email, phone=phone)
    except Exception as e:
        # Logged with its cause in get_existing_lead_by_contact; the caller gets no database text.
        logger.error(f"[leads] the contact check could not run: {type(e).__name__}")
        return {"success": False, "error": "The check could not run. Try again.", "exists": False}
    if not existing:
        return {"success": True, "exists": False}
    return {
        "success": True,
        "exists": True,
        "lead_id": existing.get("id"),
        "conversation_id": existing.get("conversation_id"),
        "visitor_name": existing.get("visitor_name"),
        "visitor_email": existing.get("visitor_email"),
        "visitor_phone": existing.get("visitor_phone"),
        "assigned_agent_id": existing.get("assigned_agent_id"),
        "lead_tier": existing.get("tier"),
        "lead_status": existing.get("status"),
        "tunnel": existing.get("tunnel"),
    }


@router.patch("/leads/{lead_id}/mark-crm-created")
async def mark_lead_created_in_crm(
    lead_id: str,
    confirm_locked: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    """PUSH the lead to the CRM — the button does what it says (#1372 Jia:
    the old endpoint only flipped the flag; the CRM never saw the lead).

    Runs the ONE push path (gate → submit → flag only on 2xx + crm id).
    Success returns the updated lead (with crm_lead_id); a CRM failure or
    a gate refusal returns 422 with the reason and the flag stays false.

    Still blocked when the parent conversation's derived tag is Abandoned
    or No engagement — neither is a real lead.
    """
    if user.get("role") not in ("owner", "admin", "dev", "sales"):
        raise HTTPException(status_code=403, detail="Not authorized to push leads to the CRM")

    try:
        lead = await db.get_lead_full(lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        # An agent pushes their own leads, not a colleague's (hole B, app.services.lead_access).
        await lead_access.check(user, lead, "crm push")
        if lead.get("created_in_crm"):
            return {"success": True, "data": lead, "already": True}
        conv_id = lead.get("conversation_id")
        conv = await db.get_conversation_simple(conv_id) if conv_id else None
        # PR-15: a conversation locked for abuse goes to the CRM only when the
        # operator confirms it (the panel asks, then resends with confirm=1).
        from app.security.behavior import is_locked
        if conv and is_locked(conv.get("metadata")) and not confirm_locked:
            raise HTTPException(
                status_code=409,
                detail="This conversation was locked for abuse. Confirm to push it anyway.",
            )
        _tag = db.derive_conversation_tag(conv, lead=lead) if conv else None
        if _tag in ("abandoned", "no_engagement"):
            # BUSINESS RULE (owner, explicit): every captured contact is
            # dialable — silent leads GO to CRM through the defaults path
            # (AAA route placeholders, +30d departure). The old 409 was one
            # of three layers strangling the original AAA design.
            from app.services.crm import submit_abandoned_to_crm

            _conv_payload = dict(conv or {})
            _conv_payload.setdefault("id", conv_id)
            if _tag == "no_engagement":
                _conv_payload["_no_engagement"] = True
            result = await submit_abandoned_to_crm(_conv_payload, lead)
            if result.success and result.request_id:
                await db.mark_lead_created_in_crm(
                    lead_id, crm_lead_id=result.request_id, source="push", set_by=user.get("id")
                )
                db.invalidate_leads_universe()
                updated = await db.get_lead_full(lead_id)
                return {
                    "success": True,
                    "data": updated or lead,
                    "pushed_with_defaults": True,
                }
            raise HTTPException(
                status_code=422, detail=f"CRM push failed: {result.error}"
            )

        from types import SimpleNamespace

        from app.services.crm import push_lead_to_crm

        conv = conv or {}
        visitor = SimpleNamespace(
            name=conv.get("visitor_name") or "",
            email=conv.get("visitor_email") or "",
            phone=conv.get("visitor_phone") or "",
        )
        result = await push_lead_to_crm(
            lead,
            visitor,
            conv_id or lead_id,
            conv_metadata=conv.get("metadata"),
            suid=conv.get("visitor_id"),
            set_by=user.get("id"),
        )
        if not result.success:
            # The refusal reason was written on the lead: show it on the list.
            db.invalidate_leads_universe()
            raise HTTPException(
                status_code=422,
                detail=f"CRM push failed: {result.error}",
            )
        db.invalidate_leads_universe()
        updated = await db.get_lead_full(lead_id)
        return {"success": True, "data": updated or lead}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to push lead: {str(e)}")


# The CRM's own ids: 24 hexadecimal characters (811 of the last 812, Nov 2026).
_CRM_ID_SHAPE = re.compile(r"[0-9a-fA-F]{24}")


def crm_id_shape(crm_id: str) -> str:
    """"ok" when the id looks like one the CRM gives, else "odd" (accepted when confirmed, and sampled by QA)."""
    return "ok" if _CRM_ID_SHAPE.fullmatch(crm_id or "") else "odd"


def _client_keys(conv: Optional[dict]) -> set[str]:
    """What makes two chats one client, by the Offers view's own rule (offers_view._keys): the e-mail, a
    placed number by ALL its digits (+1 207 946 0958 and +44 20 7946 0958 are two clients), a number to
    check only against the same number to check."""
    from app.services.offers_view import _keys

    conv = conv or {}
    meta = conv.get("metadata") if isinstance(conv.get("metadata"), dict) else {}
    return set(_keys({"conversation_id": None, "visitor_email": conv.get("visitor_email"),
                      "visitor_phone": conv.get("visitor_phone"), "phone_to_check": meta.get("phone_to_check")})[1:])


async def _crm_id_on_another_client(crm_id: str, lead: dict) -> Optional[str]:
    """The chat number of another client's lead that already carries this CRM id, if any.

    One client with several chats is one CRM lead: their other chats may carry it. Raises when it can't
    be checked; the route then refuses (a duplicate check that cannot run must not pass)."""
    others = [r for r in await db.get_leads_by_crm_id(crm_id) if r.get("id") != lead.get("id")]
    if not others:
        return None
    mine = _client_keys(await db.get_conversation_simple(lead["conversation_id"])) if lead.get("conversation_id") else set()
    for other in others:
        conv = await db.get_conversation_simple(other["conversation_id"]) if other.get("conversation_id") else None
        if not (mine & _client_keys(conv)):
            return str((conv or {}).get("chat_number") or "another chat")
    return None


@router.patch("/leads/{lead_id}/crm-id")
async def set_manual_crm_id(
    lead_id: str,
    crm_id: str = Query(..., min_length=1, max_length=64),
    confirm_odd: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    """The SEPARATE manual action: an operator entered the lead in the CRM
    by hand and pastes the CRM's id here. No push happens — the pasted id
    IS the proof. Distinct from mark-crm-created on purpose: a declaring
    button and a pushing button must never share a name.

    Since 21 Nov: only on the agent's own lead (hole B); an id that does not
    look like the CRM's asks once (422, then confirm_odd=1) and is kept marked
    odd for QA; the same id on another client's lead is a 409 naming that chat;
    who pasted it and when is noted on the chat (metadata.crm) and in audit_log."""
    if user.get("role") not in ("owner", "admin", "dev", "sales"):
        raise HTTPException(status_code=403, detail="Not authorized")
    crm_id = crm_id.strip()
    if not crm_id:
        # A whitespace id would flip the flag with NO proof stored —
        # exactly the lying state this PR exists to kill.
        raise HTTPException(status_code=422, detail="crm_id must be non-empty")
    try:
        lead = await db.get_lead_full(lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        await lead_access.check(user, lead, "crm id")
        shape = crm_id_shape(crm_id)
        if shape == "odd" and flag("CRM_ID_SHAPE_CHECK", True) and not confirm_odd:
            raise HTTPException(
                status_code=422,
                detail="This doesn't look like an id from the CRM (24 letters and digits). Send it again to keep it.",
            )
        try:
            taken = await _crm_id_on_another_client(crm_id, lead)
        except Exception as e:
            logger.error(f"[crm-id] lead={lead_id}: the duplicate check did not run ({type(e).__name__}: {e})")
            raise HTTPException(503, "Couldn't check whether this id is already used. Try again.") from e
        if taken:
            raise HTTPException(status_code=409, detail=f"This CRM id is already on chat #{taken}, another client.")
        result = await db.mark_lead_created_in_crm(
            lead_id, crm_lead_id=crm_id, source="manual", set_by=user.get("id"), shape=shape,
        )
        if not result:
            raise HTTPException(404, "Lead not found")
        await db.log_audit(user.get("id"), user.get("email"), "crm_id_set", "leads", lead_id,
                           {"shape": shape, "conversation_id": lead.get("conversation_id")})
        db.invalidate_leads_universe()
        return {"success": True, "data": result, "count": 1, "shape": shape}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[crm-id] lead={lead_id}: not recorded ({type(e).__name__}: {e})")
        raise HTTPException(500, "Failed to record CRM id") from e
