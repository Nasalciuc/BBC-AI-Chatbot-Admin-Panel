"""Agent presence — heartbeat endpoint."""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.db import supabase as db
from app.pipeline.orchestrator import _fire_and_forget
from app.security.auth import get_current_user
from config.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_HANDOFF_COOLDOWN_SECONDS = 120


class HeartbeatBody(BaseModel):
    viewing_conversation_id: str | None = None


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


async def _enforce_response_deadline(
    viewing_conversation_id: str | None = None,
    viewing_user_id: str | None = None,
) -> int:
    """Fall back conversations where the assigned agent never sent a message within
    agent_silent_timeout_seconds of assignment (checked via agent_assigned_at metadata).

    Covers agents who ARE online but simply never engaged — the stale-heartbeat pass
    only catches agents who went offline. 73% zero-response rate (30d audit Jun 2026).

    Returns number of conversations fallen back."""
    from config.settings import settings
    from app.services.handoff import fall_back_to_ai

    convs = await db.get_active_human_conversations()
    count = 0
    now = datetime.now(timezone.utc)
    deadline_first = timedelta(seconds=settings.agent_first_response_timeout_seconds)
    deadline_engaged = timedelta(seconds=settings.agent_silent_timeout_seconds)
    for conv in convs:
        meta = conv.get("metadata") or {}
        assigned_at_raw = meta.get("agent_assigned_at")
        if not assigned_at_raw:
            continue  # pre-PR3 assignment: stale cleanup covers it
        try:
            assigned_at = datetime.fromisoformat(
                assigned_at_raw.replace("Z", "+00:00")
                if isinstance(assigned_at_raw, str) else assigned_at_raw
            )
        except (ValueError, TypeError):
            continue
        _has_responded = await db.has_agent_message_since(conv["id"], assigned_at_raw)
        _timeout = deadline_engaged if _has_responded else deadline_first

        # Extend timeout to 120s if this operator is VIEWING this conversation
        if (
            not _has_responded
            and viewing_conversation_id
            and conv["id"] == viewing_conversation_id
            and conv.get("assigned_agent_id") == viewing_user_id
        ):
            _timeout = timedelta(seconds=120)

        if now - assigned_at < _timeout:
            continue
        if _has_responded:
            continue  # engaged agent → stale cleanup handles offline/silence
        await fall_back_to_ai(conv["id"])
        count += 1
    return count


async def _cleanup_stale_conversations(
    viewing_conversation_id: str | None = None,
    viewing_user_id: str | None = None,
) -> int:
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

    # Second pass: agents who are ONLINE but never engaged within the deadline.
    # (Stale pass only catches offline agents; this catches silent-but-online ones.)
    count += await _enforce_response_deadline(
        viewing_conversation_id=viewing_conversation_id,
        viewing_user_id=viewing_user_id,
    )
    return count


async def _assign_pending_conversations(
    user_id: str,
    tunnel_scope: str,
    agent_name: str = "A specialist",
) -> int:
    """If agent is idle (0 active convs), auto-assign oldest unassigned AI conv.
    Silent reservation: visitor sees nothing until the agent actually speaks."""
    from config.settings import settings
    count = await db.get_agent_active_count(user_id)
    if count >= settings.max_concurrent_chats:
        return 0
    tunnels = ["sales", "support"] if tunnel_scope == "all" else [tunnel_scope]
    for t in tunnels:
        candidates = await db.get_oldest_unassigned_conversations(t)
        for conv in candidates:
            conv_id = conv["id"]

            conv_data = await db.get_conversation(conv_id)
            if (
                conv_data
                and conv_data.get("mode") == "human"
                and conv_data.get("assigned_agent_id") == user_id
            ):
                continue

            _conv_meta = (conv_data or {}).get("metadata") or {}

            # 1.2 — Presence predicate: skip if visitor left the page.
            # Candidate list (limit 5) prevents a dead conv from blocking the queue.
            if _conv_meta.get("widget_presence") == "left":
                continue

            # 1.4 — Engaged ownership: return conv only to its engaged agent.
            _sticky = _conv_meta.get("engaged_agent_id")
            if _sticky and _sticky != user_id:
                continue

            # Anti-loop guard: skip if max auto-assigns reached or cooldown active
            from datetime import datetime, timezone
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

            # 1.3 — Silent reservation: perform_handoff sets announce_pending=True.
            # "X has joined" fires only when the agent sends their first real message.
            from app.services.handoff import perform_handoff_to_agent
            _new_count = _assign_count + 1
            await perform_handoff_to_agent(
                conversation_id=conv_id,
                agent_id=user_id,
                agent_name=agent_name,
                tunnel=t,
                emit_messages=False,
                handoff_reason="auto_assign",
                _extra_meta={"agent_assign_count": _new_count},
            )

            logger.info(
                f"[heartbeat-assign] Conv {conv_id} → {user_id} "
                f"({agent_name}), silent reservation, assign_count={_new_count}"
            )
            return 1
    return 0


@router.post("/agent/heartbeat")
async def heartbeat(body: HeartbeatBody = HeartbeatBody(), user: dict = Depends(get_current_user)):
    """Agent pings every 30s to signal online presence.
    Also cleans up conversations from offline agents.
    Only operator roles (sales/support, per DB) auto-receive conversations."""
    user_id = user.get("id")
    if not user_id:
        return {"success": False, "error": "No user ID in token"}
    await db.update_user_last_seen(user_id)
    from app.services.presence import record_presence_tick
    _fire_and_forget(record_presence_tick(user_id, db))
    cleaned = await _cleanup_stale_conversations(
        viewing_conversation_id=body.viewing_conversation_id,
        viewing_user_id=user_id,
    )
    assigned = 0
    active_assigned = 0
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
        active_assigned = await db.get_agent_active_count(user_id)
    return {
        "success": True,
        "cleaned": cleaned,
        "assigned": assigned,
        "active_assigned": active_assigned,
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
    from app.services.presence import log_ready_change
    _fire_and_forget(log_ready_change(db, user_id, body.is_ready))
    status_str = "ready" if body.is_ready else "not_ready"
    logger.info(f"[agent-status] {user.get('email')} -> {status_str}")
    return {"success": True, "is_ready": body.is_ready}


@router.get("/agents/ops-status")
async def agents_ops_status(user: dict = Depends(get_current_user)):
    if user.get("role", "") not in ("owner", "admin", "dev", "supervisor"):
        raise HTTPException(403, "Management access required")
    data = await db.get_agent_ops_status(settings.agent_timeout_seconds)
    return {"success": True, "data": data}


@router.get("/agents/{agent_id}/history")
async def agent_history(
    agent_id: str,
    days: int = 7,
    user: dict = Depends(get_current_user),
):
    role = user.get("role", "")
    uid = user.get("id", "")
    if role in ("sales", "support") and uid != agent_id:
        raise HTTPException(403, "Can only view own history")
    if role not in ("owner", "admin", "dev", "supervisor", "sales", "support"):
        raise HTTPException(403)
    data = await db.get_agent_history(agent_id, min(days, 30))
    return {"success": True, "data": data}
