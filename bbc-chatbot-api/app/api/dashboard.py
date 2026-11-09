"""Admin API — Dashboard statistics."""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import supabase as db
from app.models.admin import DashboardStats
from app.security.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/dashboard/stats", response_model=DashboardStats)
async def get_stats(user: dict = Depends(get_current_user)):
    """Full dashboard statistics. Falls back to 0 for any failed query."""
    role = user.get("role", "sales")
    tunnel_filter: Optional[str] = None
    agent_filter: Optional[str] = None
    if role not in ("owner", "admin", "dev", "qa"):
        tunnel_filter = user.get("tunnel_scope", role)
    # Sales/support see ONLY their own assigned data (not team-wide)
    if role in ("sales", "support"):
        agent_filter = user.get("id")
    stats = await db.get_dashboard_stats(
        tunnel_filter=tunnel_filter,
        agent_filter=agent_filter,
    )
    return DashboardStats(**stats)


# ── The owner's Dashboard (Overview, Conversion, Agents, Quality) ──

OWNER_DASHBOARD_ROLES = {"owner", "admin", "dev"}
OWNER_CACHE_TTL_SECONDS = 60
# One read per (days, offset) a minute, shared by every owner/admin: the page
# polls, and several people opening it must not multiply ~15 paged reads.
_OWNER_CACHE: dict[tuple[int, int], tuple[float, dict]] = {}
_OWNER_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}
OWNER_DASHBOARD_HEALTH: dict = {"hits": 0, "builds": 0, "read_failures": 0, "last_build_ms": None}


@router.get("/dashboard/owner")
async def get_owner_dashboard(
    days: int = Query(7),
    tz_offset_minutes: int = Query(0, ge=-840, le=840),
    user: dict = Depends(get_current_user),
):
    """Every tab of the owner's Dashboard, read-only, for the whole business.

    days: 1 (today), 7 or 30. tz_offset_minutes: minutes EAST of UTC (the
    panel sends -Date#getTimezoneOffset()), so days and hours are the viewer's.
    Owner, admin and dev only: the page names every agent and shows AI cost."""
    from app.services.owner_dashboard import ALLOWED_DAYS, build_owner_dashboard, fetch_since

    if user.get("role") not in OWNER_DASHBOARD_ROLES:
        raise HTTPException(status_code=403, detail="Only owner, admin and dev see this dashboard")
    if days not in ALLOWED_DAYS:
        raise HTTPException(status_code=422, detail="days must be 1, 7 or 30")

    key = (days, tz_offset_minutes)
    cached = _OWNER_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < OWNER_CACHE_TTL_SECONDS:
        OWNER_DASHBOARD_HEALTH["hits"] += 1
        return {"success": True, "data": cached[1], "count": 1}

    lock = _OWNER_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _OWNER_CACHE.get(key)
        if cached and time.monotonic() - cached[0] < OWNER_CACHE_TTL_SECONDS:
            OWNER_DASHBOARD_HEALTH["hits"] += 1
            return {"success": True, "data": cached[1], "count": 1}
        started = time.monotonic()
        now = datetime.now(timezone.utc)
        since = fetch_since(now, tz_offset_minutes, days)
        try:
            inputs = await db.get_owner_dashboard_inputs(
                since.isoformat(), (since - timedelta(days=1)).isoformat()
            )
        except Exception as e:
            # A dashboard built from a partial read would show wrong numbers
            # as if they were right.
            OWNER_DASHBOARD_HEALTH["read_failures"] += 1
            logger.error(f"[owner_dashboard] read failed: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail="Couldn't load the dashboard") from e
        data = build_owner_dashboard(
            now=now, offset_minutes=tz_offset_minutes, days=days, **inputs
        )
        OWNER_DASHBOARD_HEALTH["builds"] += 1
        OWNER_DASHBOARD_HEALTH["last_build_ms"] = int((time.monotonic() - started) * 1000)
        _OWNER_CACHE[key] = (time.monotonic(), data)
    return {"success": True, "data": data, "count": 1}
