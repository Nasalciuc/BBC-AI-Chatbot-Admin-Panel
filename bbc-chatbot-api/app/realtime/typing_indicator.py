"""Redis-backed typing indicator state.

Stores what the client is currently typing, per conversation.
Uses Redis SETEX for automatic TTL — no manual cleanup needed.
Works correctly with multiple Railway workers (shared state).

Two independent directions, two key namespaces:
  client → operator : bbc:typing:{conv_id}        (text is shown to the operator)
  operator → client : bbc:typing:agent:{conv_id}  (only a name, shown in the widget)

TTL: 5 minutes for the client direction (see _TYPING_TTL), 10s for the operator
direction (see _AGENT_TYPING_TTL).
"""
import json
import logging
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

_TYPING_TTL = 300  # 5 minutes — safety fallback for orphan state when
                   # widget disconnects without sending DELETE (crash,
                   # network drop). During normal use, typing indicator
                   # is cleared explicitly by widget on: send, input
                   # cleared, or widget closed. Long TTL prevents the
                   # indicator from disappearing during natural typing
                   # pauses (previously 10s caused the operator to lose
                   # visibility on what the client was writing).

_AGENT_TYPING_TTL = 10  # Operator → visitor direction is deliberately SHORT.
                        # The visitor sees a live "is typing…" claim, so a key
                        # that outlives the actual typing tells the visitor a
                        # lie. The admin panel refreshes it ~1/s while the
                        # operator types, so 10s covers normal pauses and self-
                        # heals if the operator closes the tab mid-sentence.


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

    # ── Operator → visitor direction (separate key, never mixed with above) ──

    @staticmethod
    def _agent_key(conv_id: str) -> str:
        return f"bbc:typing:agent:{conv_id}"

    async def set_agent_typing(self, conv_id: str, name: str = "") -> None:
        """Record that the operator is typing. SETEX resets TTL on every call."""
        r = self._get_client()
        if not r:
            return
        try:
            await r.setex(
                self._agent_key(conv_id),
                _AGENT_TYPING_TTL,
                json.dumps({"name": name}),
            )
        except Exception as e:
            logger.warning(f"[typing] set_agent_typing error: {e}")

    async def clear_agent_typing(self, conv_id: str) -> None:
        """Remove operator typing state (message sent or input cleared)."""
        r = self._get_client()
        if not r:
            return
        try:
            await r.delete(self._agent_key(conv_id))
        except Exception as e:
            logger.warning(f"[typing] clear_agent_typing error: {e}")

    async def get_agent_typing(self, conv_id: str) -> Optional[dict]:
        """Operator typing state for the widget. None if not typing or expired.

        Only the operator's name travels to the visitor — never the draft text.
        """
        r = self._get_client()
        if not r:
            return None
        try:
            data = await r.get(self._agent_key(conv_id))
            if not data:
                return None
            parsed = json.loads(data)
            return {"is_typing": True, "name": parsed.get("name", "")}
        except Exception as e:
            logger.warning(f"[typing] get_agent_typing error: {e}")
            return None


# Module-level singleton — import this instance everywhere
typing_manager = TypingManager()
