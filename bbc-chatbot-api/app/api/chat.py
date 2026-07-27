"""POST /api/chat — Main chat endpoint.
Thin controller: validate → sanitize → orchestrate → respond.
"""

import logging
import uuid as _uuid_mod

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from config.settings import settings
from app.models.chat import ChatRequest, ChatResponse, VisitorInfo
from app.security.input_sanitizer import sanitize_message, is_suspicious
from app.security.rate_limiter import check_rate_limit
from app.pipeline.orchestrator import process_message, _fire_and_forget
from app.db import supabase as db
from app.realtime.manager import manager
from app.services import conversation_service, lead_service
from app.services.routing import route_conversation
from app.services.handoff import perform_handoff_to_agent

from pydantic import BaseModel, Field

from app.deps.ownership import require_visitor_ownership
from app.services.blocklist import is_blocked, REFUSAL_MESSAGE as BLOCK_REFUSAL

logger = logging.getLogger(__name__)


class TypingBody(BaseModel):
    text: str = ""


router = APIRouter()


# --- Init endpoint: pre-create conversation for SSE pre-connect ---

class ChatInitRequest(BaseModel):
    tunnel: str = Field(default="sales", pattern="^(sales|support)$")
    visitor: VisitorInfo = Field(default_factory=VisitorInfo)
    metadata: Optional[dict] = None
    visitor_id: Optional[str] = None


class ChatInitResponse(BaseModel):
    conversation_id: str


