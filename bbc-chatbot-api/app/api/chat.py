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
from app.pipeline.orchestrator import process_message
from app.db import supabase as db
from app.realtime.manager import manager
from app.services import conversation_service, lead_service

from pydantic import BaseModel, Field

from app.deps.ownership import require_visitor_ownership

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
        if conv_info and conv_info.get('status') == 'closed':
            logger.info(f"[reopen] Conv {req.conversation_id} closed — client wrote again, reopening")
            reopen_mode = "human" if conv_info.get("assigned_agent_id") else "ai"
            await db.update_conversation(req.conversation_id, {
                'status': 'active',
                'mode': reopen_mode,
            })
            # Fall through: if assigned agent exists, message is queued for human mode.

    # ── POST-CRM: template response + re-close (no handoff) ──
    if req.conversation_id:
        _lead = await lead_service.get_or_create_lead(req.conversation_id)
        if _lead and _lead.get("created_in_crm"):
            _post_crm_mode = await db.get_conversation_mode(req.conversation_id)
            if _post_crm_mode == "ai":
                # Save user message
                await conversation_service.add_message(
                    conversation_id=req.conversation_id,
                    role="user",
                    content=clean_message,
                )
                # Respond with closing template — no handoff, no routing
                _post_crm_msg = settings.post_crm_closing_message
                await conversation_service.add_message(
                    conversation_id=req.conversation_id,
                    role="ai",
                    content=_post_crm_msg,
                    model_used="template",
                    cost=0.0,
                )
                # Re-close conversation (client reopened it by writing)
                await db.update_conversation(req.conversation_id, {
                    "status": "closed",
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "ai",
                    "assigned_agent_id": None,
                })
                logger.info(f"[{req.conversation_id}] Post-CRM: template + re-close (no handoff)")
                return ChatResponse(
                    conversation_id=req.conversation_id,
                    message=_post_crm_msg,
                    type="template",
                    model_used="template",
                )

    # 3. If existing conversation in 'human' mode
    if req.conversation_id:
        mode = await db.get_conversation_mode(req.conversation_id)
        if mode == "human":
            # H1: Check if assigned agent is effectively offline before queuing
            from app.services.handoff import is_agent_effectively_offline, fall_back_to_ai
            if await is_agent_effectively_offline(req.conversation_id):
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

    # 3.5. New conversations always start with AI.
    # Agent handoff only via [HANDOFF_REQUESTED] in the pipeline (or explicit needs_agent).
    # Auto-routing on first message removed — stale heartbeats caused phantom agents
    # and "Connecting you with a specialist..." with no AI follow-up.

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

    response = await process_message(
        conversation_id=req.conversation_id,
        message=clean_message,
        tunnel=req.tunnel,
        visitor=req.visitor,
        metadata=_meta or None,
        visitor_id=req.visitor_id,
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
        return {"success": True, "data": msgs}
    except Exception as e:
        logger.error(f"public_get_messages error: {e}")
        return {"success": False, "data": [], "error": str(e)}


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
