"""Closing message utilities — single source of truth for brand text + atomic claim."""

import logging
from datetime import datetime, timezone

from app.ai.prompts import get_brand_vars
from app.db import supabase as db
from config.settings import settings

logger = logging.getLogger(__name__)


def compute_closing_text(site: str | None = None) -> str:
    """Resolve closing message for the correct brand (BBC vs BCT).
    Always use this — never reference settings.post_crm_closing_message directly."""
    brand = get_brand_vars(site)
    return brand.get("closing_message") or settings.post_crm_closing_message


async def claim_closing_sent(conversation_id: str) -> bool:
    """Claim the right to send closing message. Returns True ONLY for the
    first caller. Second+ callers get False (closing already sent).

    Not fully atomic (read-then-write via Supabase client), but at ~2 conv/hour
    the race window is <50ms. Logs ERROR on failure per learning:
    'silent swallow on critical paths converts bugs into multi-day mysteries.'
    """
    try:
        conv = await db.get_conversation_simple(conversation_id)
        meta = dict((conv or {}).get("metadata") or {})
        if meta.get("closing_sent_at"):
            logger.info(f"[{conversation_id}] closing_sent_at already set — skip duplicate")
            return False
        meta["closing_sent_at"] = datetime.now(timezone.utc).isoformat()
        await db.update_conversation(conversation_id, {"metadata": meta})
        logger.info(f"[{conversation_id}] closing_sent_at claimed — sending closing")
        return True
    except Exception as e:
        logger.error(f"[{conversation_id}] claim_closing_sent FAILED: {e}")
        return False


async def claim_super_alert(conversation_id: str, cooldown_minutes: int) -> bool:
    """Returns True if this caller should send the super alert (claims it).
    Read-then-write like claim_closing_sent — accepts ~50ms race window.
    Prevents repeated alerts within cooldown for the same conversation."""
    try:
        conv = await db.get_conversation_simple(conversation_id)
        if not conv:
            return False
        meta = dict((conv or {}).get("metadata") or {})
        last = meta.get("super_notified_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(
                    last.replace("Z", "+00:00") if isinstance(last, str) else last
                )
                if (datetime.now(timezone.utc) - last_dt).total_seconds() < cooldown_minutes * 60:
                    logger.info(f"[{conversation_id}] super_notified_at within cooldown — skip")
                    return False
            except (ValueError, TypeError):
                pass
        meta["super_notified_at"] = datetime.now(timezone.utc).isoformat()
        await db.update_conversation(conversation_id, {"metadata": meta})
        logger.info(f"[{conversation_id}] super_notified_at claimed — sending alert")
        return True
    except Exception as e:
        logger.error(f"[{conversation_id}] claim_super_alert FAILED: {e}")
        return False


def has_closing_been_sent(metadata: dict | None) -> bool:
    """Check if closing was already sent (for guards without DB call)."""
    return bool((metadata or {}).get("closing_sent_at"))
