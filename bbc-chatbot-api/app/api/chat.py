"""POST /api/chat — Main chat endpoint.
Thin controller: validate → sanitize → orchestrate → respond.
"""

import logging

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from config.settings import settings
from app.models.chat import ChatRequest, ChatResponse
from app.security.input_sanitizer import sanitize_message, is_suspicious
from app.security.rate_limiter import check_rate_limit
from app.pipeline.orchestrator import process_message
from app.db import supabase as db
from app.realtime.manager import manager

from pydantic import BaseModel

from app.deps.ownership import require_visitor_ownership

logger = logging.getLogger(__name__)


class TypingBody(BaseModel):
    text: str = ""


router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, _rate: None = Depends(check_rate_limit)) -> ChatResponse:
    """Handle a single chat message from the widget."""

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

    # 3. If existing conversation in 'human' mode
    if req.conversation_id:
        mode = await db.get_conversation_mode(req.conversation_id)
        if mode == "human":
            # Human mode is authoritative: never auto-switch to AI on client input.
            from app.services.conversation_service import add_message
            await add_message(
                conversation_id=req.conversation_id,
                role="user",
                content=clean_message,
            )
            return ChatResponse(
                conversation_id=req.conversation_id,
                message="One moment please, connecting you with a specialist...",
                type="queued",
                model_used="none",
            )

    # 3.5. New conversation? Try routing to an available agent first
    if not req.conversation_id:
        from app.services.routing import route_conversation
        # Step 3.5: Human-first routing (takes precedence over AI pipeline)
        # If agent available → return here, AI pipeline NOT called
        # If no agent → fall through to AI pipeline (step 4+)
        route = await route_conversation(req.tunnel, visitor=req.visitor)
        if not route["agent_id"]:
            # No agent on first attempt — wait 2s and retry once
            # This catches agents who just logged in (heartbeat in flight)
            logger.info(f"[routing] No agent for tunnel={req.tunnel} — retrying in 2s")
            await asyncio.sleep(2)
            route = await route_conversation(req.tunnel, visitor=req.visitor)
        if route["agent_id"]:
            from app.services.conversation_service import add_message
            from uuid import uuid4
            from datetime import datetime, timezone
            conv = await db.get_or_create_conversation(None, req.tunnel, req.visitor, visitor_id=req.visitor_id)
            if conv:
                await db.update_conversation(conv["id"], {
                    "mode": "human",
                    "assigned_agent_id": route["agent_id"],
                })
                # Race condition guard: verify agent not overloaded
                actual_count = await db.get_agent_active_count(route["agent_id"])
                if actual_count > settings.max_concurrent_chats:
                    logger.warning(
                        f"[routing] Race condition detected for agent "
                        f"{route['agent_id'][:8]}... — falling back to AI"
                    )
                    await db.update_conversation(conv["id"], {
                        "mode": "ai",
                        "assigned_agent_id": None,
                    })
                    # Fall through to AI pipeline (Step 4)
                else:
                    await add_message(conv["id"], "user", clean_message)

                    # Build 3 system messages
                    agent_name = route.get("agent_name", "A specialist")
                    now = datetime.now(timezone.utc).isoformat()

                    connecting = settings.connecting_message
                    joined = settings.joined_message_template.format(agent_name=agent_name)
                    welcome = (settings.welcome_message_sales
                            if req.tunnel == "sales"
                            else settings.welcome_message_support)
                    qr = (settings.quick_replies_sales
                        if req.tunnel == "sales"
                        else settings.quick_replies_support)

                    # Save all 3 to DB — capture real Supabase UUIDs to avoid polling duplicates
                    row1 = await add_message(conv["id"], "system", connecting)
                    row2 = await add_message(conv["id"], "system", joined)
                    row3 = await add_message(conv["id"], "system", welcome)
                    now = datetime.now(timezone.utc).isoformat()

                    return ChatResponse(
                        conversation_id=conv["id"],
                        message=welcome,
                        type="welcome",
                        model_used="none",
                        quick_replies=qr,
                        system_messages=[
                        {"id": row1["id"] if row1 else str(uuid4()), "role": "system", "content": connecting, "created_at": row1.get("created_at", now) if row1 else now},
                        {"id": row2["id"] if row2 else str(uuid4()), "role": "system", "content": joined, "created_at": row2.get("created_at", now) if row2 else now},
                        {"id": row3["id"] if row3 else str(uuid4()), "role": "system", "content": welcome, "created_at": row3.get("created_at", now) if row3 else now},
                        ],
                    )

    # 4. AI mode or new conversation (no agent available) → run pipeline
    response = await process_message(
        conversation_id=req.conversation_id,
        message=clean_message,
        tunnel=req.tunnel,
        visitor=req.visitor,
        metadata=req.metadata,
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
):
    """Public incremental message polling for widget.
    Used as SSE fallback and for catch-up after reconnect."""
    try:
        msgs = await db.get_messages_after(conversation_id, after)
        return {"success": True, "data": msgs}
    except Exception as e:
        logger.error(f"public_get_messages error: {e}")
        return {"success": False, "data": [], "error": str(e)}


@router.get("/chat/stream/{conversation_id}")
async def sse_stream(conversation_id: str, request: Request):
    """SSE real-time stream for widget.
    Pushes agent messages instantly (~50ms vs 1s polling).
    X-Accel-Buffering: no disables Railway/nginx proxy buffering — required for SSE.
    Widget falls back to polling if SSE fails 3 times."""

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
