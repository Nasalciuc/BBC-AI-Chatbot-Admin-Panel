"""Admin API — conversations CRUD."""
import logging
import re as _re
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

# Per-instance claim counters, same shape as HANDOFF_HEALTH (handoff.py) and
# GENERATION_HEALTH (generator.py). Single-instance deploy today (ADR-5); a
# scale-out would shard these, which is why /health labels them per-instance.
CLAIM_HEALTH: dict = {"won": 0, "lost": 0, "races_detected": 0, "last_at": None}

router = APIRouter()


def _enforce_tunnel(user: dict, tunnel: Optional[str]) -> Optional[str]:
    """Force tunnel filter for sales/support roles."""
    role = user.get("role", "sales")
    if role in ("owner", "admin", "dev", "supervisor", "qa", "project_manager"):
        return tunnel  # privileged/oversight roles filter tunnel freely (team scoping applied separately — Phase 2)
    scope = user.get("tunnel_scope", role)
    if tunnel and tunnel != scope:
        raise HTTPException(status_code=403, detail="Access denied to this tunnel")
    return scope


async def _resolve_team_scope(user: dict) -> Optional[list[str]]:
    """Phase 2 team scoping. Returns:
      - None  → caller is NOT team-scoped (owner/admin/dev/qa/sales/support) — unchanged behavior.
      - []    → caller IS team-scoped (supervisor/PM) but owns no active team — FAIL CLOSED (see nothing).
      - [ids] → caller is team-scoped to these active team ids.
    """
    role = user.get("role", "")
    uid = user.get("id", "")
    if role == "supervisor":
        return await db.get_team_ids_for_supervisor(uid)
    if role == "project_manager":
        return await db.get_team_ids_for_pm(uid)
    return None


