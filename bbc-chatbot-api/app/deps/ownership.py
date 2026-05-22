"""Ownership dependency — verifies the caller owns the conversation.

Used on widget write endpoints (typing, session/open, session/close).
The visitor_id is passed as an optional X-Visitor-Id header.

Soft enforcement: only rejects when BOTH the request AND the DB row
have a visitor_id and they differ. Legacy conversations without
visitor_id are always allowed through.
"""
import logging
from typing import Optional

from fastapi import Header, HTTPException, Query

from app.db import supabase as db

logger = logging.getLogger(__name__)


async def require_visitor_ownership(
    conversation_id: str,
    x_visitor_id: Optional[str] = Header(None),
    vid: Optional[str] = Query(None),
) -> None:
    """Strict ownership — blocks access when conv has visitor_id but caller doesn't prove it."""
    effective_id = (x_visitor_id or vid or "").strip() or None

    conv = await db.get_conversation_simple(conversation_id)
    if not conv:
        return  # conv not found — let the handler return 404

    conv_visitor = conv.get("visitor_id")
    if not conv_visitor:
        return  # legacy conv without visitor — allow

    # Conv HAS visitor_id — caller MUST prove ownership
    if not effective_id or effective_id != conv_visitor:
        logger.warning(
            f"Ownership denied for {conversation_id[:8]}: "
            f"expected={conv_visitor[:8]} got={effective_id[:8] if effective_id else 'NONE'}"
        )
        raise HTTPException(status_code=403, detail="Access denied")
