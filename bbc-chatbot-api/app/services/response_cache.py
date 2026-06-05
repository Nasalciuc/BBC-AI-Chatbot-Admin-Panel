"""Simple exact-match response cache using Redis."""

import hashlib
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_redis_client = None
_redis_checked = False


def _get_redis():
    """Lazy Redis connection — returns None if unavailable."""
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True
    try:
        from config.settings import settings
        if not settings.redis_url:
            logger.info("[CACHE] Redis not configured — cache disabled")
            return None
        import redis
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        _redis_client.ping()
        logger.info("[CACHE] Redis connected — response cache enabled")
    except Exception as e:
        logger.warning(f"[CACHE] Redis unavailable: {e}")
        _redis_client = None
    return _redis_client


def _cache_key(message: str, site: str, tunnel: str) -> str:
    normalized = message.strip().lower()
    raw = f"{site}:{tunnel}:{normalized}"
    return f"rc:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


# Only cache these intents — NEVER booking-specific
CACHEABLE_INTENTS = frozenset({
    "greeting", "faq", "general_question", "farewell",
    "thanks", "help", "about", "closing",
})


def get_cached(message: str, site: str = "bbc", tunnel: str = "sales") -> Optional[str]:
    """Return cached response text or None."""
    r = _get_redis()
    if not r:
        return None
    try:
        key = _cache_key(message, site, tunnel)
        data = r.get(key)
        if data:
            parsed = json.loads(data)
            logger.info(f"[CACHE] HIT {key[:12]} intent={parsed.get('intent', '?')}")
            return parsed.get("message")
        return None
    except Exception as e:
        logger.warning(f"[CACHE] GET error: {e}")
        return None


def set_cached(
    message: str,
    response: str,
    intent: str,
    site: str = "bbc",
    tunnel: str = "sales",
    ttl: int = 3600,
) -> None:
    """Cache response. Only for safe intents."""
    if intent not in CACHEABLE_INTENTS:
        return
    r = _get_redis()
    if not r:
        return
    try:
        key = _cache_key(message, site, tunnel)
        r.setex(key, ttl, json.dumps({"message": response, "intent": intent}))
        logger.info(f"[CACHE] SET {key[:12]} ttl={ttl} intent={intent}")
    except Exception as e:
        logger.warning(f"[CACHE] SET error: {e}")