@router.get("/conversations")
async def list_conversations(
    tunnel: Optional[str] = Query(None, pattern="^(sales|support)$"),
    status: Optional[str] = Query(None, pattern="^(active|pending|closed)$"),
    assigned_to: Optional[str] = Query(None, pattern="^(me|none|all)$"),
    search: Optional[str] = Query(None, max_length=100),
    handled_by: Optional[str] = Query(None, pattern="^(human|ai|fallback)$"),
    tag: Optional[str] = Query(
        None,
        pattern="^(fresh|active|main_queue|completed|abandoned|no_engagement)$",
    ),
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    try:
        tunnel = _enforce_tunnel(user, tunnel)

        # Phase 2 team scoping (supervisor/PM): restrict to their team(s).
        # Fail closed — a scoped caller with no team sees an empty list.
        team_ids = await _resolve_team_scope(user)
        if team_ids is not None and len(team_ids) == 0:
            return {"success": True, "data": [], "count": 0}

        # AI-outcome tags are supervisor/oversight only — a queue-health
        # signal, not something an agent needs filtering their own queue.
        if tag in ("completed", "abandoned", "no_engagement"):
            role = user.get("role", "")
            if role not in ("owner", "admin", "dev", "supervisor", "qa", "project_manager"):
                raise HTTPException(
                    status_code=403,
                    detail="This tag filter is restricted to supervisors and admins.",
                )

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

        quiet_before = None
        no_engagement_only = False
        if tag == "abandoned":
            from datetime import datetime, timedelta, timezone
            from config.settings import settings
            # Abandoned chip: DB pre-filter narrows to "customer went quiet a
            # while ago". Deliberately does NOT touch status/status_in — the
            # chip layers over whatever state tab the caller picked (state
            # and outcome are orthogonal). derive_conversation_tag itself
            # never returns "abandoned" for a closed conversation, so
            # "Abandoned" + "All Closed" is a valid combination that (by
            # design) comes back empty rather than silently switching tabs.
            quiet_before = (
                datetime.now(timezone.utc)
                - timedelta(minutes=settings.inactive_quiet_minutes)
            ).isoformat()
        elif tag == "no_engagement":
            # GLOBAL: no operator was ever involved, so this has no team to
            # scope by. Override even a team-scoped supervisor's team_ids —
            # otherwise they'd silently see zero results (their team_id
            # filter can never match a NULL-team row). Status is left as
            # whatever tab the caller already selected (orthogonal to tag).
            no_engagement_only = True
            team_ids = None

        rows, total = await db.get_conversations(
            tunnel=tunnel, status=list_status, status_in=status_in, search=search,
            agent_id=agent_id_filter, agent_id_is_null=agent_id_is_null,
            team_ids=team_ids,
            tag=tag, quiet_before=quiet_before, no_engagement_only=no_engagement_only,
            limit=limit, offset=offset,
        )
        if handled_by == "human":
            rows = [c for c in rows if c.get("agent_state") == "active"]
        elif handled_by == "fallback":
            rows = [c for c in rows if c.get("agent_state") == "fallback"]
        elif handled_by == "ai":
            rows = [c for c in rows if c.get("agent_state") == "ai_only"]
        if handled_by:
            total = len(rows)
        # Supervisors (QA) never receive raw customer PII or marketing metadata.
        if user.get("role") == "supervisor":
            from app.security.pii import mask_visitor_row
            rows = [mask_visitor_row(dict(c)) for c in rows]
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
    # This endpoint drives the LIVE presence line in the open chat — the
    # one place an operator looks before typing. It must derive exactly
    # like the list and the detail, or the panel contradicts itself.
    effective, age = db.derive_effective_presence(
        metadata, last_user_message_at=conv.get("last_user_message_at")
    )
    return {
        "success": True,
        "data": {
            "widget_open": metadata.get("widget_open", False),
            "widget_presence": metadata.get("widget_presence", "minimized"),
            "widget_presence_effective": effective,
            "widget_presence_age_seconds": age,
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
    # Phase 2: supervisor/PM may read message content ONLY for conversations
    # handled by their own team(s). This narrows the Phase-1 supervisor read
    # flip from "whole tunnel" down to "own team". Fail closed.
    role = user.get("role")
    if role in ("supervisor", "project_manager"):
        team_ids = await _resolve_team_scope(user)
        conv = await db.get_conversation_simple(conversation_id)
        conv_team = (conv or {}).get("team_id")
        if not team_ids or conv_team not in team_ids:
            raise HTTPException(
                status_code=403,
                detail="You can only read messages for conversations handled by your team.",
            )
    msgs = await db.get_messages_after(conversation_id, after)
    return {"success": True, "data": msgs}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: dict = Depends(get_current_user),
):
    # A failed fetch is NOT a missing conversation. This used to return HTTP 200
    # with {data: null} for both cases, and since the client only treats non-2xx
    # as an error, every transient Supabase blip told the operator the
    # conversation had been deleted. Now: absent row → 404, fetch failure → 502,
    # so the panel's retry path recovers instead of lying. See Bug 5 forensic
    # (17.04.2026) for the earlier half of this fix.
    try:
        conv = await db.get_conversation(conversation_id, raise_on_error=True)
    except Exception as e:
        logger.error(
            f"get_conversation({conversation_id}) fetch failed: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=502, detail="Failed to load conversation") from e

    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # _enforce_tunnel signals 403 via HTTPException — it must propagate untouched.
    _enforce_tunnel(user, conv.get("tunnel"))

    # Phase 2: supervisor/PM see message content ONLY for their own team's
    # conversations; otherwise metadata-only (messages blanked). Fail closed.
    if user.get("role") in ("supervisor", "project_manager"):
        team_ids = await _resolve_team_scope(user)
        conv_team = conv.get("team_id")
        if not team_ids or conv_team not in team_ids:
            conv = dict(conv)
            conv["messages"] = []

    # Supervisors (QA) never receive raw customer PII or marketing metadata,
    # even within their own team.
    if user.get("role") == "supervisor":
        from app.security.pii import mask_visitor_row
        conv = mask_visitor_row(dict(conv))

    return {"success": True, "data": conv, "count": 1}


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

    # PRESENCE: a sent reply is the strongest proof the operator is here —
    # stronger than the 5s heartbeat, which can lapse (token refresh, network
    # blip, sleeping worker) and then hand an actively-writing operator's
    # conversation back to the AI. Never let a presence write block the send.
    try:
        await db.update_user_last_seen(_user_id)
    except Exception as e:
        logger.warning(f"last_seen update after agent message failed (non-fatal): {e}")

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
        # Personalization: the visitor should see a real person, not an
        # anonymous bot. Identity is attached to operator messages only —
        # never to AI or system rows.
        msg["agent_name"] = (_sender_db or {}).get("name") or "Consultant"
        msg["agent_avatar_url"] = (_sender_db or {}).get("avatar_url")
        await manager.push(conversation_id, msg)

    # The reply is out — the operator is no longer typing.
    from app.realtime.typing_indicator import typing_manager
    await typing_manager.clear_agent_typing(conversation_id)

    return {"success": True, "data": msg}


class AgentTypingBody(BaseModel):
    text: str = ""


@router.post("/conversations/{conversation_id}/agent-typing")
async def set_agent_typing_status(
    conversation_id: str,
    body: AgentTypingBody,
    user: dict = Depends(get_current_user),
):
    """Operator reports they are typing a reply; the widget shows it live.

    Mirrors the client→operator endpoint's convention: text present = typing,
    empty text = cleared. Only the operator's NAME reaches the visitor.
    """
    conv = await db.get_conversation_simple(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _enforce_tunnel(user, conv.get("tunnel"))

    from app.realtime.typing_indicator import typing_manager
    if body.text.strip():
        sender = await db.get_user_by_id(user.get("id"))
        name = (sender or {}).get("name") or "Consultant"
        await typing_manager.set_agent_typing(conversation_id, name)
    else:
        await typing_manager.clear_agent_typing(conversation_id)
    return {"success": True}


@router.delete("/conversations/{conversation_id}/agent-typing")
async def clear_agent_typing_status(
    conversation_id: str,
    user: dict = Depends(get_current_user),
):
    """Operator stopped typing (sent, cleared the box, or left the conversation)."""
    conv = await db.get_conversation_simple(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _enforce_tunnel(user, conv.get("tunnel"))

    from app.realtime.typing_indicator import typing_manager
    await typing_manager.clear_agent_typing(conversation_id)
    return {"success": True}


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

    from app.services.handoff import perform_handoff_to_agent
    current_agent = conv.get("assigned_agent_id")
    _me = user.get("id")
    # Re-claiming my own conversation: allowed exactly as before. The gate would
    # answer False here (the field is not NULL), and that False would mean
    # "someone else has it" — which is not true, it is mine.
    if current_agent and current_agent == _me:
        await perform_handoff_to_agent(
            conversation_id=conversation_id,
            agent_id=_me,
            tunnel=conv.get("tunnel", "sales"),
            emit_messages=False,
            handoff_reason="manual_claim",
            _pop_meta_keys=["agent_assign_count", "agent_cooldown_until"],
        )
        return {"success": True, "data": {"conversation_id": conversation_id, "assigned_to": _me}}
    # Cheap fast path: obviously taken. The gate below is still the truth.
    if current_agent:
        _owner = await db.get_user_by_id(current_agent)
        raise HTTPException(status_code=409, detail={
            "detail": "already_claimed",
            "winner": (_owner or {}).get("name") or "another agent",
            "conversation_id": conversation_id,
        })
    # The database decides. Exactly one UPDATE matches; the losers get nothing.
    _claim = await db.claim_conversation_if_unassigned(conversation_id, _me)
    if not _claim.get("won"):
        _cur = await db.get_conversation_simple(conversation_id)
        _winner_id = (_cur or {}).get("assigned_agent_id")
        _winner = await db.get_user_by_id(_winner_id) if _winner_id else None
        from app.services.presence import log_activity
        from app.pipeline.orchestrator import _fire_and_forget
        CLAIM_HEALTH["lost"] += 1
        CLAIM_HEALTH["races_detected"] += 1
        _fire_and_forget(log_activity(db, _me, conversation_id, "claim_lost"))
        raise HTTPException(status_code=409, detail={
            "detail": "already_claimed",
            "winner": (_winner or {}).get("name") or "another agent",
            "conversation_id": conversation_id,
        })
    # Won: the gate already wrote ownership, status and queued_at atomically.
    from app.services.presence import log_activity
    from datetime import datetime, timezone
    CLAIM_HEALTH["won"] += 1
    CLAIM_HEALTH["last_at"] = datetime.now(timezone.utc).isoformat()
    _age = _claim.get("queued_age_seconds")
    await log_activity(
        db, _me, conversation_id, "claim_won",
        response_seconds=int(_age) if _age is not None else None,
    )
    # Reset loop guards — agent chose this conv actively.
    # Single metadata write via perform_handoff (reads + merges + writes).
    await perform_handoff_to_agent(
        conversation_id=conversation_id,
        agent_id=_me,
        tunnel=conv.get("tunnel", "sales"),
        emit_messages=False,
        handoff_reason="manual_claim",
        _pop_meta_keys=["agent_assign_count", "agent_cooldown_until"],
        _gate_won=True,
    )

    return {"success": True, "data": {"conversation_id": conversation_id, "assigned_to": _me}}


@router.post("/conversations/{conversation_id}/release")
async def release_conversation_endpoint(
    conversation_id: str,
    user: dict = Depends(get_current_user),
):
    """Give a conversation back to the shared line — for accidental claims.

    The panel offers this for 30 seconds after a claim. Only the current owner
    may release: the conditional write guarantees it, so a stale click cannot
    take a conversation away from whoever holds it now.
    """
    conv = await db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _enforce_tunnel(user, conv.get("tunnel"))
    _me = user.get("id")
    if conv.get("assigned_agent_id") != _me:
        raise HTTPException(status_code=409, detail="Not yours to release")
    from app.services.handoff import _release_from_agent
    _meta = dict(conv.get("metadata") or {})
    _meta.pop("announce_pending", None)
    _meta.pop("agent_assigned_at", None)
    ok = await _release_from_agent(
        conversation_id, _me, _meta, reason="released_by_agent"
    )
    if not ok:
        raise HTTPException(status_code=409, detail="Already released")
    from app.services.presence import log_activity
    from app.pipeline.orchestrator import _fire_and_forget
    _fire_and_forget(log_activity(db, _me, conversation_id, "released_by_agent"))
    return {"success": True, "data": {"conversation_id": conversation_id}}


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

    # WHO closed this matters more than the fact that it is closed. The
    # abandoned-conversation cron sweeps closed conversations on purpose (#200
    # — the Diana class: a client who left contact details and went quiet is
    # exactly the dialable lead). But an AGENT closing a chat is a human
    # decision — spam, resolved by phone, not a lead — and the cron must not
    # overrule it by pushing a flight request to the CRM anyway.
    #
    # Written BEFORE the status change, so there is never an instant where the
    # conversation reads `closed` without saying who closed it. A patch, not a
    # snapshot: a full metadata write would erase keys another request set in
    # between (that is what migration 031 exists for).
    _marked = await db.patch_conversation_presence(conversation_id, {
        "closed_by_agent_id": user.get("id"),
        "closed_by_role": user.get("role") or "",
    })
    if not _marked:
        # Fall back rather than lose the decision: an unmarked close is one the
        # cron will happily push to the CRM tomorrow.
        try:
            _cm = dict((conv or {}).get("metadata") or {})
            _cm["closed_by_agent_id"] = user.get("id")
            _cm["closed_by_role"] = user.get("role") or ""
            await db.update_conversation(conversation_id, {"metadata": _cm})
            logger.warning(
                f"[{conversation_id}] closed-by marker written by snapshot — "
                "patch_conv_presence unavailable (migration 031)"
            )
        except Exception as e:
            logger.error(
                f"[{conversation_id}] could not record who closed it ({e}) — "
                "the CRM cron may still push this conversation"
            )

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


# ── Blocklist (abuse) ──
# Roles allowed to block/unblock. Deliberately NOT the same set as reassign:
# supervisors and PMs moderate abuse, sales/support/qa do not.
_BLOCK_ROLES = ("owner", "admin", "dev", "supervisor", "project_manager")


class BlockRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


@router.post("/conversations/{conversation_id}/block")
async def block_conversation_visitor(
    conversation_id: str,
    body: BlockRequest | None = None,
    user: dict = Depends(get_current_user),
):
    """Block this visitor's phone + email (and record their IP) in one action.

    Only phone/email actually refuse future visitors; the IP row is recorded for
    audit and expires (see app/services/blocklist.py).
    """
    if user.get("role") not in _BLOCK_ROLES:
        raise HTTPException(403, "Not allowed to block visitors")

    conv = await db.get_conversation_simple(conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")

    from app.services.blocklist import block_from_conversation

    blocked = await block_from_conversation(
        conv,
        blocked_by_id=user.get("id"),
        reason=(body.reason if body else None),
    )
    if not blocked:
        raise HTTPException(400, "Nothing to block — no phone, email or IP on this conversation")

    logger.warning(
        f"[blocklist] BLOCK conv={conversation_id} kinds={blocked} "
        f"by={user.get('email')} (role={user.get('role')})"
    )
    return {"success": True, "blocked": blocked}


@router.post("/conversations/{conversation_id}/unblock")
async def unblock_conversation_visitor(
    conversation_id: str,
    user: dict = Depends(get_current_user),
):
    """Remove this conversation's phone / email / IP from the blocklist."""
    if user.get("role") not in _BLOCK_ROLES:
        raise HTTPException(403, "Not allowed to unblock visitors")

    conv = await db.get_conversation_simple(conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")

    from app.services.blocklist import unblock_from_conversation

    removed = await unblock_from_conversation(conv)
    logger.warning(
        f"[blocklist] UNBLOCK conv={conversation_id} kinds={removed} "
        f"by={user.get('email')} (role={user.get('role')})"
    )
    return {"success": True, "unblocked": removed}


@router.get("/blocklist")
async def list_blocklist(user: dict = Depends(get_current_user)):
    """Current blocklist entries (management view)."""
    if user.get("role") not in _BLOCK_ROLES:
        raise HTTPException(403, "Not allowed to view the blocklist")
    return {"success": True, "data": await db.blocklist_list()}


# ── Operator History ──


def _parse_operator_events(messages: list[dict]) -> list[dict]:
    """Parse system messages for operator history (pre-activity_log fallback)."""
    events = []
    for m in messages:
        c, ts = m.get("content", ""), m.get("created_at", "")
        _assisted = _re.search(r"being assisted by (.+?)\.", c)
        if _assisted:
            events.append({
                "action": "assigned",
                "agent_name": _assisted.group(1),
                "happened_at": ts,
            })
        if "connecting you" in c.lower():
            events.append({"action": "connecting", "happened_at": ts})
        if "sorry for the wait" in c.lower() or "right where we left off" in c.lower():
            events.append({"action": "deadline_fired", "happened_at": ts})
        _joined = _re.search(r"(.+?) has joined", c)
        if _joined and "connecting" not in c.lower():
            events.append({
                "action": "agent_joined",
                "agent_name": _joined.group(1),
                "happened_at": ts,
            })
    return events


@router.get("/conversations/{conversation_id}/operator-history")
async def get_operator_history(
    conversation_id: str,
    user: dict = Depends(get_current_user),
):
    """Operator assignment history — timeline per conversation."""
    role = user.get("role", "")
    if role not in ("owner", "admin", "dev", "supervisor", "qa", "sales", "support"):
        raise HTTPException(status_code=403, detail="Access denied")

    conv = await db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _enforce_tunnel(user, conv.get("tunnel"))

    events = await db.get_activity_for_conversation(conversation_id)
    if events:
        _a = next((e for e in events if e["action"] == "assigned"), None)
        _r = next((e for e in events if e["action"] == "first_response"), None)
        _d = next((e for e in events if e["action"] == "deadline_fired"), None)
        if _a and _r:
            summary = (
                f"👤 {_a.get('agent_name', '?')} → ✅ "
                f"{_r.get('response_seconds', '?')}s"
            )
        elif _a and _d:
            summary = f"👤 {_a.get('agent_name', '?')} → ❌ timeout"
        elif _a:
            summary = f"👤 {_a.get('agent_name', '?')} → ⏳ active"
        else:
            summary = "🤖 AI handled"
        return {"source": "activity_log", "events": events, "summary": summary}

    msgs = await db.get_system_messages_for_conversation(conversation_id)
    if msgs:
        parsed = _parse_operator_events(msgs)
        if parsed:
            _a = next((e for e in parsed if e["action"] == "assigned"), None)
            _d = next((e for e in parsed if e["action"] == "deadline_fired"), None)
            if _a and _d:
                summary = f"👤 {_a['agent_name']} → ❌ timeout"
            elif _a:
                summary = f"👤 {_a['agent_name']}"
            else:
                summary = "🤖 AI handled"
            return {"source": "messages", "events": parsed, "summary": summary}

    return {"source": "none", "events": [], "summary": "🤖 AI handled"}
