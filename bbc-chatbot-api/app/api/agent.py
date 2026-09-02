"""Agent presence — heartbeat endpoint, and the panel's SSE stream."""
import asyncio
import json
import logging
import time
from collections import deque as _deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.db import supabase as db
from app.pipeline.orchestrator import _fire_and_forget
from app.security.auth import get_current_user
from config.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_HANDOFF_COOLDOWN_SECONDS = 120

# The heartbeat is the hottest endpoint in the system (every agent, every 5s).
# Its latency is the earliest signal of executor saturation — it climbs minutes
# before the panel shows "Couldn't load". Per-instance, like CRM_PUSH_HEALTH.
HEARTBEAT_HEALTH: dict = {"calls": 0, "latency_ms": _deque(maxlen=1000)}


# Rows the deadline sweep cannot judge. A conversation assigned to an ONLINE
# agent whose agent_assigned_at is missing or unparseable is invisible to both
# passes: this one skips it, and the stale pass only looks at OFFLINE agents.
# It would hang forever — and every hung chat is one more row in every sweep.
# Counted rather than silently skipped: if this stays 0 the concern is
# theoretical; if it climbs, the metadata repair is its own ticket.
DEADLINE_HEALTH: dict = {"skipped_no_assigned_at": 0, "skipped_bad_timestamp": 0}


def heartbeat_health_snapshot() -> dict:
    lat = sorted(HEARTBEAT_HEALTH["latency_ms"])

    def _pct(p: float) -> float:
        return round(lat[min(len(lat) - 1, int(len(lat) * p))], 1) if lat else 0.0

    return {"calls": HEARTBEAT_HEALTH["calls"], "p50_ms": _pct(0.5), "p95_ms": _pct(0.95)}


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
    """Fall back conversations whose assigned agent never replied within the
    first-response deadline.

    ONE query for the whole set; the loop is pure memory. The previous version
    issued has_agent_message_since() for EVERY active human conversation before
    checking any deadline, and ran on every heartbeat from every agent —
    O(agents × open chats) per 5s, ~290 queries/s at 35 agents × 40 chats. That
    product is what took the panel down on 31 Aug.

    `last_agent_message_at` (migration 024, updated on every agent message)
    answers "has the agent replied since assignment" without a query. The old
    `deadline_engaged` branch is gone: an agent who HAD replied was skipped
    unconditionally right after, so that timeout never decided anything.

    The "operator is viewing this conversation → 120s" extension is read from
    the joined users row (035), honoured only while the operator's last_seen_at
    is fresh (<30s) so a closed tab cannot shield a conversation forever. The
    two parameters are kept for callers/tests; when given they take precedence.

    Returns number of conversations fallen back."""
    from config.settings import settings
    from app.services.handoff import fall_back_to_ai

    convs = await db.get_active_human_conversations()
    count = 0
    now = datetime.now(timezone.utc)
    deadline_first = timedelta(seconds=settings.agent_first_response_timeout_seconds)
    viewing_grace = timedelta(seconds=120)
    fresh_window = timedelta(seconds=30)

    def _parse(ts, *, count_bad: bool = False):
        if not ts:
            return None
        try:
            return datetime.fromisoformat(
                ts.replace("Z", "+00:00") if isinstance(ts, str) else ts
            )
        except (ValueError, TypeError):  # noqa: silent — every None here fails SAFE and the one that could hang a chat is COUNTED in DEADLINE_HEALTH: an unparseable agent_assigned_at skips the row, an unparseable last_agent_message_at reads as "never replied" so the deadline still fires, an unparseable last_seen_at just loses the 120s viewing courtesy
            if count_bad:
                DEADLINE_HEALTH["skipped_bad_timestamp"] += 1
            return None

    for conv in convs:
        meta = conv.get("metadata") or {}
        assigned_at = _parse(meta.get("agent_assigned_at"), count_bad=True)
        if not assigned_at:
            # Pre-PR3 assignment, or metadata we cannot read. The stale pass
            # only catches OFFLINE agents, so a row like this sitting on an
            # ONLINE agent is invisible to both passes and hangs. Skipping is
            # the safe action (we will not fall back a conversation we cannot
            # reason about) — but it is counted, not silent.
            DEADLINE_HEALTH["skipped_no_assigned_at"] += 1
            continue

        last_agent = _parse(conv.get("last_agent_message_at"))
        if last_agent and last_agent >= assigned_at:
            continue  # engaged agent → stale cleanup handles offline/silence

        # Viewing extension: explicit params (tests/legacy) or the joined user row.
        agent_row = conv.get("users") or {}
        if isinstance(agent_row, list):
            agent_row = agent_row[0] if agent_row else {}
        is_viewing = False
        if viewing_conversation_id:
            is_viewing = (
                conv["id"] == viewing_conversation_id
                and conv.get("assigned_agent_id") == viewing_user_id
            )
        else:
            last_seen = _parse(agent_row.get("last_seen_at"))
            is_viewing = (
                agent_row.get("viewing_conversation_id") == conv["id"]
                and last_seen is not None
                and now - last_seen < fresh_window
            )
        timeout = viewing_grace if is_viewing else deadline_first

        if now - assigned_at < timeout:
            continue
        await fall_back_to_ai(conv["id"])
        count += 1
    return count