@router.post("/chat/init", response_model=ChatInitResponse)
async def chat_init(
    payload: ChatInitRequest,
    request: Request,
    _rate: None = Depends(check_rate_limit),
):
    """Create conversation instantly for SSE pre-connect. No pipeline."""
    _meta = dict(payload.metadata or {})
    _client_ip = (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or (request.client.host if request.client else "")
    )
    if _client_ip:
        _meta.setdefault("client_ip", _client_ip)
    _ua = request.headers.get("user-agent")
    if _ua:
        _meta.setdefault("user_agent", _ua)
    _referer = request.headers.get("referer") or request.headers.get("referrer")
    if _referer:
        _meta.setdefault("referrer", _referer)
    if payload.visitor_id:
        _meta.setdefault("visitor_id", payload.visitor_id)

    # Blocklist — identity chokepoint. Only phone/email refuse; a blocked IP
    # alone never does (shared IPs would catch innocent visitors).
    if await is_blocked(
        phone=getattr(payload.visitor, "phone", None),
        email=getattr(payload.visitor, "email", None),
    ):
        logger.warning("[init] Blocked visitor refused")
        raise HTTPException(status_code=403, detail=BLOCK_REFUSAL)

    conv = await conversation_service.get_or_create_conversation(
        conversation_id=None,
        tunnel=payload.tunnel,
        visitor=payload.visitor,
        visitor_id=payload.visitor_id,
        metadata=_meta or None,
    )
    if not conv or "id" not in conv:
        raise HTTPException(status_code=500, detail="Failed to create conversation")

    conv_id = conv["id"]
    logger.info(f"[init] Conv {conv_id} created/found for SSE pre-connect")
    return ChatInitResponse(conversation_id=conv_id)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    request: Request,
    _rate: None = Depends(check_rate_limit),
) -> ChatResponse:
    """Handle a single chat message from the widget."""

    # 0. Validate conversation_id format if provided — widgets from a prior
    # backend bug may persist literal 'unknown' or other non-UUID values.
    if req.conversation_id is not None:
        try:
            _uuid_mod.UUID(req.conversation_id)
        except (ValueError, AttributeError, TypeError):
            logger.warning(
                f"[chat] Rejecting non-UUID conversation_id from widget: "
                f"{req.conversation_id!r}"
            )
            raise HTTPException(
                status_code=422,
                detail="Invalid conversation_id format",
            )

    # 1. Sanitize
    clean_message = sanitize_message(req.message)
    if is_suspicious(req.message):
        logger.warning(f"Suspicious message detected (conv={req.conversation_id})")

    # 1.5. Blocklist — identity chokepoint on the contact the widget sends.
    # Phone/email only; a blocked IP alone never refuses (shared IPs).
    if await is_blocked(
        phone=getattr(req.visitor, "phone", None),
        email=getattr(req.visitor, "email", None),
    ):
        logger.warning(f"[chat] Blocked visitor refused (conv={req.conversation_id})")
        raise HTTPException(status_code=403, detail=BLOCK_REFUSAL)

    # 2. Check message count limit
    if req.conversation_id:
        count = await db.count_messages(req.conversation_id)
        if count >= settings.max_messages_per_conversation:
            raise HTTPException(
                status_code=429,
                detail="Conversation message limit reached. Please start a new conversation.",
            )

    # 2.5. Reopen closed conversation if client writes again within session window
    if req.conversation_id:
        conv_info = await db.get_conversation_simple(req.conversation_id)
        # Blocklist — contact already stored on the conversation (the visitor
        # submitted it earlier and may now send messages with an empty visitor
        # payload). Reuses the row already fetched above: no extra roundtrip.
        if conv_info and await is_blocked(
            phone=conv_info.get("visitor_phone"),
            email=conv_info.get("visitor_email"),
        ):
            logger.warning(
                f"[chat] Blocked visitor refused (stored contact, conv={req.conversation_id})"
            )
            raise HTTPException(status_code=403, detail=BLOCK_REFUSAL)
        if conv_info and conv_info.get('status') in ('closed', 'completed'):
            # 'completed' = post-CRM, never reopen
            if conv_info.get('status') == 'completed':
                _ack = "Thank you! Our consultant will reach out shortly."
                await conversation_service.add_message(
                    req.conversation_id, "ai", _ack,
                    model_used="template", cost=0.0,
                )
                return ChatResponse(
                    conversation_id=req.conversation_id,
                    message=_ack, type="post_sale", model_used="template",
                )
            _meta = dict(conv_info.get("metadata") or {})
            # Don't reopen post-sale conversations — send polite ack instead
            if _meta.get("closing_sent_at"):
                _ack = "Thank you! Our consultant will reach out shortly."
                await conversation_service.add_message(
                    req.conversation_id, "ai", _ack,
                    model_used="template", cost=0.0,
                )
                return ChatResponse(
                    conversation_id=req.conversation_id,
                    message=_ack, type="post_sale", model_used="template",
                )
            logger.info(f"[reopen] Conv {req.conversation_id} closed — client wrote again, reopening")
            reopen_mode = "human" if conv_info.get("assigned_agent_id") else "ai"
            await db.update_conversation(req.conversation_id, {
                'status': 'active',
                'mode': reopen_mode,
            })
            # Fall through: if assigned agent exists, message is queued for human mode.

    # ── POST-CRM: template response + re-close (no handoff) ──
    # Intercept ONLY when collection is COMPLETE. created_in_crm means
    # "submitted early so the consultant can call" — NOT "done talking".
    # If route/dates/pax are still missing, fall through to the pipeline
    # so the bot keeps collecting (real data lands in Supabase + admin
    # panel; the CRM record was created with defaults by design).
    if req.conversation_id:
        _lead = await lead_service.get_or_create_lead(req.conversation_id)
        if _lead and _lead.get("created_in_crm"):
            from app.models.lead import get_missing_fields as _gmf_postcrm
            _conv_row = await db.get_conversation_simple(req.conversation_id) or {}
            _contact_ctx = {
                "visitor_name": _conv_row.get("visitor_name")
                    or getattr(req.visitor, "name", None),
                "visitor_email": _conv_row.get("visitor_email")
                    or getattr(req.visitor, "email", None),
                "visitor_phone": _conv_row.get("visitor_phone")
                    or getattr(req.visitor, "phone", None),
            }
            _still_collecting = bool(_gmf_postcrm(_lead, _contact_ctx))
            # Don't close with template if client hasn't confirmed summary.
            # Abandoned cron may submit CRM without confirmation; client
            # returning with "yes" should go through pipeline, not template.
            if not _still_collecting and not (_conv_row.get("metadata") or {}).get("confirmed_at"):
                _still_collecting = True
            _post_crm_mode = await db.get_conversation_mode(req.conversation_id)
            if _post_crm_mode == "ai" and not _still_collecting:
                from app.services.closing import compute_closing_text, has_closing_been_sent, claim_closing_sent
                _meta = dict(_conv_row.get("metadata") or {})

                # Save user message
                await conversation_service.add_message(
                    conversation_id=req.conversation_id,
                    role="user",
                    content=clean_me,)

                if has_closing_been_sent(_meta):
                    # Post-sale: closing already sent — discriminate message type
                    _msg_lower = clean_message.strip().lower().rstrip(".!,")
                    _polite = _msg_lower in (
                        "thank you", "thanks", "ok", "great", "perfect",
                        "yes", "bye", "goodbye", "awesome", "sounds good",
                        "appreciate it", "thx", "ty",
                    )
                    if _polite:
                        _ack = "You're welcome! Our team will be in touch shortly."
                    else:
                        _ack = (
                            "Noted — we'll include that in your request. "
                            "For any changes, please call "
                            + compute_closing_text(_meta.get("site")).split("call ")[-1].rstrip(".")
                            + "."
                        )
                    await conversation_service.add_message(
                        req.conversation_id, "ai", _ack,
                        model_used="template", cost=0.0,
                    )
                    await db.update_conversation(req.conversation_id, {
                        "status": "completed",
                        "closed_at": datetime.now(timezone.utc).isoformat(),
                        "mode": "ai",
                        "assigned_agent_id": None,
                    })
                    return ChatResponse(
                        conversation_id=req.conversation_id,
                        message=_ack, type="post_sale", model_used="template",
                    )

                # First post-CRM closing (PR-A claim handles dedup)
                _claimed = await claim_closing_sent(req.conversation_id)
                _closing = compute_closing_text(_meta.get("site"))
                _msg = _closing if _claimed else "You're all set! Our consultant will reach out shortly."
                await conversation_service.add_message(
                    req.conversation_id, "ai", _msg,
                    model_used="template", cost=0.0,
                )
                await db.update_conversation(req.conversation_id, {
                    "status": "completed",
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "ai",
                    "assigned_agent_id": None,
                })
                logger.info(f"[{req.conversation_id}] Post-CRM: template + re-close (no handoff)")
                return ChatResponse(
                    conversation_id=req.conversation_id,
                    message=_msg, type="closing", model_used="template",
                )

    # 3. If existing conversation in 'human' mode
    if req.conversation_id:
        mode = await db.get_conversation_mode(req.conversation_id)
        if mode == "human":
            # H1: Check if assigned agent is effectively offline before queuing
            from app.services.handoff import is_agent_effectively_offline, fall_back_to_ai
            # FIX-A: Silent reservation (announce_pending) — the operator was auto-assigned
            # but has NOT engaged yet. The orchestrator is designed to let AI keep serving
            # until the operator sends their first real message. Do NOT queue here (that
            # returned an ephemeral "One moment…" and skipped the pipeline, killing the
            # visitor's message). Fall through to the AI pipeline; if the operator engages
            # mid-flight, the orchestrator's mode=='human' guard discards the AI response.
            _conv_res = await db.get_conversation_simple(req.conversation_id)
            _is_silent_reservation = bool(
                ((_conv_res or {}).get("metadata") or {}).get("announce_pending")
            )
            if _is_silent_reservation:
                pass  # fall through to the AI pipeline below (Step 3.5+)
            elif await is_agent_effectively_offline(req.conversation_id):
                logger.info(
                    f"[H1] Conv {req.conversation_id}: agent offline in human mode "
                    f"→ falling back to AI"
                )
                from app.services.conversation_service import add_message
                await add_message(
                    conversation_id=req.conversation_id,
                    role="user",
                    content=clean_message,
                )
                await fall_back_to_ai(req.conversation_id)
                from app.services.handoff import _FALLBACK_MSG
                return ChatResponse(
                    conversation_id=req.conversation_id,
                    message=_FALLBACK_MSG,
                    type="fallback",
                    model_used="none",
                )
            else:
                # Agent is still online — queue the message for human handling
                from app.services.conversation_service import add_message
                await add_message(
                    conversation_id=req.conversation_id,
                    role="user",
                    content=clean_message,
                )
                from app.services.handoff import _handoff_phrase_recently_sent

                already = await _handoff_phrase_recently_sent(req.conversation_id)
                queued_msg = (
                    "Your message has been sent to the specialist."
                    if already
                    else "One moment please, connecting you with a specialist..."
                )
                return ChatResponse(
                    conversation_id=req.conversation_id,
                    message=queued_msg,
                    type="queued",
                    model_used="none",
                )

    # ── First-message routing: operator-first, AI fallback ──
    # Visitor writes → is an operator free right now?
    #   YES → handoff immediately (live-chat from message 1)
    #   NO  → fall-through to AI pipeline (collect → CRM → closed)
    # Restored from #70 (disabled in #61 due to phantom agents);
    # now safe: 8-min deadline (#101), notifications (#102),
    # persist-fallback (#103), internal scheduler (#104).
    # Gate: first user message on a fresh AI conversation.
    if req.conversation_id:
        _conv_check = await db.get_conversation(req.conversation_id)
        if (
            _conv_check
            and _conv_check.get("mode") == "ai"
            and not _conv_check.get("assigned_agent_id")
        ):
            _msg_count = await db.count_messages(req.conversation_id)
            if _msg_count == 0:  # first user message — not yet saved
                _route = await route_conversation(
                    req.tunnel,
                    visitor=req.visitor,
                    visitor_id=req.visitor_id,
                )
                if _route and _route.get("agent_id"):
                    await perform_handoff_to_agent(
                        conversation_id=req.conversation_id,
                        agent_id=_route["agent_id"],
                        agent_name=_route.get("agent_name", "A specialist"),
                        tunnel=req.tunnel,
                        emit_messages=False,
                        handoff_reason="first_message",
                    )
                    logger.info(
                        f"[{req.conversation_id}] First-message silent handoff → "
                        f"{_route.get('agent_name')} (30s to respond, AI pipeline continues)"
                    )
                    # Fall through — pipeline saves user message and generates AI greeting.
                else:
                    logger.info(
                        f"[{req.conversation_id}] First-message routing → "
                        f"no operator, AI pipeline"
                    )
                    # Management rule (Tyke, 02-Jul): email super@ whenever a
                    # client starts chatting and NO agents are logged in —
                    # not only on explicit handoff. Distinct from "all busy":
                    # busy agents are logged in and see the chat, so no email.
                    # Fire-and-forget + 24h throttle (claim_super_alert).
                    _cid_alert = req.conversation_id
                    _tunnel_alert = req.tunnel
                    _visitor_alert = req.visitor
                    _first_msg_alert = clean_message

                    async def _send_first_message_super_alert():
                        from app.services.closing import claim_super_alert
                        from app.services.email import send_super_alert_email

                        online = await db.get_available_agents(
                            tunnel=_tunnel_alert,
                            timeout_seconds=settings.agent_timeout_seconds,
                        )
                        if online:
                            return  # agents logged in (just busy) — no alert
                        claimed = await claim_super_alert(
                            _cid_alert, settings.super_alert_cooldown_minutes
                        )
                        if claimed:
                            await send_super_alert_email(
                                conversation_id=_cid_alert,
                                visitor_name=getattr(_visitor_alert, "name", None),
                                visitor_phone=getattr(_visitor_alert, "phone", None),
                                visitor_email=getattr(_visitor_alert, "email", None),
                                tunnel=_tunnel_alert,
                                last_message=_first_msg_alert,
                            )

                    _fire_and_forget(_send_first_message_super_alert())

    # 4. AI mode or new conversation → run pipeline
    # Enrich metadata with client IP + User-Agent
    _meta = dict(req.metadata or {})
    _client_ip = (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or (request.client.host if request.client else "")
    )
    if _client_ip:
        _meta.setdefault("client_ip", _client_ip)
    _ua = request.headers.get("user-agent")
    if _ua:
        _meta.setdefault("user_agent", _ua)
    _referer = request.headers.get("referer") or request.headers.get("referrer")
    if _referer:
        _meta.setdefault("referrer", _referer)
    if req.visitor_id:
        _meta.setdefault("visitor_id", req.visitor_id)

    # Response cache check (Sprint 3)
    from app.services.response_cache import get_cached, set_cached
    from app.pipeline.intent import detect_intent

    _cache_site = (_meta or {}).get("site", "bbc")
    _cached_response = get_cached(clean_message, site=_cache_site, tunnel=req.tunnel)
    if _cached_response and req.conversation_id:
        await conversation_service.add_message(
            conversation_id=req.conversation_id,
            role="user",
            content=clean_message,
        )
        await conversation_service.add_message(
            conversation_id=req.conversation_id,
            role="ai",
            content=_cached_response,
            model_used="cache",
            cost=0.0,
        )
        logger.info(f"[{req.conversation_id}] Cache HIT — skipped pipeline")
        return ChatResponse(
            conversation_id=req.conversation_id,
            message=_cached_response,
            streaming=False,
            type="ai",
            model_used="cache",
            cost=0.0,
        )

    response = await process_message(
        conversation_id=req.conversation_id,
        message=clean_message,
        tunnel=req.tunnel,
        visitor=req.visitor,
        metadata=_meta or None,
        visitor_id=req.visitor_id,
    )

    # Cache store for FAQ/greeting (Sprint 3) — skip streaming (message empty until SSE ends)
    _result_intent = detect_intent(clean_message, _meta).value
    if response.message and not response.streaming and _result_intent:
        set_cached(
            clean_message,
            response.message,
            _result_intent,
            site=_cache_site,
            tunnel=req.tunnel,
        )

    return response


