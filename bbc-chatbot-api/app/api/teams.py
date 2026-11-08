"""Admin API — Teams CRUD (Phase 1: model + management only, no team filtering)."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import supabase as db
from app.models.teams import TeamCreate, TeamUpdate
from app.security.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

PRIVILEGED = {"owner", "admin", "dev"}
# project_manager can also create + manage teams, but only the ones they own
# (teams.pm_id == their id). owner/admin/dev manage every team.
CAN_CREATE_TEAMS = PRIVILEGED | {"project_manager"}


def _can_manage_team(user: dict, team: dict) -> bool:
    """True if this user may edit/delete this specific team."""
    role = user.get("role", "")
    if role in PRIVILEGED:
        return True
    if role == "project_manager":
        return bool(team) and team.get("pm_id") == user.get("id")
    return False


async def _validate_supervisor(supervisor_id: Optional[str]) -> None:
    if not supervisor_id:
        return
    u = await db.get_user_by_id(supervisor_id)
    if not u or u.get("role") != "supervisor":
        raise HTTPException(400, "supervisor_id must reference a user with role 'supervisor'")


async def _validate_pm(pm_id: Optional[str]) -> None:
    if not pm_id:
        return
    u = await db.get_user_by_id(pm_id)
    if not u or u.get("role") != "project_manager":
        raise HTTPException(400, "pm_id must reference a user with role 'project_manager'")


async def _assert_supervisor_free(supervisor_id: Optional[str], exclude_team_id: Optional[str] = None) -> None:
    """One supervisor ⇒ one active team. Raise 409 if already supervising another."""
    if not supervisor_id:
        return
    existing = await db.get_teams(is_active=True, supervisor_id=supervisor_id)
    for t in existing:
        if t.get("id") != exclude_team_id:
            raise HTTPException(
                409,
                f"This supervisor already owns team '{t.get('name')}'",
            )


@router.get("/admin/teams")
async def list_teams(
    is_active: Optional[bool] = Query(None),
    user: dict = Depends(get_current_user),
):
    role = user.get("role", "sales")
    if role in PRIVILEGED:
        teams = await db.get_teams(is_active=is_active)
    elif role == "project_manager":
        teams = await db.get_teams(is_active=is_active, pm_id=user.get("id"))
    elif role == "supervisor":
        teams = await db.get_teams(is_active=is_active, supervisor_id=user.get("id"))
    else:
        raise HTTPException(403, "Not allowed to view teams")
    return {"success": True, "data": teams, "count": len(teams)}


@router.post("/admin/teams")
async def create_team(body: TeamCreate, user: dict = Depends(get_current_user)):
    role = user.get("role")
    if role not in CAN_CREATE_TEAMS:
        raise HTTPException(403, "Only owner/admin or a project manager can create teams")

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Team name is required")

    # A project_manager always owns the teams they create — they cannot assign
    # a different PM (owner/admin can). This also makes the new team visible to
    # the creator, since PMs see only teams where pm_id == their id.
    if role == "project_manager":
        body.pm_id = user.get("id")

    await _validate_supervisor(body.supervisor_id)
    await _validate_pm(body.pm_id)
    await _assert_supervisor_free(body.supervisor_id)

    payload = {
        "name": name,
        "shift_name": body.shift_name,
        "shift_start": body.shift_start.isoformat() if body.shift_start else None,
        "shift_end": body.shift_end.isoformat() if body.shift_end else None,
        "supervisor_id": body.supervisor_id,
        "pm_id": body.pm_id,
        "created_by": user.get("id"),
    }
    team = await db.create_team(payload)
    if not team:
        raise HTTPException(500, "Failed to create team")
    return {"success": True, "data": team, "count": 1}


@router.patch("/admin/teams/{team_id}")
async def update_team(team_id: str, body: TeamUpdate, user: dict = Depends(get_current_user)):
    existing = await db.get_team(team_id)
    if not existing:
        raise HTTPException(404, "Team not found")
    if not _can_manage_team(user, existing):
        raise HTTPException(403, "You can only modify your own teams")

    fields = body.model_dump(exclude_unset=True)
    # A project_manager cannot reassign a team to a different PM (would lock
    # themselves out). owner/admin can. Silently drop the field for PMs.
    if user.get("role") == "project_manager":
        fields.pop("pm_id", None)
    if "name" in fields:
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(400, "Team name cannot be empty")
        fields["name"] = name
    if "supervisor_id" in fields:
        await _validate_supervisor(body.supervisor_id)
        await _assert_supervisor_free(body.supervisor_id, exclude_team_id=team_id)
    if "pm_id" in fields:
        await _validate_pm(body.pm_id)
    if "shift_start" in fields:
        fields["shift_start"] = body.shift_start.isoformat() if body.shift_start else None
    if "shift_end" in fields:
        fields["shift_end"] = body.shift_end.isoformat() if body.shift_end else None

    if not fields:
        raise HTTPException(400, "No valid fields to update")

    team = await db.update_team(team_id, fields)
    if not team:
        raise HTTPException(500, "Failed to update team")
    return {"success": True, "data": team, "count": 1}


@router.delete("/admin/teams/{team_id}")
async def delete_team(team_id: str, user: dict = Depends(get_current_user)):
    existing = await db.get_team(team_id)
    if not existing:
        raise HTTPException(404, "Team not found")
    if not _can_manage_team(user, existing):
        raise HTTPException(403, "You can only delete your own teams")

    # Soft delete. Refuse if members still reference it — admin must reassign
    # them first (keeps membership consistent; reversible by reactivating).
    members = await db.count_team_members(team_id)
    if members > 0:
        raise HTTPException(
            409,
            f"Team has {members} member(s). Reassign them before deleting.",
        )

    team = await db.update_team(team_id, {"is_active": False})
    if not team:
        raise HTTPException(500, "Failed to delete team")
    return {"success": True, "data": team, "count": 1}


# ── Team · Now (owner, admin, dev, supervisor, project manager) ──
# The floor as it is right now. A supervisor or project manager sees the
# people of their own teams and the chats those people have, the shared queue
# of their tunnel (the line their people take from), and how many chats the
# AI has on its own there, as a number. Supervisors get clients' names masked;
# nobody but owner, admin and dev gets message previews, as in Conversations.
# No team means the "no team yet" state, never everything. One read every
# few seconds, shared.

TEAM_VIEW_ROLES = PRIVILEGED | {"supervisor", "project_manager"}
TEAM_NOW_TTL_SECONDS = 10
_TEAM_NOW_CACHE: dict[str, tuple[float, dict]] = {}
TEAM_NOW_HEALTH: dict = {"reads": 0, "hits": 0, "read_failures": 0}


async def _team_scope(user: dict) -> tuple[Optional[set[str]], Optional[str]]:
    """(team ids or None for everything, tunnel or None for both)."""
    role = user.get("role", "")
    if role in PRIVILEGED:
        return None, None
    ids = (
        await db.get_team_ids_for_supervisor(user.get("id", ""))
        if role == "supervisor"
        else await db.get_team_ids_for_pm(user.get("id", ""))
    )
    scope = user.get("tunnel_scope") or role
    return set(ids), scope if scope in ("sales", "support") else None


async def _team_now_inputs(day_start_iso: str) -> dict:
    import time

    cached = _TEAM_NOW_CACHE.get(day_start_iso)
    if cached and time.monotonic() - cached[0] < TEAM_NOW_TTL_SECONDS:
        TEAM_NOW_HEALTH["hits"] += 1
        return cached[1]
    try:
        inputs = await db.get_team_now_inputs(day_start_iso)
    except Exception as e:
        TEAM_NOW_HEALTH["read_failures"] += 1
        logger.error(f"[team_now] read failed: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="Couldn't load the floor") from e
    TEAM_NOW_HEALTH["reads"] += 1
    for k in [k for k, v in _TEAM_NOW_CACHE.items() if time.monotonic() - v[0] >= TEAM_NOW_TTL_SECONDS]:
        _TEAM_NOW_CACHE.pop(k, None)
    _TEAM_NOW_CACHE[day_start_iso] = (time.monotonic(), inputs)
    return inputs


@router.get("/admin/team/now")
async def team_now(
    tz_offset_minutes: int = Query(0, ge=-840, le=840),
    user: dict = Depends(get_current_user),
):
    from datetime import datetime, timezone
    from app.services.agent_day import local_day_start
    from app.services.team_now import build_team_now
    from config.settings import settings

    role = user.get("role", "")
    if role not in TEAM_VIEW_ROLES:
        raise HTTPException(403, "Team is for the owner, admins, supervisors and project managers")
    team_ids, tunnel = await _team_scope(user)
    now = datetime.now(timezone.utc)
    if team_ids is not None and not team_ids:
        return {"success": True, "data": {"no_team": True, "now": now.isoformat()}, "count": 0}
    inputs = await _team_now_inputs(local_day_start(now, tz_offset_minutes).isoformat())
    previews: dict = {}
    if role in PRIVILEGED:
        ids = [c["id"] for c in inputs["conversations"] if c.get("id")]
        try:
            previews = await db.get_last_message_previews(ids) if ids else {}
        except Exception as e:
            # The floor without previews is still the floor.
            logger.warning(f"[team_now] previews unavailable ({e}) — serving the floor without them")
    data = build_team_now(
        now=now, offset_minutes=tz_offset_minutes, previews=previews, team_ids=team_ids, tunnel=tunnel,
        masked=role == "supervisor", queue_window_s=settings.queue_presence_window_seconds,
        online_s=settings.agent_timeout_seconds, **inputs,
    )
    data["no_team"] = False
    data["can_give"] = role in ("owner", "admin", "dev", "supervisor")
    return {"success": True, "data": data, "count": len(data["people"])}


@router.get("/admin/team/people/{user_id}/today")
async def person_today(
    user_id: str,
    tz_offset_minutes: int = Query(0, ge=-840, le=840),
    user: dict = Depends(get_current_user),
):
    from datetime import datetime, timezone
    from app.services.agent_day import local_day_start
    from app.services.team_now import build_person_day
    from config.settings import settings

    role = user.get("role", "")
    if role not in TEAM_VIEW_ROLES:
        raise HTTPException(403, "Team is for the owner, admins, supervisors and project managers")
    now = datetime.now(timezone.utc)
    try:
        inputs = await db.get_person_day_inputs(user_id, local_day_start(now, tz_offset_minutes).isoformat())
    except Exception as e:
        TEAM_NOW_HEALTH["read_failures"] += 1
        logger.error(f"[team_now] person {user_id} read failed: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="Couldn't load this person's day") from e
    if not inputs:
        raise HTTPException(404, "User not found")
    team_ids, _ = await _team_scope(user)
    if team_ids is not None and inputs["user"].get("team_id") not in team_ids:
        raise HTTPException(403, "You can only see the people of your own teams")
    data = build_person_day(
        now=now, offset_minutes=tz_offset_minutes, masked=role == "supervisor",
        queue_window_s=settings.queue_presence_window_seconds, online_s=settings.agent_timeout_seconds,
        **inputs,
    )
    return {"success": True, "data": data, "count": 1}
