"""Admin API — conversations CRUD."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.db import supabase as db
from app.models.admin import ConversationUpdate
from app.security.auth import get_current_user

router = APIRouter()


def _enforce_tunnel(user: dict, tunnel: Optional[str]) -> Optional[str]:
    """Force tunnel filter for sales/support roles."""
    role = user.get("role", "sales")
    if role in ("owner", "admin", "dev"):
        return tunnel  # privileged users can filter freely
    scope = user.get("tunnel_scope", role)
    if tunnel and tunnel != scope:
        raise HTTPException(status_code=403, detail="Access denied to this tunnel")
    return scope


@router.get("/conversations")
async def list_conversations(
    tunnel: Optional[str] = Query(None, pattern="^(sales|support)$"),
    status: Optional[str] = Query(None, pattern="^(active|pending|closed)$"),
    search: Optional[str] = Query(None, max_length=100),
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    try:
        tunnel = _enforce_tunnel(user, tunnel)
        rows, total = await db.get_conversations(tunnel=tunnel, status=status, search=search, limit=limit, offset=offset)
        return {"success": True, "data": rows, "count": total}
    except Exception as e:
        return {"success": False, "data": [], "count": 0, "error": str(e)}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    try:
        conv = await db.get_conversation(conversation_id)
        if not conv:
            return {"success": False, "data": None, "count": 0, "error": "Not found"}
        return {"success": True, "data": conv, "count": 1}
    except Exception as e:
        return {"success": False, "data": None, "count": 0, "error": str(e)}


@router.patch("/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, body: ConversationUpdate):
    payload = body.model_dump(exclude_none=True)
    if not payload:
        return {"success": False, "data": None, "count": 0, "error": "No fields to update"}
    try:
        result = await db.update_conversation(conversation_id, payload)
        if not result:
            return {"success": False, "data": None, "count": 0, "error": "Conversation not found"}
        return {"success": True, "data": result, "count": 1}
    except Exception as e:
        return {"success": False, "data": None, "count": 0, "error": str(e)}
