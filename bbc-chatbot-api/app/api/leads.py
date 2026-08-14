"""Admin API — leads CRUD."""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.db import supabase as db
from app.models.admin import LeadFull, LeadReviewUpdate, LeadStatusUpdate
from app.security.auth import get_current_user

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
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    # project_manager is intentionally NOT in this list — PM has no leads
    # access (Phase 1 trap), so it 403s here regardless of team scoping.
    if user.get("role") not in ("owner", "admin", "supervisor", "qa"):
        raise HTTPException(status_code=403, detail="Leads access restricted to admin/owner")
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
    return {"success": True, "reviewed": body.reviewed, "data": result}


@router.patch("/leads/{lead_id}")
async def update_lead(
    lead_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
):
    if user.get("role") == "qa":
        raise HTTPException(403, "QA role cannot modify leads")
    allowed = {"score", "tier", "cabin_class", "passengers", "flexible_dates", "notes", "intent_signals"}
    payload = {k: v for k, v in body.items() if k in allowed}
    if not payload:
        raise HTTPException(400, "No valid fields to update")
    result = await db.update_lead(lead_id, payload)
    if not result:
        raise HTTPException(404, "Lead not found")
    return result


@router.get("/leads/check/existing")
async def check_existing_lead(
    email: Optional[str] = Query(None, max_length=255),
    phone: Optional[str] = Query(None, max_length=50),
):
    """Check if a lead already exists in the system by email or phone.
    Used for: CRM duplicate detection, returning visitor identification.
    Returns: { exists: bool, lead: {...}, assigned_agent_id: "...", tunnel: "..." }
    """
    if not email and not phone:
        return {"success": False, "error": "Must provide email or phone", "exists": False}
    
    try:
        existing = await db.get_existing_lead_by_contact(email=email, phone=phone)
        if existing:
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
        return {"success": True, "exists": False}
    except Exception as e:
        return {"success": False, "error": str(e), "exists": False}


@router.patch("/leads/{lead_id}/mark-crm-created")
async def mark_lead_created_in_crm(lead_id: str, user: dict = Depends(get_current_user)):
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
        if lead.get("created_in_crm"):
            return {"success": True, "data": lead, "already": True}
        conv_id = lead.get("conversation_id")
        conv = await db.get_conversation_simple(conv_id) if conv_id else None
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
                    lead_id, crm_lead_id=result.request_id
                )
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
        )
        if not result.success:
            raise HTTPException(
                status_code=422,
                detail=f"CRM push failed: {result.error}",
            )
        updated = await db.get_lead_full(lead_id)
        return {"success": True, "data": updated or lead}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to push lead: {str(e)}")


@router.patch("/leads/{lead_id}/crm-id")
async def set_manual_crm_id(
    lead_id: str,
    crm_id: str = Query(..., min_length=1, max_length=64),
    user: dict = Depends(get_current_user),
):
    """The SEPARATE manual action: an operator entered the lead in the CRM
    by hand and pastes the CRM's id here. No push happens — the pasted id
    IS the proof. Distinct from mark-crm-created on purpose: a declaring
    button and a pushing button must never share a name."""
    if user.get("role") not in ("owner", "admin", "dev", "sales"):
        raise HTTPException(status_code=403, detail="Not authorized")
    crm_id = crm_id.strip()
    if not crm_id:
        # A whitespace id would flip the flag with NO proof stored —
        # exactly the lying state this PR exists to kill.
        raise HTTPException(status_code=422, detail="crm_id must be non-empty")
    try:
        result = await db.mark_lead_created_in_crm(lead_id, crm_lead_id=crm_id)
        if not result:
            raise HTTPException(404, "Lead not found")
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to record CRM id: {str(e)}")

