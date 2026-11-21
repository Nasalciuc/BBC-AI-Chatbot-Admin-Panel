"""The search box's two other answers: what was said in the chats, and the chats I opened lately.

GET /api/search/messages?q=   the messages of the last 30 days that contain q (3 to 100 characters).
    An agent finds only what they may read: their tunnel, and never a chat waiting blind for a person
    (conversations._taken_blind). Supervisors and project managers don't search words: their rows mask the
    contact the client's words carry, as the list gives them no previews. A few searches a minute each
    (SEARCH_MESSAGES_PER_MINUTE, app.flags): a search reads up to a month of messages.
GET /api/search/recent        the chats this user opened lately (app.services.recent_chats), read fresh.

Nothing typed is logged: a search is often a name.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.conversations import _enforce_tunnel, _taken_blind
from app.db import supabase as db
from app.flags import flag
from app.infra.redis_client import get_redis, redis_failed
from app.security.auth import get_current_user
from app.services import recent_chats
from app.services.chat_search import SEARCH_HEALTH

logger = logging.getLogger(__name__)
router = APIRouter()

MESSAGE_READERS = {"owner", "admin", "dev", "qa", "sales", "support"}
WINDOW_DAYS = 30
MAX_HITS = 30
SNIPPET_AROUND = 60
_ROLE_LABEL = {"user": "client", "ai": "assistant", "agent": "agent"}

# Without Redis: per process, a minute at a time.
_local_counts: dict[tuple[str, int], int] = {}


async def _over_the_limit(user_id: str) -> bool:
    limit = max(1, int(flag("SEARCH_MESSAGES_PER_MINUTE", 20)))
    minute = int(time.time() // 60)
    r = get_redis()
    if r is not None:
        key = f"rl:search:{user_id}:{minute}"
        try:
            pipe = r.pipeline(transaction=True)
            pipe.incr(key)
            pipe.expire(key, 120)
            count, _ = await pipe.execute()
            return int(count) > limit
        except Exception as e:
            redis_failed("search rate limit", e)
            logger.warning(f"[search] the shared count failed ({type(e).__name__}) — counting in this process")
    for k in [k for k in _local_counts if k[1] < minute]:
        _local_counts.pop(k, None)
    _local_counts[(user_id, minute)] = _local_counts.get((user_id, minute), 0) + 1
    return _local_counts[(user_id, minute)] > limit


def snippet(content: str, needle: str, around: int = SNIPPET_AROUND) -> str:
    """The words around the first place `needle` appears (case-insensitive), with … where cut."""
    text = " ".join((content or "").split())
    at = text.lower().find(needle.lower())
    if at < 0:
        at = 0
    start, end = max(0, at - around), min(len(text), at + len(needle) + around)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


@router.get("/search/messages")
async def search_messages(q: str = Query(..., min_length=3, max_length=100), user: dict = Depends(get_current_user)):
    if (user.get("role") or "") not in MESSAGE_READERS:
        raise HTTPException(403, "Searching what was said isn't open to this role.")
    needle = q.strip()
    if len(needle) < 3:
        raise HTTPException(400, "Type at least 3 characters.")
    if await _over_the_limit(user.get("id") or ""):
        SEARCH_HEALTH["rate_limited"] += 1
        raise HTTPException(429, "Too many searches in a minute. Try again shortly.")
    tunnel = _enforce_tunnel(user, None)
    since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()
    hits: list[dict] = []
    chats: dict[str, dict] = {}
    try:
        hits = await db.search_messages(needle, since_iso=since, limit=MAX_HITS * 2)
        chats = await db.get_conversations_brief([h.get("conversation_id") for h in hits])
    except Exception as e:
        SEARCH_HEALTH["messages_failed"] += 1
        logger.error(f"[search] messages not searched: {type(e).__name__}")
        raise HTTPException(502, "Couldn't search the messages. Try again.") from e
    out: list[dict] = []
    for hit in hits:
        chat = chats.get(hit.get("conversation_id") or "")
        if chat is None or (tunnel and chat.get("tunnel") != tunnel) or _taken_blind(user, chat):
            continue
        out.append({
            "conversation_id": chat["id"],
            "chat_number": chat.get("chat_number"),
            "visitor_name": chat.get("visitor_name"),
            "status": chat.get("status"),
            "message_id": hit.get("id"),
            "said_by": _ROLE_LABEL.get(hit.get("role") or "", "client"),
            "said_at": hit.get("created_at"),
            "snippet": snippet(hit.get("content") or "", needle),
        })
        if len(out) >= MAX_HITS:
            break
    return {"success": True, "data": out, "count": len(out), "window_days": WINDOW_DAYS}


@router.get("/search/recent")
async def recent(user: dict = Depends(get_current_user)):
    ids = await recent_chats.recent_ids(user.get("id"))
    if not ids:
        return {"success": True, "data": [], "count": 0, "available": ids is not None}
    chats: dict[str, dict] = {}
    try:
        chats = await db.get_conversations_brief(ids)
    except Exception as e:
        SEARCH_HEALTH["recent_failed"] += 1
        logger.error(f"[search] the recent chats not read: {type(e).__name__}")
        raise HTTPException(502, "Couldn't load the recent chats.") from e
    tunnel = _enforce_tunnel(user, None)
    masked = user.get("role") == "supervisor"
    out: list[dict] = []
    for cid in ids:
        chat = chats.get(cid)
        if chat is None or (tunnel and chat.get("tunnel") != tunnel):
            continue
        name = chat.get("visitor_name")
        if masked:
            from app.security.pii import mask_name
            name = mask_name(name)
        out.append({"id": cid, "chat_number": chat.get("chat_number"), "visitor_name": name,
                    "status": chat.get("status"), "updated_at": chat.get("updated_at")})
    return {"success": True, "data": out, "count": len(out), "available": True}
