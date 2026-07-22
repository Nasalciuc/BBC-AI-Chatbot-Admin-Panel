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