async def _cleanup_stale_conversations(
    viewing_conversation_id: str | None = None,
    viewing_user_id: str | None = None,
) -> int:
    """Revert conversations from offline agents back to AI mode, then enforce
    the first-response deadline.

    Called from the scheduler (run_agent_sweep) ONCE per interval. It used to
    be called from every heartbeat — "each online agent helps clean up" — which
    meant 35 agents ran the identical sweep seven times a second."""
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
    # The presence write carries what the operator is looking at (035) so the
    # scheduler sweep can honour the "operator is viewing → 120s" extension
    # without this endpoint doing the sweep itself.
    _t0 = time.monotonic()
    await db.update_user_last_seen(
        user_id, viewing_conversation_id=body.viewing_conversation_id
    )
    from app.services.presence import record_presence_tick
    _fire_and_forget(record_presence_tick(user_id, db))
    # The stale/deadline sweep used to run HERE — on every heartbeat, from every
    # agent. It now runs once, in the scheduler (run_agent_sweep). `cleaned`
    # stays in the response for one cycle of client compatibility; nothing
    # reads it. Removed in QUEUE-CLEANUP.
    cleaned = 0
    assigned = 0
    active_assigned = 0
    # SECURITY: role, readiness AND tunnel_scope all from DB, never from JWT.
    # A stale token (e.g. admin still carrying an old sales token) was
    # receiving auto-assigns in production. Fail closed on any miss.
    user_db = await db.get_user_by_id(user_id)
    role_db = (user_db or {}).get("role") or ""
    is_ready = bool((user_db or {}).get("is_ready", False))
    # 033: chat_enabled comes from the DB too, never from the JWT. An absent
    # column (migration not applied yet) reads as True — see supabase.py.
    chat_enabled = bool((user_db or {}).get("chat_enabled", True))
    # D4: OFF with the shared queue — an operator must not receive a
    # conversation because their browser pinged first. Behind the flag for one
    # iteration as a rollback path; deleted in QUEUE-CLEANUP.
    if (
        settings.auto_dispatch_enabled
        and user_db
        and role_db in db._OPERATOR_ROLES
        and is_ready
        and chat_enabled
    ):
        agent_name = user_db.get("name") or user_db.get("email") or "A specialist"
        assigned = await _assign_pending_conversations(
            user_id,
            user_db.get("tunnel_scope") or "sales",
            agent_name,
        )
        active_assigned = await db.get_agent_active_count(user_id)
    # --- shared queue, riding on the heartbeat (spec v2.4 §6.7) ---
    # Zero new HTTP requests: a separate poll at 10 operators would be ~120
    # queries/minute on the hottest table. Nothing is computed for someone who
    # has no right to it: 0 and [] AT THE SOURCE, not filtered later in the UI.
    _queue_count = 0
    _queue_ids: list = []
    _queue_oldest = None
    try:
        _qa_ok = (
            user_db
            and bool(user_db.get("is_active", True))
            and bool(user_db.get("chat_enabled", True))
            and role_db in db._OPERATOR_ROLES
        )
        if _qa_ok:
            _tunnels = (
                ["sales", "support"]
                if (user_db.get("tunnel_scope") or "sales") == "all"
                else [user_db.get("tunnel_scope") or "sales"]
            )
            _rows: list = []
            for _t in _tunnels:
                _rows.extend(
                    await db.get_queue_for_operator(
                        tunnel=_t,
                        team_id=user_db.get("team_id"),
                        viewer_agent_id=user_id,
                    )
                )
            _queue_count = len(_rows)
            _queue_ids = [r["id"] for r in _rows][:20]
            # The OLDEST waiting conversation — by queued_at, not by list
            # order: the queue sorts needs_agent first, so _rows[0] is the
            # loudest, not the longest-waiting. The CRM shows this one in its
            # notification, and its age decides how long they keep reminding.
            _oldest_row = min(
                (r for r in _rows if r.get("queued_at")),
                key=lambda r: str(r["queued_at"]),
                default=None,
            )
            if _oldest_row:
                from datetime import datetime, timezone
                _age = None
                try:
                    _age = int(
                        (
                            datetime.now(timezone.utc)
                            - datetime.fromisoformat(
                                str(_oldest_row["queued_at"]).replace("Z", "+00:00")
                            )
                        ).total_seconds()
                    )
                except (ValueError, TypeError):  # noqa: silent — a malformed queued_at reads as unknown age; the notification still names the conversation
                    _age = None
                # The route the pipeline already extracted — never the
                # client's own words (see ChatBridgeMessage: the text would go
                # into a desktop notification that stays up until touched).
                # ADAPTATION vs the brief: the route does NOT live in
                # conversations.metadata — it lives on the leads table
                # (origin_code / destination_code, written by lead_service).
                # One targeted read-only select, only while a queue exists;
                # get_or_create_lead is NOT used here because it CREATES.
                _from = _to = None
                try:
                    _lead_res = await db._run_sync(
                        lambda: db.get_client()
                        .table("leads")
                        .select("origin_code, destination_code")
                        .eq("conversation_id", _oldest_row["id"])
                        .limit(1)
                        .execute()
                    )
                    _lead_rows = _lead_res.data or []
                    if _lead_rows:
                        _from = _lead_rows[0].get("origin_code")
                        _to = _lead_rows[0].get("destination_code")
                except Exception as _e:
                    logger.warning(
                        f"[heartbeat] route lookup failed for queue_oldest: {_e} "
                        f"— notification goes out without a route"
                    )
                _queue_oldest = {
                    "id": _oldest_row["id"],
                    "waiting_seconds": _age,
                    "route": f"{_from} → {_to}" if (_from and _to) else None,
                }
    except Exception as e:
        # The queue must never break the heartbeat: presence is more important
        # than the badge. Logged, never silent.
        logger.warning(f"[heartbeat] queue fetch failed for {user_id}: {e}")
    HEARTBEAT_HEALTH["latency_ms"].append((time.monotonic() - _t0) * 1000.0)
    HEARTBEAT_HEALTH["calls"] += 1
    return {
        "success": True,
        "cleaned": cleaned,
        "assigned": assigned,
        "active_assigned": active_assigned,
        "is_ready": is_ready,
        "role": role_db,
        "queue_count": _queue_count,
        "queue_ids": _queue_ids,
        "queue_oldest": _queue_oldest,
    }


