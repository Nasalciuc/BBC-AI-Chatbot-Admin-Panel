"""Admin API — users CRUD."""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from app.db import supabase as db
from app.models.admin import UserUpdate

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_ROLES = {"owner", "admin", "sales", "support"}
VALID_TUNNELS = {"sales", "support", "all"}


@router.get("/admin/users")
async def list_users(
    role:   Optional[str] = Query(None, pattern="^(owner|admin|sales|support)$"),
    search: Optional[str] = Query(None, max_length=100),
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    try:
        rows, total = await db.get_users(role=role, search=search, limit=limit, offset=offset)
        return {"success": True, "data": rows, "count": total}
    except Exception as e:
        logger.error(f"list_users error: {e}")
        raise HTTPException(status_code=500, detail="Failed to load users")


@router.patch("/admin/users/{user_id}")
async def update_user(user_id: str, body: UserUpdate):
    payload = body.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(400, "No valid fields to update")
    if "role" in payload and payload["role"] not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {VALID_ROLES}")
    if "tunnel_scope" in payload and payload["tunnel_scope"] not in VALID_TUNNELS:
        raise HTTPException(400, f"Invalid tunnel_scope. Must be one of: {VALID_TUNNELS}")
    result = await db.update_user(user_id, payload)
    if not result:
        raise HTTPException(404, "User not found")
    return {"success": True, "data": result, "count": 1}