# ── Typing indicator endpoints (public — widget reports typing status) ─────

@router.post("/chat/typing/{conversation_id}")
async def set_typing_status(
    conversation_id: str,
    body: TypingBody,
    _owner: None = Depends(require_visitor_ownership),
):
    """Widget reports client is actively typing.
    Stored in Redis with 10s TTL — never saved to DB.
    Called every ~500ms while client types (debounced on client side)."""
    from app.realtime.typing_indicator import typing_manager
    text = body.text.strip()
    if text:
        await typing_manager.set_typing(conversation_id, text)
    else:
        await typing_manager.clear_typing(conversation_id)
    return {"success": True}


@router.delete("/chat/typing/{conversation_id}")
async def clear_typing_status(
    conversation_id: str,
    _owner: None = Depends(require_visitor_ownership),
):
    """Widget reports client sent message or cleared input."""
    from app.realtime.typing_indicator import typing_manager
    await typing_manager.clear_typing(conversation_id)
    return {"success": True}


@router.post("/chat/session/{conversation_id}/open")
async def mark_chat_session_open(
    conversation_id: str,
    _owner: None = Depends(require_visitor_ownership),
):
    """Client opened widget chat UI but did not necessarily send a message yet."""
    conv = await db.get_conversation(conversation_id)
    if not conv:
        return {"success": False, "data": None}
    metadata = dict(conv.get("metadata") or {})
    metadata["widget_open"] = True
    metadata["widget_presence"] = "online"
    metadata["widget_last_event"] = "open"
    metadata["widget_last_event_at"] = datetime.now(timezone.utc).isoformat()
    await db.update_conversation(conversation_id, {"metadata": metadata})
    return {"success": True}


