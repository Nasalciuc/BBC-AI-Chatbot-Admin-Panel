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

# Missed-handoff counter — surfaced in /health as handoffs_expired. Every
# increment is a client who asked for a human and never got one in time.
HANDOFF_HEALTH: dict = {"expired_since_boot": 0, "last_at": None, "fallback_races": 0}


# ── H1: Agent staleness detection + AI fallback ──────────────

async def is_agent_effectively_offline(conversation_id: str) -> bool:
    """Check if the agent assigned to this conversation is effectively offline.

    Returns True if:
      - No agent assigned, OR
      - Agent's last_seen_at is older than agent_timeout_seconds AND they have
        not sent a message within agent_silent_timeout_seconds, OR
      - Agent was assigned but never spoke within agent_silent_timeout_seconds

    A recent agent message counts as presence on its own: an operator who is
    replying is demonstrably here even if their heartbeat lapsed.
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

    # Parse last_seen (ISO format from Supabase)
    last_seen_dt = None
    if last_seen:
        try:
            if isinstance(last_seen, str):
                # Handle both Z suffix and +00:00
                last_seen_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            else:
                last_seen_dt = last_seen
        except (ValueError, TypeError):
            last_seen_dt = None

    last_agent_msg = await db.get_last_agent_message_time(conversation_id)
    if last_agent_msg is not None and last_agent_msg.tzinfo is None:
        last_agent_msg = last_agent_msg.replace(tzinfo=timezone.utc)

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.agent_timeout_seconds)
    if last_seen_dt is None or last_seen_dt < cutoff:
        # Heartbeat missing/expired. A message sent inside the silence window
        # still proves presence — the operator is typing, not gone.
        msg_cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=settings.agent_silent_timeout_seconds
        )
        if last_agent_msg is not None and last_agent_msg >= msg_cutoff:
            return False
        return True

    # Check agent_silent_timeout: has the agent actually responded?
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


async def _release_from_agent(
    conversation_id: str, agent_id: str, metadata: dict,
    reason: str = "agent_offline",
) -> bool:
    """Hand the conversation back to the AI — once, by whoever gets there first.

    reason (spec v2.4 §5.4) decides one extra thing: whether this release also
    throws the conversation back into the shared line.
      agent_offline | released_by_agent | supervisor  -> queued_at = now()
      anything else                                   -> queued_at untouched
    The write is unconditional for those reasons, on purpose: enqueue is
    idempotent so the first wait is never reset by later messages, but a release
    starts a NEW life in the queue, and its age must start now. The five
    terminal closes are NOT releases and never come through here.

    The sweeper runs on EVERY operator heartbeat, so with N operators online it
    passes over the same stale conversation every 5 seconds, N times. Nothing
    stopped it running twice: the release wrote `assigned_agent_id = None`
    unconditionally, so every run "succeeded", and every run logged a timeout
    against the agent. On 18 Aug one missed handoff produced 21 `Timeout`
    rows in three seconds in Nolan Hunt's history, and 16 in Robert Doyle's.
    A supervisor reading that cannot tell what happened, and "how often did X
    miss a handoff" answers 21 instead of 1.

    So the WRITE decides. `.eq("assigned_agent_id", agent_id)` matches exactly
    one run — the one that still finds the conversation belonging to that
    agent. Everyone else gets zero rows back, and has nothing left to say.

    Returns False on error too: if we cannot prove we are the one who released
    it, we must not log a timeout against a human's record.
    """
    try:
        client = db.get_client()
        _update: dict = {
            "mode": "ai", "assigned_agent_id": None, "metadata": metadata,
        }
        if reason in ("agent_offline", "released_by_agent", "supervisor") \
                and db._queued_at_column_available():
            from datetime import datetime as _dt2, timezone as _tz2
            _update["queued_at"] = _dt2.now(_tz2.utc).isoformat()
        def _rel(with_queued_at: bool):
            _u = dict(_update)
            if not with_queued_at:
                _u.pop("queued_at", None)
            return (
                client.table("conversations")
                .update(_u)
                .eq("id", conversation_id)
                .eq("assigned_agent_id", agent_id)
                .execute()
            )
        try:
            res = await db._run_sync(
                lambda: _rel("queued_at" in _update), idempotent=False
            )
        except Exception as _e:
            db._downgrade_queued_at(_e)
            if db._queued_at_column_available():
                raise
            res = await db._run_sync(lambda: _rel(False), idempotent=False)
        return bool(res.data)
    except Exception as e:
        logger.warning(
            f"[handoff] Conv {conversation_id}: conditional release failed ({e}, "
            f"reason={reason}) — treating as lost race, no timeout recorded"
        )
        return False


async def _claim_fallback_notice(conversation_id: str, metadata: dict) -> bool:
    """Win the right to say "I'm here" exactly once, or stay quiet.

    `_safe_system_msg`'s cooldown is check-then-act: it reads the last system
    message, decides it is not a duplicate, and only then writes. Three
    deadline sweeps firing within the same second all read the same "nothing
    there yet" and all three write. On 18 Aug 2026 a client got the apology
    three times in a row, seconds apart.

    So the decision moves into the database, where it can only have one
    answer: the flag is set by a single UPDATE whose WHERE clause requires it
    to be absent. Exactly one caller updates a row; everyone else gets zero
    rows back and says nothing.

    The metadata we merge is the dict this function's caller has just written,
    so this adds no clobber window that the write above it did not already
    have. Returns False on any error — losing the race and a broken query both
    mean the same thing here: do not speak.
    """
    try:
        client = db.get_client()
        payload = {**(metadata or {}), "fallback_notice_at": datetime.now(timezone.utc).isoformat()}
        res = await db._run_sync(
            lambda: client.table("conversations")
            .update({"metadata": payload})
            .eq("id", conversation_id)
            .is_("metadata->>fallback_notice_at", "null")
            .execute(),
            idempotent=False,
        )
        won = bool(res.data)
        if not won:
            logger.info(
                f"[handoff] Conv {conversation_id}: fallback notice already claimed "
                "by a concurrent sweep — staying quiet"
            )
        return won
    except Exception as e:
        logger.warning(
            f"[handoff] Conv {conversation_id}: could not claim the fallback notice ({e}) — "
            "staying quiet rather than risking a duplicate"
        )
        return False


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
        from app.services.routing import dispatch_needs_agent
        from app.pipeline.orchestrator import _fire_and_forget
        _fire_and_forget(dispatch_needs_agent(conversation_id))
    except Exception as e:
        logger.warning(f"[{conversation_id}] Failed to queue needs_agent: {e}")

    # The queue reply COLLECTS instead of deflecting to a phone number —
    # acknowledge, promise the teammate, and ask the next missing piece so
    # the wait is never a dead end. (The phone stays in closing flows only.)
    _next_question = ""
    try:
        from app.services import lead_service
        from app.models.lead import get_missing_fields

        _lead = await lead_service.get_or_create_lead(conversation_id)
        _conv_row = await db.get_conversation_simple(conversation_id) or {}
        _missing = get_missing_fields(_lead or {}, {
            "visitor_name": _conv_row.get("visitor_name") or getattr(visitor, "name", None),
            "visitor_email": _conv_row.get("visitor_email") or getattr(visitor, "email", None),
            "visitor_phone": _conv_row.get("visitor_phone") or getattr(visitor, "phone", None),
        })
        _ask_map = {
            "route": None,  # route is two questions — keep it free-form
            "travel dates": "ask_dates",
            "departure date": "ask_dates",
            "phone": "ask_phone",
            "email": "ask_email",
            "name": "ask_name",
            "passengers": "ask_passengers",
        }
        for _m in _missing:
            _tpl_key = next((v for k, v in _ask_map.items() if k in _m), None)
            if _tpl_key:
                _q = get_template(_tpl_key, tunnel, visitor)
                if _q:
                    _next_question = f" While they join — {_q[0].lower()}{_q[1:]}"
                    break
    except Exception as _e:
        logger.warning(f"[{conversation_id}] queue-reply question skipped: {_e}")

    return (
        "You got it — I'm flagging a teammate right now, and your chat is "
        f"first in line.{_next_question}",
        "template",
    )


async def fall_back_to_ai(conversation_id: str) -> None:
    """Revert a human-mode conversation back to AI.
    Clears agent assignment, sets mode='ai', emits a system message unless
    the reservation was silent (announce_pending=True), and clears loop
    guards (GO-08) so the conversation can be re-assigned."""
    _conv = await db.get_conversation_simple(conversation_id)
    _meta = dict((_conv or {}).get("metadata") or {})
    _was_unannounced = bool(_meta.get("announce_pending"))
    # A missed handoff is a LOST demand signal, not housekeeping — it used to
    # be swept back silently (Oslo's chats_served_today=1 with zero messages
    # was an assignment he never saw). Scream, count, and mark it.
    _missed_by = (_conv or {}).get("assigned_agent_id")
    if _missed_by:
        from datetime import datetime as _dt, timezone as _tz

        # Stamped into the payload now; COUNTED only if we win the release
        # below. Counting here is what made one missed handoff look like 21.
        _meta["missed_by_human_at"] = _dt.now(_tz.utc).isoformat()
    # GO-08: reset loop guards so the conversation can be re-assigned.
    _meta.pop("agent_assign_count", None)
    _meta.pop("agent_cooldown_until", None)
    _meta.pop("announce_pending", None)
    _meta.pop("agent_assigned_at", None)

    from app.services.presence import log_activity
    from app.pipeline.orchestrator import _fire_and_forget
    _agent = (_conv or {}).get("assigned_agent_id") or ""

    if _agent:
        # One release, one set of consequences. Whoever loses this stops here:
        # the conversation was already handed back, and there is nothing left
        # to log, say, or push.
        if not await _release_from_agent(
            conversation_id, _agent, _meta, reason="agent_offline"
        ):
            HANDOFF_HEALTH["fallback_races"] += 1
            logger.info(
                f"[{conversation_id}] fallback race lost — already released by "
                "another sweep, staying quiet"
            )
            return
        HANDOFF_HEALTH["expired_since_boot"] += 1
        HANDOFF_HEALTH["last_at"] = _meta.get("missed_by_human_at")
        logger.error(
            f"[{conversation_id}] HANDOFF EXPIRED: agent {_agent} never "
            f"engaged — conversation returns to AI (counted in /health)"
        )
        _fire_and_forget(log_activity(db, _agent, conversation_id, "deadline_fired"))
    else:
        # Nobody holds it, so there is no race and no human to charge a
        # timeout to. Same write as before, no activity row.
        await db.update_conversation(conversation_id, {
            "mode": "ai",
            "assigned_agent_id": None,
            "metadata": _meta,
        })
    # V4: emit fallback message ⟺ a promise was made this cycle —
    # either the announce ("X has joined") OR the queued-message promise
    # ("One moment please, connecting you with a specialist...").
    _promised_recently = await _handoff_phrase_recently_sent(conversation_id)
    # "Sorry for the wait — I'm here" says a human arrived. If no agent was
    # ever assigned on this conversation, nobody arrived, and the sentence is
    # simply false: the client reads it, believes an operator is now reading
    # along, and waits for them. The AI keeps answering either way (the FIX-C
    # backstop below), so silence costs the client nothing and the lie costs
    # them their patience.
    _silent = (_was_unannounced and not _promised_recently) or not _agent
    if not _silent:
        # Two nets, in order: the row-level claim decides WHO speaks, the
        # content cooldown still guards against a repeat later in the shift.
        if await _claim_fallback_notice(conversation_id, _meta):
            msg = await _safe_system_msg(conversation_id, _FALLBACK_MSG, cooldown_seconds=120)
            if msg:
                await manager.push(conversation_id, msg)
    # else: silent reservation expired — visitor never knew; AI continues seamlessly.
    logger.info(
        f"[handoff] Conv {conversation_id}: agent offline → fell back to AI "
        f"(loop guards cleared, silent={_silent}, had_agent={bool(_agent)})"
    )
    # FIX-C: backstop — if the visitor's last message was never answered (it arrived
    # while in human mode and no AI reply followed), re-run the pipeline now so they
    # get a response instead of silence. Covers every fallback path, not just FIX-A.
    try:
        _last_msgs = await db.get_recent_messages(conversation_id, limit=1)
        _last = _last_msgs[-1] if _last_msgs else None
        if _last and _last.get("role") == "user" and _last.get("created_at"):
            _has_reply_after = await db.has_ai_or_agent_message_since(
                conversation_id, _last["created_at"]
            )
            if not _has_reply_after:
                from app.pipeline.orchestrator import reprocess_last_user_message
                _fire_and_forget(reprocess_last_user_message(conversation_id))
    except Exception as _e:
        logger.warning(f"[handoff] FIX-C reprocess skipped for {conversation_id}: {_e}")


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
    _gate_won: bool = False,
) -> dict:
    """Assign conversation to an agent and emit system messages.

    handoff_reason values:
      'auto_assign'     — heartbeat or on-close auto-pick; silent reservation
                          (announce fires on agent's first real message).
      'manual_claim'    — operator took/sent message; announce from claim UX.
      'visitor_request' — visitor asked for agent; immediate system messages.
      'first_message'   — operator-first routing on visitor's first message.
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
        _meta["engaged_agent_id"] = agent_id
        if handoff_reason in ("auto_assign", "first_message"):
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

    # Phase 2 (frozen history): stamp the conversation with the assigned
    # operator's CURRENT team_id, so it stays with the team that handled it
    # even if the operator later changes teams. Only stamp when the operator
    # actually has a team — never overwrite a previously-stamped team with
    # NULL. (AI fallback nulls assigned_agent_id but leaves team_id intact.)
    # Spec v2.4 §5.5: ownership is decided by the gate, and NOTHING happens
    # before it is won. A caller that already went through the gate passes
    # _gate_won=True and we must not write ownership twice.
    if _is_new_cycle and not _gate_won:
        _claim = await db.claim_conversation_if_unassigned(conversation_id, agent_id)
        if not _claim.get("won"):
            # Someone else owns it. No metadata, no "X has joined", no
            # notification, no CRM signal: the client must never see two
            # consultants introducing themselves two seconds apart.
            logger.info(
                f"[handoff] Conv {conversation_id}: claim gate lost for agent "
                f"{agent_id} (reason={handoff_reason}) — no side effects"
            )
            return {}
        _gate_won = True

    _update: dict = {"metadata": _meta}
    if not _gate_won:
        # Re-handoff to the SAME agent (not a new cycle): ownership is already
        # correct, we only refresh metadata. Writing it again is harmless and
        # keeps behaviour identical to today.
        _update["mode"] = "human"
        _update["assigned_agent_id"] = agent_id
    _operator = await db.get_user_by_id(agent_id)
    _operator_team_id = (_operator or {}).get("team_id")
    if _operator_team_id:
        _update["team_id"] = _operator_team_id

    await db.update_conversation(conversation_id, _update)
    if _is_new_cycle:
        from app.services.presence import log_activity
        from app.pipeline.orchestrator import _fire_and_forget
        _fire_and_forget(
            log_activity(
                db, agent_id, conversation_id, "assigned",
                handoff_reason=handoff_reason,
            )
        )

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