@router.get("/agent/stream/{conversation_id}")
async def agent_stream(
    conversation_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """SSE for the operator panel: message / typing / presence for ONE
    conversation — the one on screen. Replaces three polls (500ms/2s/2s).

    Same generator shape as the widget stream; same fan-out. Authorisation
    mirrors GET /conversations/{id}: tunnel gate, and a supervisor/PM outside
    the conversation's team gets nothing (403), not a metadata-only stream —
    events carry message text.

    The panel authenticates with the Bearer header via fetch()+ReadableStream,
    not EventSource: EventSource cannot set headers and a session JWT in a
    query string lands in every proxy log."""
    conv = await db.get_conversation_simple(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    from app.api.conversations import _enforce_tunnel, _resolve_team_scope
    _enforce_tunnel(user, conv.get("tunnel"))
    if user.get("role") in ("supervisor", "project_manager"):
        team_ids = await _resolve_team_scope(user)
        if not team_ids or conv.get("team_id") not in team_ids:
            raise HTTPException(status_code=403, detail="Outside your team")

    from app.realtime.manager import manager

    async def event_generator():
        queue = await manager.connect(conversation_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:  # noqa: silent — not a failure: 25s of quiet is the normal state of an open chat, and the keepalive below is the whole point of the timeout (proxies close idle connections)
                    yield ": keepalive\n\n"
        finally:
            manager.disconnect(conversation_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


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
