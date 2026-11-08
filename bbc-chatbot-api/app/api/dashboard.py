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


# ── The new Dashboard (Overview, Conversion, Agents, Quality) ──
# One read per (days, offset) a minute, shared by every viewer: the page polls,
# and several people opening it must not multiply ~15 paged reads. Each scope
# (all chats, sales, support) is built once a minute from that read; what a
# role may see is cut from the shared build on every request (cheap).

OWNER_DASHBOARD_ROLES = {"owner", "admin", "dev"}
TEAM_DASHBOARD_ROLES = {"supervisor", "project_manager", "qa"}
DASHBOARD_CACHE_TTL_SECONDS = 60
_INPUTS_CACHE: dict[tuple[int, int], tuple[float, datetime, dict]] = {}
_BUILD_CACHE: dict[tuple[int, int, Optional[str]], tuple[float, dict]] = {}
_DASH_LOCKS: dict[tuple, asyncio.Lock] = {}
OWNER_DASHBOARD_HEALTH: dict = {"hits": 0, "builds": 0, "read_failures": 0, "last_build_ms": None}


def _fresh(at: float) -> bool:
    return time.monotonic() - at < DASHBOARD_CACHE_TTL_SECONDS


def _prune(cache: dict) -> None:
    """Drop expired entries: viewers in many time zones would otherwise keep
    one 30-day read each in memory for good."""
    for k in [k for k, v in cache.items() if not _fresh(v[0])]:
        cache.pop(k, None)


async def _inputs_for(days: int, offset: int) -> tuple[datetime, dict]:
    from app.services.owner_dashboard import fetch_since

    key = (days, offset)
    cached = _INPUTS_CACHE.get(key)
    if cached and _fresh(cached[0]):
        return cached[1], cached[2]
    async with _DASH_LOCKS.setdefault(("inputs", *key), asyncio.Lock()):
        cached = _INPUTS_CACHE.get(key)
        if cached and _fresh(cached[0]):
            return cached[1], cached[2]
        now = datetime.now(timezone.utc)
        since = fetch_since(now, offset, days)
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
        _prune(_INPUTS_CACHE)
        _INPUTS_CACHE[key] = (time.monotonic(), now, inputs)
    return now, inputs


async def _dashboard_for(days: int, offset: int, tunnel: Optional[str]) -> dict:
    """The whole build for one scope. Shared through the cache: never mutate."""
    from app.services.owner_dashboard import build_owner_dashboard, scope_inputs

    key = (days, offset, tunnel)
    cached = _BUILD_CACHE.get(key)
    if cached and _fresh(cached[0]):
        OWNER_DASHBOARD_HEALTH["hits"] += 1
        return cached[1]
    async with _DASH_LOCKS.setdefault(("build", *key), asyncio.Lock()):
        cached = _BUILD_CACHE.get(key)
        if cached and _fresh(cached[0]):
            OWNER_DASHBOARD_HEALTH["hits"] += 1
            return cached[1]
        started = time.monotonic()
        now, inputs = await _inputs_for(days, offset)
        data = build_owner_dashboard(
            now=now, offset_minutes=offset, days=days, **scope_inputs(inputs, tunnel)
        )
        OWNER_DASHBOARD_HEALTH["builds"] += 1
        OWNER_DASHBOARD_HEALTH["last_build_ms"] = int((time.monotonic() - started) * 1000)
        _prune(_BUILD_CACHE)
        _BUILD_CACHE[key] = (time.monotonic(), data)
    return data


def _check_days(days: int) -> None:
    from app.services.owner_dashboard import ALLOWED_DAYS

    if days not in ALLOWED_DAYS:
        raise HTTPException(status_code=422, detail="days must be 1, 7 or 30")


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
    from app.services.owner_dashboard import redact

    if user.get("role") not in OWNER_DASHBOARD_ROLES:
        raise HTTPException(status_code=403, detail="Only owner, admin and dev see this dashboard")
    _check_days(days)
    data = await _dashboard_for(days, tz_offset_minutes, None)
    out = redact(data, mask_names=False, show_cost=True, show_sources=True)
    out["scope"] = {"tunnel": "all", "team_scoped_links": False}
    return {"success": True, "data": out, "count": 1}


@router.get("/dashboard/team")
async def get_team_dashboard(
    days: int = Query(7),
    tz_offset_minutes: int = Query(0, ge=-840, le=840),
    user: dict = Depends(get_current_user),
):
    """The Dashboard of a supervisor, a project manager or QA: the same build,
    cut to what the role may see.

    Supervisor and project manager: their tunnel (all of it, like the old
    dashboard); chat links only for chats of their own teams, which are the
    ones Conversations opens for them (no team: no links, the numbers stay).
    Supervisors get clients' names masked, as in Conversations. QA: all chats.
    Nobody here sees the AI's cost or the marketing sources."""
    from app.services.owner_dashboard import redact

    role = user.get("role", "")
    if role not in TEAM_DASHBOARD_ROLES:
        raise HTTPException(status_code=403, detail="This dashboard is for supervisors, project managers and QA")
    _check_days(days)
    scope = user.get("tunnel_scope") or role
    tunnel = scope if role != "qa" and scope in ("sales", "support") else None
    team_ids: Optional[set[str]] = None
    if role == "supervisor":
        team_ids = set(await db.get_team_ids_for_supervisor(user.get("id", "")))
    elif role == "project_manager":
        team_ids = set(await db.get_team_ids_for_pm(user.get("id", "")))
    data = await _dashboard_for(days, tz_offset_minutes, tunnel)
    out = redact(
        data, mask_names=role == "supervisor", show_cost=False, show_sources=False, team_ids=team_ids
    )
    out["scope"] = {"tunnel": tunnel or "all", "team_scoped_links": team_ids is not None}
    return {"success": True, "data": out, "count": 1}
