"""Redis-backed typing indicator state.

Stores what the client is currently typing, per conversation.
Uses Redis SETEX for automatic TTL — no manual cleanup needed.
Works correctly with multiple Railway workers (shared state).

Key format: bbc:typing:{conv_id}
TTL: 10 seconds (reset on every keystroke via debounce)
"""
import json
import logging
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

_TYPING_TTL = 10  # seconds — auto-expires if client stops typing


class TypingManager:
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        """Lazy Redis client init. Returns None if Redis not configured."""
        if self._client is not None:
            return self._client
        if not settings.redis_url:
            logger.warning("[typing] REDIS_URL not set — typing indicator disabled")
            return None
        try:
            import redis.asyncio as aioredis
            self._client = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
            )
            logger.info("[typing] Redis client initialized")
        except Exception as e:
            logger.error(f"[typing] Failed to init Redis client: {e}")
            self._client = None
        return self._client

    async def set_typing(self, conv_id: str, text: str) -> None:
        """Record client is typing. SETEX resets TTL on every call."""
        r = self._get_client()
        if not r:
            return
        try:
            key = f"bbc:typing:{conv_id}"
            await r.setex(key, _TYPING_TTL, json.dumps({"text": text}))
        except Exception as e:
            logger.warning(f"[typing] set_typing error: {e}")

    async def clear_typing(self, conv_id: str) -> None:
        """Remove typing state immediately (message sent or input cleared)."""
        r = self._get_client()
        if not r:
            return
        try:
            await r.delete(f"bbc:typing:{conv_id}")
        except Exception as e:
            logger.warning(f"[typing] clear_typing error: {e}")

    async def get_typing(self, conv_id: str) -> Optional[dict]:
        """Return current typing state. Returns None if not typing or expired."""
        r = self._get_client()
        if not r:
            return None
        try:
            data = await r.get(f"bbc:typing:{conv_id}")
            if not data:
                return None
            parsed = json.loads(data)
            return {"is_typing": True, "text": parsed.get("text", "")}
        except Exception as e:
            logger.warning(f"[typing] get_typing error: {e}")
            return None


# Module-level singleton — import this instance everywhere
typing_manager = TypingManager()
