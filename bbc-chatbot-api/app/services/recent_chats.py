"""The chats I opened lately: what the search box offers before anything is typed.

A Redis list per panel user, `recent:<user id>`, newest first, at most KEEP conversation ids, kept TTL_S
after the last open (contracts/redis-keys.md). Coordination, never the truth: without Redis the list is
empty and the panel shows the search box alone. The chats themselves are read fresh on every read
(app.api.search), and an agent sees only the ones they may read now.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.infra.redis_client import get_redis, redis_failed

logger = logging.getLogger(__name__)

KEEP = 10
TTL_S = 30 * 86400

RECENT_HEALTH: dict = {"remembered": 0, "remember_failed": 0, "read_failed": 0}


def key_for(user_id: str) -> str:
    return f"recent:{user_id}"


async def remember(user_id: Optional[str], conversation_id: str) -> bool:
    """This user just opened this chat: it goes first in their list. False when it could not be noted."""
    r = get_redis()
    if r is None or not user_id or not conversation_id:
        return False
    key = key_for(user_id)
    try:
        pipe = r.pipeline(transaction=True)
        pipe.lrem(key, 0, conversation_id)
        pipe.lpush(key, conversation_id)
        pipe.ltrim(key, 0, KEEP - 1)
        pipe.expire(key, TTL_S)
        await pipe.execute()
    except Exception as e:
        redis_failed("recent chats", e)
        RECENT_HEALTH["remember_failed"] += 1
        logger.warning(f"[recent] opening {conversation_id} not noted for {user_id} ({type(e).__name__}: {e})")
        return False
    RECENT_HEALTH["remembered"] += 1
    return True


async def recent_ids(user_id: Optional[str]) -> Optional[list[str]]:
    """The ids, newest first; [] when there are none; None when Redis can't say (not configured, down)."""
    r = get_redis()
    if r is None or not user_id:
        return None
    try:
        return [str(x) for x in await r.lrange(key_for(user_id), 0, KEEP - 1)]
    except Exception as e:
        redis_failed("recent chats read", e)
        RECENT_HEALTH["read_failed"] += 1
        logger.warning(f"[recent] the list of {user_id} not read ({type(e).__name__}: {e})")
        return None
