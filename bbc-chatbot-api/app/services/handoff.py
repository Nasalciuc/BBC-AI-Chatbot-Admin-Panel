"""Handoff service — unified logic for human ↔ AI transitions.

H1: is_agent_effectively_offline / fall_back_to_ai
    Used when a conversation is in human mode but the assigned agent
    has gone silent (heartbeat timeout or agent_silent_timeout exceeded).
    Falls back to AI so the visitor isn't stuck.

H3: perform_handoff_to_agent
    Single function that sets mode='human', assigns the agent, and
    emits the 3 system messages (connecting + joined + welcome).
    Replaces all ad-hoc handoff code scattered across chat.py and agent.py.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from config.settings import settings
from app.db import supabase as db
from app.services.conversation_service import add_message
from app.realtime.manager import manager

logger = logging.getLogger(__name__)


# ── H1: Agent staleness detection + AI fallback ──────────────

async def is_agent_effectively_offline(conversation_id: str) -> bool:
    """Check if the agent assigned to this conversation is effectively offline.

    Returns True if:
      - No agent assigned, OR
      - Agent's last_seen_at is older than agent_timeout_seconds, OR
      - Agent hasn't sent a message in agent_silent_timeout_seconds
    """
    conv = await db.get_conversation_simple(conversation_id)
    if not conv:
        return False

    agent_id = conv.get("assigned_agent_id")
    if not agent_id:
        return True  # no agent at all — effectively offline

    # Check heartbeat freshness
    agent = await db.get_user_by_id(agent_id)
    if not agent:
        return True

    last_seen = agent.get("last_seen_at")
    if not last_seen:
        return True

    # Parse last_seen (ISO format from Supabase)
    try:
        if isinstance(last_seen, str):
            # Handle both Z suffix and +00:00
            last_seen_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        else:
            last_seen_dt = last_seen
    except (ValueError, TypeError):
        return True

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.agent_timeout_seconds)
    if last_seen_dt < cutoff:
        return True  # heartbeat expired

    # Check agent_silent_timeout: has the agent actually responded?
    last_agent_msg = await db.get_last_agent_message_time(conversation_id)
    if last_agent_msg is None:
        # Agent was assigned but never sent a message.
        # Prefer agent_assigned_at (cycle start) — updated_at moves on ANY
        # conversation update, so an active visitor kept "refreshing" the
        # agent's deadline forever (divergence F5).
        _meta_check = conv.get("metadata") or {}
        conv_updated = _meta_check.get("agent_assigned_at") or conv.get("updated_at")
        if conv_updated:
            try:
                updated_dt = datetime.fromisoformat(
                    conv_updated.replace("Z", "+00:00") if isinstance(conv_updated, str) else conv_updated
                )
                silent_cutoff = datetime.now(timezone.utc) - timedelta(
                    seconds=settings.agent_silent_timeout_seconds
                )
                if updated_dt < silent_cutoff:
                    return True
            except (ValueError, TypeError):
                pass
        return False

    return False


_FALLBACK_MSG = (
    "Sorry for the wait — I'm here and we can pick up right where we left off."
)


async def _safe_system_msg(
    conversation_id: str,
    content: str,
    cooldown_seconds: int = 60,
) -> Optional[dict]:
    """Add system message only if not a duplicate within the cooldown window."""
    last = await db.get_last_system_message(conversation_id)
    if last and last.get("content") == content:
        created = last.get("created_at", "")
        try:
            msg_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - msg_time).total_seconds()
            if elapsed < cooldown_seconds:
                logger.debug(
                    f"[handoff] Skip duplicate system msg "
                    f"({elapsed:.0f}s < {cooldown_seconds}s)"
                )
                return None
        except Exception:
            pass
    return await add_message(conversation_id, "system", content)


async def _handoff_phrase_recently_sent(conversation_id: str, lookback: int = 8) -> bool:
    """Check if a handoff/connecting phrase was sent recently in last N messages."""
    try:
        msgs = await db.get_recent_messages(conversation_id, limit=lookback)
        _patterns = (
            "connecting you",
            "specialist",
            "reach out",
            "contact you",
            "connect you",
            "one moment",
        )
        for msg in reversed(msgs):
            role = msg.get("role", "")
            if role in ("ai", "system"):
                content = (msg.get("content") or "").lower()
                if any(p in content for p in _patterns):
                    return True
        return False
    except Exception:
        return False


async def get_handoff_response(
    conversation_id: str,
    tunnel: str,
    visitor,
    skip_if_recent: bool = True,
    visitor_id: Optional[str] = None,
) -> tuple[str, str]:
    """Decide honest handoff response based on agent availability.

    Returns: (response_text, model_used)
    """
    from app.services.routing import route_conversation
    from app.ai.templates import get_template

    if skip_if_recent:
        already = await _handoff_phrase_recently_sent(conversation_id)
        if already:
            return (
                "I understand you'd like to speak with someone. "
                "You can reach us directly at +1 (888) 322-7999 — available 24/7.",
                "template",
            )

    route = await route_conversation(tunnel, visitor=visitor, visitor_id=visitor_id)

    if route and route.get("agent_id"):
        await perform_handoff_to_agent(
            conversation_id,
            agent_id=route["agent_id"],
            agent_name=route.get("agent_name", "A specialist"),
            tunnel=tunnel,
            emit_messages=True,
            handoff_reason="visitor_request",
        )
        return (
            "I'm connecting you with a specialist now. They'll have all the details "
            "from our conversation.",
            "handoff",
        )

    # Queue feeding (V2): client asked for a human, none available now.
    try:
        await db.update_conversation(conversation_id, {"status": "needs_agent"})
        logger.info(f"[{conversation_id}] No agent available → status=needs_agent (queued)")
    except Exception as e:
        logger.warning(f"[{conversation_id}] Failed to queue needs_agent: {e}")

    text = get_template("no_agent_available", tunnel, visitor)
    return (
        text or "All specialists are currently busy. Please call +1 (888) 322-7999.",
        "template",
    )


async def fall_back_to_ai(conversation_id: str) -> None:
    """Revert a human-mode conversation back to AI.
    Clears agent assignment, sets mode='ai', emits a system message unless
    the reservation was silent (announce_pending=True), and sets a 15-min
    cooldown to prevent infinite reassign loops."""
    _conv = await db.get_conversation_simple(conversation_id)
    _meta = dict((_conv or {}).get("metadata") or {})
    _was_unannounced = bool(_meta.get("announce_pending"))
    _assign_count = int(_meta.get("agent_assign_count", 0))
    _cooldown = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    _meta["agent_cooldown_until"] = _cooldown
    _meta["agent_assign_count"] = _assign_count  # preserved; incremented at assign time
    _meta.pop("announce_pending", None)
    _meta.pop("agent_assigned_at", None)

    await db.update_conversation(conversation_id, {
        "mode": "ai",
        "assigned_agent_id": None,
        "metadata": _meta,
    })
    # V4: emit fallback message ⟺ a promise was made this cycle —
    # either the announce ("X has joined") OR the queued-message promise
    # ("One moment please, connecting you with a specialist...").
    _promised_recently = await _handoff_phrase_recently_sent(conversation_id)
    _silent = _was_unannounced and not _promised_recently
    if not _silent:
        msg = await _safe_system_msg(conversation_id, _FALLBACK_MSG, cooldown_seconds=120)
        if msg:
            await manager.push(conversation_id, msg)
    # else: silent reservation expired — visitor never knew; AI continues seamlessly.
    logger.info(
        f"[handoff] Conv {conversation_id}: agent offline → fell back to AI "
        f"(assign_count={_assign_count}, cooldown=15min, silent={_was_unannounced})"
    )


# ── H3: Unified handoff to agent ─────────────────────────────

async def perform_handoff_to_agent(
    conversation_id: str,
    agent_id: str,
    agent_name: str = "A specialist",
    tunnel: str = "sales",
    emit_messages: bool = True,
    handoff_reason: str = "manual",
    _extra_meta: dict | None = None,
    _pop_meta_keys: list | None = None,
) -> dict:
    """Assign conversation to an agent and emit system messages.

    handoff_reason values:
      'auto_assign'     — heartbeat or on-close auto-pick; silent reservation
                          (announce fires on agent's first real message).
      'manual_claim'    — operator took/sent message; announce from claim UX.
      'visitor_request' — visitor asked for agent; immediate system messages.
      'manual'          — generic/admin action (default).

    Returns dict with the system message rows (or empty dict if
    emit_messages=False).
    """
    # Merge metadata: preserve guard fields, add tracking fields.
    _conv_cur = await db.get_conversation_simple(conversation_id)
    _meta = dict((_conv_cur or {}).get("metadata") or {})
    # agent_assigned_at marks the START of an assignment CYCLE.
    # Write it ONLY when the assigned agent CHANGES — never refresh on
    # re-handoff to the same agent (F1: refreshing evicted engaged agents).
    _is_new_cycle = (_conv_cur or {}).get("assigned_agent_id") != agent_id
    if _is_new_cycle:
        _meta["agent_assigned_at"] = datetime.now(timezone.utc).isoformat()
        _meta["handoff_reason"] = handoff_reason
        if handoff_reason == "auto_assign":
            _meta["announce_pending"] = True
            _meta.pop("agent_cooldown_until", None)
        else:
            _meta.pop("announce_pending", None)
    # _is_new_cycle False → leave agent_assigned_at / handoff_reason /
    # announce_pending exactly as they are.
    if _extra_meta:
        _meta.update(_extra_meta)
    if _pop_meta_keys:
        for _k in _pop_meta_keys:
            _meta.pop(_k, None)

    await db.update_conversation(conversation_id, {
        "mode": "human",
        "assigned_agent_id": agent_id,
        "metadata": _meta,
    })

    if not emit_messages:
        return {}

    connecting = settings.connecting_message
    joined = settings.joined_message_template.format(agent_name=agent_name)
    welcome = (
        settings.welcome_message_sales
        if tunnel == "sales"
        else settings.welcome_message_support
    )
    qr = (
        settings.quick_replies_sales
        if tunnel == "sales"
        else settings.quick_replies_support
    )

    row1 = await _safe_system_msg(conversation_id, connecting, cooldown_seconds=60)
    row2 = await _safe_system_msg(conversation_id, joined, cooldown_seconds=60)
    row3 = await _safe_system_msg(conversation_id, welcome, cooldown_seconds=60)

    # Push to SSE — no-op if widget isn't connected
    for row in (row1, row2, row3):
        if row:
            await manager.push(conversation_id, row)

    return {
        "connecting": row1,
        "joined": row2,
        "welcome": row3,
        "quick_replies": qr,
    }
