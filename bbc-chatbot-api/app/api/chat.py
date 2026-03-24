"""POST /api/chat — Main chat endpoint.
Thin controller: validate → sanitize → orchestrate → respond.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from config.settings import settings
from app.models.chat import ChatRequest, ChatResponse
from app.security.input_sanitizer import sanitize_message, is_suspicious
from app.security.rate_limiter import check_rate_limit
from app.pipeline.orchestrator import process_message
from app.db import supabase as db

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

    # 3. If existing conversation in 'human' mode → save message only, skip AI
    if req.conversation_id:
        mode = await db.get_conversation_mode(req.conversation_id)
        if mode == "human":
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

    # 4. AI mode or new conversation → run pipeline as before
    response = await process_message(
        conversation_id=req.conversation_id,
        message=clean_message,
        tunnel=req.tunnel,
        visitor=req.visitor,
        metadata=req.metadata,
    )

    return response
