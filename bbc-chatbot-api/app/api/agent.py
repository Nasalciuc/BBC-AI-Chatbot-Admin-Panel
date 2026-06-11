"""Agent presence — heartbeat endpoint."""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.db import supabase as db
from app.security.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

_HANDOFF_COOLDOWN_SECONDS = 120


def _recent_fallback_system_message(last_sys: dict | None) -> bool:
    """True if the last system message is a recent specialist-unavailable fallback."""
    if not last_sys or "right where we left off" not in (last_sys.get("content") or ""):
        return False
    try:
        msg_time = datetime.fromisoformat(last_sys["created_at"].replace("Z", "+00:00"))
        return (
            datetime.now(timezone.utc) - msg_time
        ).total_seconds() < _HANDOFF_COOLDOWN_SECONDS
    except Exception:
        return False


async def _cleanup_stale_conversations() -> int:
    """Revert conversations from offline agents back to AI mode.
    Called on every heartbeat — each online agent helps clean up."""
    from config.settings import settings
    from app.services.handoff import fall_back_to_ai
    stale = await db.get_stale_agent_conversations(settings.agent_timeout_seconds)
    count = 0
    for conv in stale:
        conv_id = conv["id"]
        last_sys = await db.get_last_system_message(conv_id)
        if _recent_fallback_system_message(last_sys):
            continue
        await fall_back_to_ai(conv_id)
        count += 1
    return count



async def _assign_pending_conversations(
    user_id: str,
    tunnel_scope: str,
    agent_name: str = "A specialist",
) -> int:
    """If agent is idle (0 active convs), auto-assign oldest unassigned AI conv.
    Sends system messages + SSE push to notify widget of the handoff."""
    from config.settings import settings
    count = await db.get_agent_active_count(user_id)
    if count >= settings.max_concurrent_chats:
        return 0
    tunnels = ["sales", "support"] if tunnel_scope == "all" else [tunnel_scope]
    for t in tunnels:
        conv = await db.get_oldest_unassigned_conversation(t)
        if conv:
            conv_id = conv["id"]

            conv_data = await db.get_conversation(conv_id)
            if (
                conv_data
                and conv_data.get("mode") == "human"
                and conv_data.get("assigned_agent_id") == user_id
            ):
                continue

            last_sys = await db.get_last_system_message(conv_id)
            if _recent_fallback_system_message(last_sys):
                continue

            # Anti-loop guard: skip if max auto-assigns reached or cooldown active
            from datetime import datetime, timezone
            _conv_meta = (conv_data or {}).get("metadata") or {}
            _assign_count = int(_conv_meta.get("agent_assign_count", 0))
            if _assign_count >= 3:
                logger.info(
                    f"[heartbeat] Skip conv {conv_id} — max auto-assigns reached "
                    f"({_assign_count}). Manual claim required."
                )
                continue

            _cooldown_str = _conv_meta.get("agent_cooldown_until")
            if _cooldown_str:
                try:
                    _cooldown_until = datetime.fromisoformat(_cooldown_str)
                    if datetime.now(timezone.utc) < _cooldown_until:
                        logger.info(
                            f"[heartbeat] Skip conv {conv_id} — cooldown active "
                            f"until {_cooldown_str}"
                        )
                        continue
                except (ValueError, TypeError):
                    pass

            # Use handoff service for the assignment (mode + agent_id),
            # but skip its system messages — heartbeat uses different templates.
            from app.services.handoff import perform_handoff_to_agent, _safe_system_msg
            await perform_handoff_to_agent(
                conversation_id=conv_id,
                agent_id=user_id,
                agent_name=agent_name,
                tunnel=t,
                emit_messages=False,
            )

            # Increment assignment counter (clear cooldown — now active)
            _new_count = _assign_count + 1
            _updated_meta = {**_conv_meta, "agent_assign_count": _new_count}
            _updated_meta.pop("agent_cooldown_until", None)
            await db.update_conversation(conv_id, {"metadata": _updated_meta})

            # Heartbeat-specific system message (join notification only — no welcome prompt)
            from app.realtime.manager import manager

            joined = settings.heartbeat_joined_template.format(agent_name=agent_name)
            row1 = await _safe_system_msg(conv_id, joined, cooldown_seconds=60)

            # Push to SSE stream — no-op if widget not currently connected
            if row1:
                await manager.push(conv_id, row1)

            logger.info(
                f"[heartbeat-assign] Conv {conv_id} → {user_id} "
                f"({agent_name}), SSE pushed, assign_count={_new_count}"
            )
            return 1
    return 0


@router.post("/agent/heartbeat")
async def heartbeat(user: dict = Depends(get_current_user)):
    """Agent pings every 30s to signal online presence.
    Also cleans up conversations from offline agents.
    Only operator roles (sales/support, per DB) auto-receive conversations."""
    user_id = user.get("id")
    if not user_id:
        return {"success": False, "error": "No user ID in token"}
    await db.update_user_last_seen(user_id)
    cleaned = await _cleanup_stale_conversations()
    assigned = 0
    # SECURITY: role, readiness AND tunnel_scope all from DB, never from JWT.
    # A stale token (e.g. admin still carrying an old sales token) was
    # receiving auto-assigns in production. Fail closed on any miss.
    user_db = await db.get_user_by_id(user_id)
    role_db = (user_db or {}).get("role") or ""
    is_ready = bool((user_db or {}).get("is_ready", False))
    if user_db and role_db in db._OPERATOR_ROLES and is_ready:
        agent_name = user_db.get("name") or user_db.get("email") or "A specialist"
        assigned = await _assign_pending_conversations(
            user_id,
            user_db.get("tunnel_scope") or "sales",
            agent_name,
        )
    return {
        "success": True,
        "cleaned": cleaned,
        "assigned": assigned,
        "is_ready": is_ready,
        "role": role_db,
    }


@router.get("/agent/status")
async def agent_status(user: dict = Depends(get_current_user)):
    """Get all agents with online/offline status."""
    from config.settings import settings
    agents = await db.get_all_agents_status(
        timeout_seconds=settings.agent_timeout_seconds
    )
    return {"success": True, "data": agents}


class ReadyStatusRequest(BaseModel):
    is_ready: bool


@router.post("/agent/ready")
async def set_ready_status(
    body: ReadyStatusRequest,
    user: dict = Depends(get_current_user),
):
    """Agent sets their availability (ready/not-ready)."""
    user_id = user.get("id")
    if not user_id:
        return {"success": False, "error": "No user ID"}

    role = user.get("role", "sales")
    if role in db._MANAGEMENT_ROLES:
        return {
            "success": False,
            "error": "Management roles cannot set ready status",
        }

    await db.update_user(user_id, {"is_ready": body.is_ready})
    status_str = "ready" if body.is_ready else "not_ready"
    logger.info(f"[agent-status] {user.get('email')} -> {status_str}")
    return {"success": True, "is_ready": body.is_ready}
