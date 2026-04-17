"""Conversation service — business logic for conversations."""
import logging
from typing import Optional, Any
from app.db import supabase as db

logger = logging.getLogger(__name__)


async def get_or_create_conversation(
    conversation_id: Optional[str], tunnel: str, visitor: Any,
    visitor_id: Optional[str] = None,
):
    return await db.get_or_create_conversation(conversation_id, tunnel, visitor, visitor_id=visitor_id)


async def add_message(conversation_id: str, role: str, content: str,
                      model_used: Optional[str] = None, cost: float = 0.0):
    return await db.add_message(conversation_id, role, content, model_used, cost)
