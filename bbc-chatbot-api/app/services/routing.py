"""Routing engine — casino-fair assignment to free operators.

Algorithm: Fisher-Yates shuffle (CSPRNG) + sort by chats_served_today.
Least-served-today agent wins. Equal counts: random tiebreak.
Daily counter resets lazily (date check, no cron needed).

Sticky affinity: returning clients (closed conv within 90 days) go back to
the same operator if they're online, bypassing max_concurrent_chats.
New leads still respect the max_concurrent cap.
"""
import secrets
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from config.settings import settings
from app.db import supabase as db

logger = logging.getLogger(__name__)

AFFINITY_WINDOW_DAYS = 90  # Dan's business rule — returning-client definition

# TODO (privacy hardening): shared-browser residual risk — on a shared PC,
# user B completing the form with user A's email+phone (or being auto-
# restored via visitor_id in localStorage) will be routed to user A's
# previous operator, potentially exposing A's identity/context to B.
# Mitigation (separate ticket): add an email re-verify challenge on tab-
# close reopen. For BBC's primarily-personal-device user base the risk is
# accepted. Do NOT remove this TODO.


async def _find_sticky_operator(
    visitor,
    visitor_id: Optional[str],
) -> Optional[dict]:
    """Find the most recent operator this visitor spoke with in the last 90 days.
    Returns routing dict if found and effectively online, else None.

    Bypasses max_concurrent_chats — returning clients always go back to their
    operator regardless of load (Dan's policy).
    """
    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(days=AFFINITY_WINDOW_DAYS)
    ).isoformat()

    db_client = db.get_client()
    query = db_client.table("conversations").select(
        "assigned_agent_id, updated_at"
    )

    if visitor_id:
        query = query.eq("visitor_id", visitor_id)
    else:
        email = getattr(visitor, "email", None) if visitor else None
        phone = getattr(visitor, "phone", None) if visitor else None
        if not email and not phone:
            return None  # no way to identify visitor — treat as new lead
        # Require BOTH to match when present, to reduce shared-browser false positives
        if email and phone:
            query = query.eq("visitor_email", email).eq("visitor_phone", phone)
        elif email:
            query = query.eq("visitor_email", email)
        else:
            query = query.eq("visitor_phone", phone)

    query = (
        query.eq("status", "closed")
        .not_.is_("assigned_agent_id", "null")
        .gte("updated_at", cutoff_iso)
        .order("updated_at", desc=True)
        .limit(1)
    )

    res = await db._run_sync(lambda: query.execute())
    if not res.data:
        return None

    candidate_agent_id = res.data[0]["assigned_agent_id"]

    # Is the candidate effectively online?
    agent = await db.get_user_by_id(candidate_agent_id)
    if not agent:
        return None
    if not agent.get("is_active"):
        return None
    if not agent.get("is_ready"):
        return None
    if agent.get("role") in db._MANAGEMENT_ROLES:
        return None  # guards against former operator promoted to admin/dev/owner

    last_seen = agent.get("last_seen_at")
    if not last_seen:
        return None
    try:
        last_seen_dt = datetime.fromisoformat(
            last_seen.replace("Z", "+00:00")
            if isinstance(last_seen, str)
            else last_seen.isoformat()
        )
    except (ValueError, TypeError):
        return None
    if (
        datetime.now(timezone.utc) - last_seen_dt
    ).total_seconds() > settings.agent_timeout_seconds:
        return None  # heartbeat stale — treat as offline

    agent_name = agent.get("name") or agent.get("email") or "A specialist"
    return {
        "agent_id": candidate_agent_id,
        "agent_name": agent_name,
        "mode": "human",
        "reason": "affinity",
    }


