"""Admin API — users CRUD."""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.db import supabase as db
from app.models.admin import UserUpdate
from app.security.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_ROLES = {"owner", "admin", "dev", "sales", "support", "supervisor", "qa", "project_manager"}
VALID_TUNNELS = {"sales", "support", "all"}
PRIVILEGED = {"owner", "admin", "dev"}
# Roles that can read user list (extends PRIVILEGED for reassign dropdown).
# Write operations (update, invite, delete) remain restricted to PRIVILEGED only.
# project_manager needs to see its teams' operators (still cannot write users).
CAN_LIST_USERS = PRIVILEGED | {"supervisor", "project_manager"}


@router.get("/admin/users")
async def list_users(
    role:   Optional[str] = Query(None, pattern="^(owner|admin|sales|support|supervisor)$"),
    search: Optional[str] = Query(None, max_length=100),
    limit:  int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    if user.get("role") not in CAN_LIST_USERS:
        raise HTTPException(status_code=403, detail="Only owner/admin can list users")
    try:
        # Phase 2: supervisor/PM see only members of their own team(s), plus
        # themselves. Fail closed — no team ⇒ only their own row.
        actor_role = user.get("role", "")
        actor_id = user.get("id", "")
        team_ids: Optional[list[str]] = None
        include_self_id: Optional[str] = None
        if actor_role == "supervisor":
            team_ids = await db.get_team_ids_for_supervisor(actor_id)
            include_self_id = actor_id
        elif actor_role == "project_manager":
            team_ids = await db.get_team_ids_for_pm(actor_id)
            include_self_id = actor_id
        if team_ids is not None and not team_ids:
            # scoped but owns no team → only self visible
            team_ids = ["00000000-0000-0000-0000-000000000000"]

        rows, total = await db.get_users(
            role=role, search=search, limit=limit, offset=offset,
            team_ids=team_ids, include_self_id=include_self_id,
        )
        return {"success": True, "data": rows, "count": total}
    except Exception as e:
        logger.error(f"list_users error: {e}")
        raise HTTPException(status_code=500, detail="Failed to load users")


@router.patch("/admin/users/{user_id}")
async def update_user(user_id: str, body: UserUpdate, user: dict = Depends(get_current_user)):
    actor_role = user.get("role", "sales")
    if actor_role not in PRIVILEGED:
        raise HTTPException(status_code=403, detail="Only owner/admin can modify access rights")

    payload = body.model_dump(exclude_none=True)

    # team_id is intentionally nullable: `null` = move user to the unassigned
    # pool. exclude_none would drop that, so re-add it whenever the client
    # explicitly sent the field (even as null).
    team_id_set = "team_id" in body.model_fields_set
    if team_id_set:
        payload["team_id"] = body.team_id

    if not payload:
        raise HTTPException(400, "No valid fields to update")
    if "role" in payload and payload["role"] not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {VALID_ROLES}")
    if "tunnel_scope" in payload and payload["tunnel_scope"] not in VALID_TUNNELS:
        raise HTTPException(400, f"Invalid tunnel_scope. Must be one of: {VALID_TUNNELS}")

    # Validate team assignment: null clears it; a value must reference an
    # existing, active team.
    if team_id_set and body.team_id is not None:
        team = await db.get_team(body.team_id)
        if not team:
            raise HTTPException(400, "Team not found")
        if not team.get("is_active", False):
            raise HTTPException(400, "Cannot assign user to an inactive team")

    existing = await db.get_user_by_id(user_id)
    if not existing:
        raise HTTPException(404, "User not found")

    target_role = existing.get("role", "sales")

    # Admin guardrails: cannot touch owner accounts or escalate to owner.
    if actor_role == "admin":
        if target_role == "owner":
            raise HTTPException(403, "Admin cannot modify owner accounts")
        if payload.get("role") == "owner":
            raise HTTPException(403, "Admin cannot assign owner role")

    result = await db.update_user(user_id, payload)
    if not result:
        raise HTTPException(404, "User not found")

    # Access audit: log only access-right changes, not profile edits.
    changed_fields: list[str] = []
    for f in ("role", "tunnel_scope", "is_active"):
        if f in payload and payload.get(f) != existing.get(f):
            changed_fields.append(f)

    if changed_fields:
        await db.create_user_access_audit({
            "target_user_id": user_id,
            "changed_by_user_id": user.get("id"),
            "action": "access_update",
            "old_role": existing.get("role"),
            "new_role": result.get("role"),
            "old_tunnel_scope": existing.get("tunnel_scope"),
            "new_tunnel_scope": result.get("tunnel_scope"),
            "old_is_active": existing.get("is_active"),
            "new_is_active": result.get("is_active"),
            "changed_fields": changed_fields,
        })

    return {"success": True, "data": result, "count": 1}


@router.get("/admin/users/{user_id}/access-history")
async def get_user_access_history(
    user_id: str,
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    if user.get("role") not in PRIVILEGED:
        raise HTTPException(status_code=403, detail="Only owner/admin can view access history")
    existing = await db.get_user_by_id(user_id)
    if not existing:
        raise HTTPException(404, "User not found")
    rows = await db.get_user_access_audit(user_id, limit=limit)
    return {"success": True, "data": rows, "count": len(rows)}