@router.post("/chat/session/{conversation_id}/close")
async def mark_chat_session_close(
    conversation_id: str,
    reason: str = Query("minimized", pattern="^(minimized|left)$"),
    _owner: None = Depends(require_visitor_ownership),
):
    """Client stopped active chat session.

    reason=minimized -> chat collapsed with X while still on page
    reason=left -> browser tab/page closed or navigated away
    """
    conv = await db.get_conversation(conversation_id)
    if not conv:
        return {"success": False, "data": None}
    metadata = dict(conv.get("metadata") or {})
    metadata["widget_open"] = False
    metadata["widget_presence"] = reason
    metadata["widget_last_event"] = "close"
    metadata["widget_last_close_reason"] = reason
    metadata["widget_last_event_at"] = datetime.now(timezone.utc).isoformat()
    await db.update_conversation(conversation_id, {"metadata": metadata})
    from app.realtime.typing_indicator import typing_manager
    await typing_manager.clear_typing(conversation_id)
    return {"success": True}


# ── Public widget endpoints (no auth required — conv_id UUID is the secret) ──

@router.get("/chat/visitor/{visitor_id}/active-conversation")
async def get_active_conversation_for_visitor(visitor_id: str):
    """Widget calls this on mount to find a visitor's active conversation.
    Returns conversation_id if one exists, else null — so the widget can
    reconnect across tabs/sessions without a stale localStorage ID."""
    try:
        client = db.get_client()
        res = await db._run_sync(
            lambda: client.table("conversations")
            .select("id")
            .eq("visitor_id", visitor_id)
            .eq("status", "active")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        conv_id = res.data[0]["id"] if res.data else None
        return {"success": True, "conversation_id": conv_id}
    except Exception as e:
        logger.error(f"get_active_conversation_for_visitor error: {e}")
        return {"success": True, "conversation_id": None}


@router.get("/chat/status/{conversation_id}")
async def public_get_conversation_status(conversation_id: str):
    """Session restore check for widget. Returns only status and mode.
    Fixes the 401 bug where widget incorrectly reset sessions on every open."""
    try:
        _uuid_mod.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        return {"success": False, "data": None}
    try:
        conv = await db.get_conversation(conversation_id)
        if not conv:
            return {"success": False, "data": None}
        return {
            "success": True,
            "data": {
                "status": conv.get("status"),
                "mode": conv.get("mode"),
            },
        }
    except Exception as e:
        logger.error(f"public_get_conversation_status error: {e}")
        return {"success": False, "data": None, "error": str(e)}


@router.get("/chat/messages/{conversation_id}")
async def public_get_messages(
    conversation_id: str,
    after: Optional[str] = Query(None),
    _owner: None = Depends(require_visitor_ownership),
):
    """Public incremental message polling for widget.
    Used as SSE fallback and for catch-up after reconnect."""
    try:
        _uuid_mod.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        return {"success": False, "data": [], "error": "invalid conversation_id"}
    try:
        msgs = await db.get_messages_after(conversation_id, after)
        return {"success": True, "data": await _with_agent_identity(conversation_id, msgs)}
    except Exception as e:
        logger.error(f"public_get_messages error: {e}")
        return {"success": False, "data": [], "error": str(e)}


async def _with_agent_identity(conversation_id: str, msgs: list) -> list:
    """Attach the operator's name + avatar to operator messages.

    The messages table stores no identity columns, so live SSE pushes carry it
    and this read path re-attaches it — that's what makes the photo and name
    survive a widget reload. Only role='agent' rows are touched; AI and system
    messages stay exactly as they are. One lookup per batch, and only when the
    batch actually contains an operator message (incremental polls usually
    return nothing).
    """
    if not any((m.get("role") == "agent") for m in msgs):
        return msgs
    identity = await db.get_conversation_agent_identity(conversation_id)
    if not identity:
        return msgs
    for m in msgs:
        if m.get("role") == "agent":
            m["agent_name"] = identity.get("name") or "Consultant"
            m["agent_avatar_url"] = identity.get("avatar_url")
    return msgs


@router.get("/chat/agent-typing/{conversation_id}")
async def public_get_agent_typing(
    conversation_id: str,
    _owner: None = Depends(require_visitor_ownership),
):
    """Widget polls this to show "<operator> is typing…".

    No operator login required (the conversation id plus visitor ownership is
    the guard, same as the other widget endpoints). Returns the operator's name
    only — the draft text never leaves the admin panel.
    """
    from app.realtime.typing_indicator import typing_manager
    state = await typing_manager.get_agent_typing(conversation_id)
    return {
        "success": True,
        "data": state or {"is_typing": False, "name": ""},
    }


@router.get("/chat/stream/{conversation_id}")
async def sse_stream(
    conversation_id: str,
    request: Request,
    _owner: None = Depends(require_visitor_ownership),
):
    """SSE real-time stream for widget.
    Pushes agent messages instantly (~50ms vs 1s polling).
    X-Accel-Buffering: no disables Railway/nginx proxy buffering — required for SSE.
    Widget falls back to polling if SSE fails 3 times."""
    try:
        _uuid_mod.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        return {"success": False, "error": "invalid conversation_id"}

    async def event_generator():
        queue = await manager.connect(conversation_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=25.0)
                    payload = json.dumps(msg)
                    msg_id = msg.get("id", "")
                    yield f"id: {msg_id}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive ping every 25s — prevents proxy from closing idle connections
                    yield ": keepalive\n\n"
        finally:
            manager.disconnect(conversation_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
