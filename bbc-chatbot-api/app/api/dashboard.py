"""Admin API — Dashboard statistics."""
from typing import Optional
from fastapi import APIRouter, Depends
from app.db import supabase as db
from app.models.admin import DashboardStats
from app.security.auth import get_current_user

router = APIRouter()


@router.get("/dashboard/stats", response_model=DashboardStats)
async def get_stats(user: dict = Depends(get_current_user)):
    """Full dashboard statistics. Falls back to 0 for any failed query."""
    role = user.get("role", "sales")
    tunnel_filter: Optional[str] = None
    if role not in ("owner", "admin", "dev", "qa"):
        tunnel_filter = user.get("tunnel_scope", role)
    stats = await db.get_dashboard_stats(tunnel_filter=tunnel_filter)
    return DashboardStats(**stats)
