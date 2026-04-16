"""Content moderation service for abusive language detection."""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

BAD_WORDS: list[str] = [
    "fuck", "shit", "asshole", "bitch", "bastard",
    "damn", "crap", "idiot", "moron", "stupid",
    "scam", "fraud", "cheat", "liar", "fake",
]

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(rf"\\b{re.escape(word)}\\b", re.IGNORECASE)
    for word in BAD_WORDS
]


def detect_bad_words(text: str) -> Optional[str]:
    """Return the first detected bad word, otherwise None."""
    for pattern, word in zip(_PATTERNS, BAD_WORDS):
        if pattern.search(text):
            return word
    return None


async def moderate_message(
    conversation_id: str,
    message_content: str,
    sender_role: str = "user",
) -> bool:
    """Flag conversation when abusive language is detected."""
    if sender_role not in ("user", "agent"):
        return False

    detected = detect_bad_words(message_content)
    if not detected:
        return False

    try:
        from app.db import supabase as db

        await db.update_conversation(conversation_id, {
            "has_flagged_content": True,
            "flagged_reason": f"Bad word detected: '{detected}'",
        })

        logger.warning(
            f"[moderation] Conv {conversation_id}: bad word detected "
            f"(role={sender_role}, word='{detected}')"
        )
        return True
    except Exception as exc:
        logger.error(f"[moderation] Error flagging conv {conversation_id}: {exc}")
        return False