async def route_conversation(
    tunnel: str, visitor=None, visitor_id: Optional[str] = None
) -> dict:
    """Pick a FREE operator using casino-fair distribution.

    Order of preference:
      1. Sticky routing — returning client's previous operator, if online.
         Bypasses max_concurrent_chats.
      2. Normal routing — least-loaded eligible operator under max_concurrent.
      3. No agent — returns {'agent_id': None, 'mode': 'ai'}, caller falls
         through to AI pipeline.

    Returns: {"agent_id": uuid|None, "mode": "human"|"ai", "agent_name": str|None}
    """
    try:
        # Step 0: Sticky affinity — returning client's previous operator
        try:
            sticky = await _find_sticky_operator(visitor, visitor_id)
            if sticky:
                logger.info(
                    f"[routing] Sticky affinity hit: visitor "
                    f"(id={visitor_id}) → agent "
                    f"{sticky['agent_id'][:8]}... ({sticky['agent_name']})"
                )
                await db.increment_chats_served(
                    await db.get_user_by_id(sticky["agent_id"]) or {}
                )
                return sticky
        except Exception as e:
            logger.error(f"[routing] Sticky check failed: {e} → normal routing")

        agents = await db.get_available_agents(
            tunnel=tunnel,
            timeout_seconds=settings.agent_timeout_seconds,
        )
        if not agents:
            logger.info(f"[routing] No agents online for tunnel={tunnel} → AI")
            return {"agent_id": None, "mode": "ai", "agent_name": None}

        # Filter: only FREE agents (0 active conversations — strict 1:1 rule)
        free_agents = []
        for agent in agents:
            count = await db.get_agent_active_count(agent["id"])
            if count < settings.max_concurrent_chats:
                free_agents.append(agent)

        if not free_agents:
            logger.info(f"[routing] All agents busy for tunnel={tunnel} → AI")
            return {"agent_id": None, "mode": "ai", "agent_name": None}

        # Lazy daily reset: stale date → treat served count as 0
        today = date.today().isoformat()
        for a in free_agents:
            if a.get("chats_served_date") != today:
                a["chats_served_today"] = 0

        # Casino step 1: Fisher-Yates shuffle with CSPRNG (random tiebreak)
        for i in range(len(free_agents) - 1, 0, -1):
            j = secrets.randbelow(i + 1)
            free_agents[i], free_agents[j] = free_agents[j], free_agents[i]

        # Casino step 2: stable sort by least served today
        free_agents.sort(key=lambda a: a.get("chats_served_today", 0))
        selected = free_agents[0]

        # Increment daily counter (1 DB call — uses data already in memory)
        await db.increment_chats_served(selected)

        agent_name = selected.get("name") or selected.get("email", "A specialist")
        logger.info(
            f"[routing] Assigned to {agent_name} "
            f"(served {selected.get('chats_served_today', 0)} today) "
            f"for tunnel={tunnel}"
        )
        return {"agent_id": selected["id"], "mode": "human", "agent_name": agent_name, "reason": "dispatch"}

    except Exception as e:
        logger.error(f"[routing] Unexpected error: {e} → fallback AI")
        return {"agent_id": None, "mode": "ai", "agent_name": None}


# ── Human dispatch: needs_agent gets DRAINED ─────────────────────────────
# The queue flag was set in two places and consumed in zero — a shelf label.
# This dispatcher turns it into an actual assignment: ready operator found →
# the SAME unified handoff path the manual Take button uses; none ready →
# the existing super-alert. The heartbeat/notify-assignment infra in the
# panel already rings on new assignments — no new notification framework.

async def dispatch_needs_agent(conversation_id: str) -> bool:
    """Try to hand a needs_agent conversation to a ready operator.

    Returns True when an operator was assigned. Concurrency-safe: the
    needs_agent → active status flip is a conditional update; whichever
    dispatch wins the flip performs the handoff, the loser walks away —
    a conversation can never be double-assigned.
    """
    try:
        conv = await db.get_conversation_simple(conversation_id)
        if not conv or conv.get("status") != "needs_agent":
            return False
        if conv.get("assigned_agent_id"):
            return False

        tunnel = conv.get("tunnel") or "sales"

        class _V:  # minimal visitor shape for route_conversation's sticky check
            name = conv.get("visitor_name")
            email = conv.get("visitor_email")
            phone = conv.get("visitor_phone")

        route = await route_conversation(
            tunnel, visitor=_V, visitor_id=conv.get("visitor_id")
        )

        if not route or not route.get("agent_id"):
            # Nobody ready — the demand signal stays queued and the existing
            # super-alert path fires exactly as it does today.
            try:
                from app.services.closing import claim_super_alert
                from app.services.email import send_super_alert_email

                if await claim_super_alert(
                    conversation_id, settings.super_alert_cooldown_minutes
                ):
                    await send_super_alert_email(
                        conversation_id=conversation_id,
                        visitor_name=conv.get("visitor_name"),
                        visitor_phone=conv.get("visitor_phone"),
                        visitor_email=conv.get("visitor_email"),
                        tunnel=tunnel,
                        last_message="(queued: client asked for a human agent)",
                        chat_number=conv.get("chat_number"),
                    )
            except Exception as e:
                logger.warning(f"[dispatch] super-alert failed for {conversation_id}: {e}")
            logger.info(f"[dispatch] {conversation_id}: no ready operators — stays queued")
            return False

        # Conditional claim: only the dispatch that flips needs_agent → active
        # proceeds. rows==0 means someone else (dispatch or manual Take) won.
        client = db.get_client()
        res = await db._run_sync(
            lambda: client.table("conversations")
            .update({"status": "active"})
            .eq("id", conversation_id)
            .eq("status", "needs_agent")
            .execute()
        )
        if not res.data:
            logger.info(f"[dispatch] {conversation_id}: lost the claim race — skipping")
            return False

        try:
            from app.services.handoff import perform_handoff_to_agent

            await perform_handoff_to_agent(
                conversation_id,
                agent_id=route["agent_id"],
                agent_name=route.get("agent_name", "A specialist"),
                tunnel=tunnel,
                emit_messages=False,
                handoff_reason="auto_assign",
            )
        except Exception:
            # The handoff itself failed — put the demand signal back on the
            # shelf so a later dispatch (or /open self-heal) retries it.
            try:
                await db.update_conversation(conversation_id, {"status": "needs_agent"})
            except Exception:
                pass
            raise
        logger.info(
            f"[dispatch] {conversation_id} → {route.get('agent_name')} "
            f"({str(route['agent_id'])[:8]}...) via auto_assign"
        )
        return True
    except Exception as e:
        logger.error(f"[dispatch] failed for {conversation_id}: {e}", exc_info=True)
        return False
