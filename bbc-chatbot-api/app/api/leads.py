"""Admin API — leads CRUD."""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.db import supabase as db
from app.models.admin import LeadFull, LeadStatusUpdate
from app.security.auth import get_current_user

router = APIRouter()


def _enforce_tunnel(user: dict, tunnel: Optional[str]) -> Optional[str]:
    """Force tunnel filter for sales/support roles."""
    role = user.get("role", "sales")
    if role in ("owner", "admin", "dev", "qa"):
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
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Leads access restricted to admin/owner")
    try:
        tunnel = _enforce_tunnel(user, tunnel)

        user_role = user.get("role", "")
        user_id = user.get("id", "")
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

        rows, total = await db.get_leads(
            status=status,
            tier=tier,
            tunnel=tunnel,
            search=search,
            assigned_to=agent_filter,
            include_drafts=include_drafts,
            limit=limit,
            offset=offset,
        )
        return {"success": True, "data": rows, "count": total}
    except Exception as e:
        return {"success": False, "data": [], "count": 0, "error": str(e)}


@router.get("/leads/{lead_id}", response_model=LeadFull)
async def get_lead(lead_id: str):
    lead = await db.get_lead_full(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead


@router.patch("/leads/{lead_id}/status")
async def update_lead_status(lead_id: str, body: LeadStatusUpdate):
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
    """Mark a lead as successfully created in the external CRM system.
    Updates created_in_crm flag and timestamp.
    Requires authentication."""
    if user.get("role") not in ("owner", "admin", "dev", "sales"):
        raise HTTPException(status_code=403, detail="Not authorized to mark leads as CRM created")
    
    try:
        result = await db.mark_lead_created_in_crm(lead_id)
        if not result:
            raise HTTPException(404, "Lead not found")
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to mark lead: {str(e)}")

