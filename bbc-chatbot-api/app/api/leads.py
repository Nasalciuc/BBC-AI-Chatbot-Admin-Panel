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
    if role in ("owner", "admin", "dev"):
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
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    try:
        tunnel = _enforce_tunnel(user, tunnel)
        rows, total = await db.get_leads(status=status, tier=tier, tunnel=tunnel, search=search, limit=limit, offset=offset)
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
async def update_lead(lead_id: str, body: dict):
    allowed = {"score", "tier", "cabin_class", "passengers", "flexible_dates", "notes", "intent_signals"}
    payload = {k: v for k, v in body.items() if k in allowed}
    if not payload:
        raise HTTPException(400, "No valid fields to update")
    result = await db.update_lead(lead_id, payload)
    if not result:
        raise HTTPException(404, "Lead not found")
    return result
