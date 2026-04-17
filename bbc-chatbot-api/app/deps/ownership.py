"""Ownership dependency — verifies the caller owns the conversation.

Used on widget write endpoints (typing, session/open, session/close).
The visitor_id is passed as an optional X-Visitor-Id header.

Soft enforcement: only rejects when BOTH the request AND the DB row
have a visitor_id and they differ. Legacy conversations without
visitor_id are always allowed through.
"""
import logging
from typing import Optional

from fastapi import Header, HTTPException

from app.db import supabase as db

logger = logging.getLogger(__name__)


async def require_visitor_ownership(
    conversation_id: str,
    x_visitor_id: Optional[str] = Header(None),
) -> None:
    """FastAPI dependency — call before any mutation on a conversation.
    Raises 403 if visitor_id is present on both sides and they differ."""
    if not x_visitor_id:
        return  # no visitor_id header → legacy widget, allow through

    conv = await db.get_conversation_simple(conversation_id)
    if not conv:
        return  # conversation not found — let the endpoint handle 404 itself

    conv_visitor = conv.get("visitor_id")
    if not conv_visitor:
        return  # legacy conversation without visitor_id, allow through

    if conv_visitor != x_visitor_id:
        logger.warning(
            f"Ownership mismatch: header={x_visitor_id[:8]}... "
            f"vs conv={conv_visitor[:8]}... (conv_id={conversation_id})"
        )
        raise HTTPException(status_code=403, detail="Visitor does not own this conversation")
