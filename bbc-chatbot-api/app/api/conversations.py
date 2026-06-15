"""Admin API — conversations CRUD."""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.db import supabase as db
from app.models.admin import ConversationUpdate
from app.security.auth import get_current_user
from app.security.input_sanitizer import sanitize_message
from app.services.conversation_service import add_message
from app.realtime.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter()


def _enforce_tunnel(user: dict, tunnel: Optional[str]) -> Optional[str]:
    """Force tunnel filter for sales/support roles."""
    role = user.get("role", "sales")
    if role in ("owner", "admin", "dev", "supervisor", "qa"):
        return tunnel  # privileged users can filter freely
    scope = user.get("tunnel_scope", role)
    if tunnel and tunnel != scope:
        raise HTTPException(status_code=403, detail="Access denied to this tunnel")
    return scope


@router.get("/conversations")
async def list_conversations(
    tunnel: Optional[str] = Query(None, pattern="^(sales|support)$"),
    status: Optional[str] = Query(None, pattern="^(active|pending|closed)$"),
    assigned_to: Optional[str] = Query(None, pattern="^(me|none|all)$"),
    search: Optional[str] = Query(None, max_length=100),
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    try:
        tunnel = _enforce_tunnel(user, tunnel)

        # Resolve assigned_to into an agent_id filter
        agent_id_filter: Optional[str] = None
        agent_id_is_null: bool = False
        if assigned_to == "me":
            agent_id_filter = user.get("id")
        elif assigned_to == "none":
            agent_id_is_null = True
        # "all" or None → no agent filter (owner/admin sees everything)

        # my_active includes reserved conversations (status=needs_agent,
        # assigned to me): the silent reservation is silent for the CLIENT,
        # not the operator — the agent must SEE it to answer within the
        # deadline. Status flips to active at their first message (engagement).
        status_in = None
        list_status = status
        if assigned_to == "me" and status == "active":
            status_in = ["active", "needs_agent"]
            list_status = None

        rows, total = await db.get_conversations(
            tunnel=tunnel, status=list_status, status_in=status_in, search=search,
            agent_id=agent_id_filter, agent_id_is_null=agent_id_is_null,
            limit=limit, offset=offset,
        )
        return {"success": True, "data": rows, "count": total}
    except HTTPException:
        # DO NOT swallow HTTPException — _enforce_tunnel uses it to signal 403.
        raise
    except Exception as e:
        logger.error(f"list_conversations unexpected error: {e}", exc_info=True)
        return {"success": False, "data": [], "count": 0, "error": str(e)}


@router.get("/conversations/counts")
async def get_conversation_counts(
    tunnel: Optional[str] = Query(None, pattern="^(sales|support)$"),
    user: dict = Depends(get_current_user),
):
    """Lightweight counts for queue tabs. Returns 3 numbers in 1 request."""
    tunnel = _enforce_tunnel(user, tunnel)
    agent_id = user.get("id", "")
    counts = await db.get_conversation_counts(
        agent_id=agent_id,
        tunnel=tunnel,
    )
    return {"success": True, "data": counts}


@router.get("/conversations/{conversation_id}/typing")
async def get_typing_status(
    conversation_id: str,
    user: dict = Depends(get_current_user),
):
    """Agent polls every 1s to see client's live typing text.
    Returns empty state if client is not typing or Redis key expired."""
    from app.realtime.typing_indicator import typing_manager
    state = await typing_manager.get_typing(conversation_id)
    return {
        "success": True,
        "data": state or {"is_typing": False, "text": ""},
    }


@router.get("/conversations/{conversation_id}/presence")
async def get_conversation_presence(
    conversation_id: str,
    user: dict = Depends(get_current_user),
):
    """Lightweight client presence metadata for real-time status in admin UI."""
    conv = await db.get_conversation_simple(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _enforce_tunnel(user, conv.get("tunnel"))

    metadata = dict(conv.get("metadata") or {})
    return {
        "success": True,
        "data": {
            "widget_open": metadata.get("widget_open", False),
            "widget_presence": metadata.get("widget_presence", "minimized"),
            "widget_last_close_reason": metadata.get("widget_last_close_reason"),
            "widget_last_event_at": metadata.get("widget_last_event_at"),
            "updated_at": conv.get("updated_at"),
        },
    }


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    after: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    """Get messages, optionally only those after a timestamp (incremental polling)."""
    # Supervisors can manage queues but cannot access message content.
    if user.get("role") == "supervisor":
        raise HTTPException(
            status_code=403,
            detail=(
                "Supervisors cannot access message content. "
                "Use /conversations for metadata only."
            ),
        )
    msgs = await db.get_messages_after(conversation_id, after)
    return {"success": True, "data": msgs}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        conv = await db.get_conversation(conversation_id)
        if not conv:
            return {"success": False, "data": None, "count": 0, "error": "Not found"}
        _enforce_tunnel(user, conv.get("tunnel"))

        # Supervisors can see metadata only, never message content.
        if user.get("role") == "supervisor":
            conv = dict(conv)
            conv["messages"] = []

        return {"success": True, "data": conv, "count": 1}
    except HTTPException:
        # DO NOT swallow HTTPException — _enforce_tunnel uses it to signal 403.
        # Letting it propagate returns proper HTTP status codes to the client.
        # Previously this was caught by `except Exception` below and returned
        # HTTP 200 with {data: null}, which the frontend rendered as
        # "Conversation not found" — hiding legitimate auth errors. See Bug 5
        # forensic (17.04.2026).
        raise
    except Exception as e:
        # Any OTHER exception (Supabase errors, unexpected bugs) is still
        # converted to a JSON error envelope for backwards compatibility,
        # but logged so we can measure frequency. The frontend now checks
        # isError and differentiates this from a real 404.
        logger.error(
            f"get_conversation({conversation_id}) unexpected error: {e}",
            exc_info=True,
        )
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


class AgentMessageBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


@router.post("/conversations/{conversation_id}/messages")
async def send_agent_message(
    conversation_id: str,
    body: AgentMessageBody,
    user: dict = Depends(get_current_user),
):
    """Agent sends a message in a conversation. Auto-sets mode to 'human'."""
    # SECURITY: role from DB, not JWT. Only hands-on roles may speak as agent.
    _sender_db = await db.get_user_by_id(user.get("id"))
    _role_db = (_sender_db or {}).get("role") or ""
    if _role_db not in db._HANDS_ON_ROLES:
        raise HTTPException(status_code=403, detail="Your role cannot send agent messages")

    # 1. Verify conversation exists and agent has tunnel access
    conv = await db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _enforce_tunnel(user, conv.get("tunnel"))

    _conv_meta = dict(conv.get("metadata") or {})
    _user_id = user.get("id")
    _already_mine = (
        conv.get("mode") == "human"
        and conv.get("assigned_agent_id") == _user_id
    )

    # F1 FIX: perform_handoff ONLY when this is a NEW assignment (admin
    # jumping into an AI conv, or takeover). Re-calling it per message
    # refreshed agent_assigned_at and made the response deadline evict
    # ENGAGED agents waiting on slow visitors.
    if not _already_mine:
        from app.services.handoff import perform_handoff_to_agent
        await perform_handoff_to_agent(
            conversation_id=conversation_id,
            agent_id=_user_id,
            tunnel=conv.get("tunnel", "sales"),
            emit_messages=False,
            handoff_reason="manual_claim",
        )
        conv = await db.get_conversation(conversation_id) or conv
        _conv_meta = dict(conv.get("metadata") or {})

    # Announce-on-engage: first real agent message in a silent reservation.
    _was_announce_pending = (
        bool(_conv_meta.get("announce_pending"))
        and conv.get("assigned_agent_id") == _user_id
    )
    if _was_announce_pending:
        from config.settings import settings as _s
        _agent_name = (_sender_db or {}).get("name") or user.get("name") or "A specialist"
        _announce_row = await add_message(
            conversation_id,
            "system",
            _s.heartbeat_joined_template.format(agent_name=_agent_name),
        )
        if _announce_row:
            await manager.push(conversation_id, _announce_row)

    clean_content = sanitize_message(body.content)
    msg = await add_message(conversation_id=conversation_id, role="agent", content=clean_content)

    _at = (conv.get("metadata") or {}).get("agent_assigned_at")
    if _at and await db.count_agent_messages_since(conversation_id, _at) == 1:
        from app.services.presence import log_activity
        from app.pipeline.orchestrator import _fire_and_forget
        _rs = None
        try:
            from datetime import datetime, timezone
            _assigned = datetime.fromisoformat(_at.replace("Z", "+00:00"))
            _rs = int((datetime.now(timezone.utc) - _assigned).total_seconds())
        except (ValueError, TypeError):
            pass
        _fire_and_forget(
            log_activity(
                db, _user_id, conversation_id, "first_response",
                response_seconds=_rs,
            )
        )

    # ENGAGEMENT — one-shot write (status + metadata together; metadata
    # update is REPLACE semantics, never write it twice in a row):
    #   - engaged_agent_id: the agent who speaks OWNS the conversation
    #   - announce_pending cleared (consumed above)
    #   - needs_agent → active: the human the client waited for is HERE
    _engage_update: dict = {}
    _meta_changed = False
    if _conv_meta.get("engaged_agent_id") != _user_id:
        _conv_meta["engaged_agent_id"] = _user_id
        _meta_changed = True
    if _conv_meta.pop("announce_pending", None) is not None:
        _meta_changed = True
    if _meta_changed:
        _engage_update["metadata"] = _conv_meta
    if conv.get("status") == "needs_agent":
        _engage_update["status"] = "active"
    if _engage_update:
        await db.update_conversation(conversation_id, _engage_update)

    if msg:
        await manager.push(conversation_id, msg)
    return {"success": True, "data": msg}


@router.post("/conversations/{conversation_id}/claim")
async def claim_conversation(
    conversation_id: str,
    user: dict = Depends(get_current_user),
):
    """Operator claims an unassigned conversation from the queue."""
    conv = await db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _enforce_tunnel(user, conv.get("tunnel"))

    # SECURITY: role from DB, not JWT. Claim ⟺ can write (supervisor/qa = read-only).
    _claimer_db = await db.get_user_by_id(user.get("id"))
    _claimer_role = (_claimer_db or {}).get("role") or ""
    if _claimer_role not in db._HANDS_ON_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Your role cannot claim conversations",
        )

    # Check if already assigned to someone else
    current_agent = conv.get("assigned_agent_id")
    if current_agent and current_agent != user.get("id"):
        raise HTTPException(status_code=409, detail="Conversation already taken by another agent")

    from app.services.handoff import perform_handoff_to_agent
    # Reset loop guards — agent chose this conv actively.
    # Single metadata write via perform_handoff (reads + merges + writes).
    await perform_handoff_to_agent(
        conversation_id=conversation_id,
        agent_id=user.get("id"),
        tunnel=conv.get("tunnel", "sales"),
        emit_messages=False,
        handoff_reason="manual_claim",
        _pop_meta_keys=["agent_assign_count", "agent_cooldown_until"],
    )

    return {"success": True, "data": {"conversation_id": conversation_id, "assigned_to": user.get("id")}}


@router.post("/conversations/{conversation_id}/close")
async def close_conversation(
    conversation_id: str,
    user: dict = Depends(get_current_user),
):
    """Close a conversation. Sets status=closed and closed_at."""
    if user.get("role") == "qa":
        raise HTTPException(403, "QA role cannot close conversations")
    conv = await db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _enforce_tunnel(user, conv.get("tunnel"))

    from datetime import datetime, timezone
    await db.update_conversation(conversation_id, {
        "status": "closed",
        "closed_at": datetime.now(timezone.utc).isoformat(),
    })
    from app.services.presence import log_activity
    from app.pipeline.orchestrator import _fire_and_forget
    _fire_and_forget(
        log_activity(db, user.get("id"), conversation_id, "closed")
    )

    # Auto-assign: freed operator picks up oldest unassigned AI conv.
    # Management roles (owner/admin/dev) do NOT auto-receive conversations.
    next_conv_id = None
    agent_id = user.get("id")
    role = user.get("role", "")
    if role not in db._MANAGEMENT_ROLES:
        tunnel_scope = user.get("tunnel_scope", "sales")
        tunnels = ["sales", "support"] if tunnel_scope == "all" else [tunnel_scope]

        from app.services.handoff import perform_handoff_to_agent
        for t in tunnels:
            candidates = await db.get_oldest_unassigned_conversations(t)
            for next_conv in candidates:
                _nc_meta = (next_conv.get("metadata") or {})
                if _nc_meta.get("widget_presence") == "left":
                    continue
                _sticky = _nc_meta.get("engaged_agent_id")
                if _sticky and _sticky != agent_id:
                    continue
                agent_name = user.get("name") or user.get("email", "A specialist")
                await perform_handoff_to_agent(
                    conversation_id=next_conv["id"],
                    agent_id=agent_id,
                    agent_name=agent_name,
                    tunnel=t,
                    emit_messages=False,
                    handoff_reason="auto_assign",
                )
                next_conv_id = next_conv["id"]
                logger.info(f"[auto-assign] Conv {next_conv['id']} → {agent_id} (on close)")
                break  # 1:1 rule — assign only 1
            if next_conv_id:
                break

    return {
        "success": True,
        "data": {
            "conversation_id": conversation_id,
            "status": "closed",
            "next_conversation_id": next_conv_id,
        },
    }


class ReassignRequest(BaseModel):
    agent_id: str = Field(..., min_length=36, max_length=36)


@router.post("/conversations/{conversation_id}/reassign")
async def reassign_conversation(
    conversation_id: str,
    body: ReassignRequest,
    user: dict = Depends(get_current_user),
):
    """Reassign conversation to another operator."""
    allowed_roles = {"supervisor", "admin", "owner", "dev"}
    if user.get("role") not in allowed_roles:
        raise HTTPException(403, "Only supervisors and admins can reassign conversations")

    conv = await db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")

    if conv.get("status") == "closed":
        raise HTTPException(400, "Cannot reassign a closed conversation")

    target_agent = await db.get_user_by_id(body.agent_id)
    if not target_agent or not target_agent.get("is_active"):
        raise HTTPException(404, "Target agent not found or inactive")

    if target_agent.get("role") not in ("sales", "support"):
        raise HTTPException(400, "Target must be an active sales/support operator")

    from datetime import datetime, timezone
    from app.services.handoff import perform_handoff_to_agent

    await perform_handoff_to_agent(
        conversation_id=conversation_id,
        agent_id=body.agent_id,
        tunnel=conv.get("tunnel", "sales"),
        emit_messages=False,
        handoff_reason="manual_claim",
        _extra_meta={
            "reassigned_by": user.get("id"),
            "reassigned_at": datetime.now(timezone.utc).isoformat(),
            "engaged_agent_id": body.agent_id,
        },
    )

    agent_name = target_agent.get("name") or target_agent.get("email", "a specialist")
    requester_name = user.get("name") or user.get("email", "supervisor")
    await add_message(
        conversation_id,
        "system",
        f"Conversation reassigned to {agent_name} by {requester_name}.",
    )

    logger.info(
        f"[reassign] Conv {conversation_id} -> {body.agent_id} "
        f"by {user.get('email')} (role={user.get('role')})"
    )

    return {"success": True, "assigned_to": body.agent_id}
