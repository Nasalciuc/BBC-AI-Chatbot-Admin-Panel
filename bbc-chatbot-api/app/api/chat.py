"""POST /api/chat — Main chat endpoint.
Thin controller: validate → sanitize → orchestrate → respond.
"""

import logging

import asyncio
import json
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

logger = logging.getLogger(__name__)

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

    # 3. If existing conversation in 'human' mode
    if req.conversation_id:
        mode = await db.get_conversation_mode(req.conversation_id)
        if mode == "human":
            # Check: has agent been silent > 5 minutes? → fallback to AI
            from datetime import datetime, timezone, timedelta
            last_agent_time = await db.get_last_agent_message_time(req.conversation_id)
            agent_silent = (
                last_agent_time is not None
                and (datetime.now(timezone.utc) - last_agent_time) > timedelta(seconds=settings.agent_silent_timeout_seconds)
            )
            if agent_silent:
                # Agent hasn't replied in 5 min → revert to AI, fall through to pipeline
                logger.info(f"[fallback] Conv {req.conversation_id}: agent silent 5min → AI")
                await db.update_conversation(req.conversation_id, {
                    "mode": "ai",
                    "assigned_agent_id": None,
                })
                # Don't return — fall through to step 3.5 / step 4 (AI pipeline)
            else:
                # Agent is active → save message for agent, skip AI
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
        route = await route_conversation(req.tunnel)
        if route["agent_id"]:
            from app.services.conversation_service import add_message
            from uuid import uuid4
            from datetime import datetime, timezone
            conv = await db.get_or_create_conversation(None, req.tunnel, req.visitor)
            if conv:
                await db.update_conversation(conv["id"], {
                    "mode": "human",
                    "assigned_agent_id": route["agent_id"],
                })
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
    )

    return response


# ── Public widget endpoints (no auth required — conv_id UUID is the secret) ──

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
