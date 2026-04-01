"""SSE ConnectionManager — in-memory message queue per conversation.

Single worker deployment (Railway Hobby) — asyncio.Queue is safe.
If scaling to 2+ workers in future, replace queues with Redis pub/sub.
"""
import asyncio
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._queues: Dict[str, asyncio.Queue] = {}

    async def connect(self, conv_id: str) -> asyncio.Queue:
        """Register SSE connection for conv_id. Latest connection wins on reconnect."""
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._queues[conv_id] = q
        logger.info(f"[sse] connected conv={conv_id} total={len(self._queues)}")
        return q

    def disconnect(self, conv_id: str) -> None:
        """Remove SSE connection for conv_id."""
        self._queues.pop(conv_id, None)
        logger.info(f"[sse] disconnected conv={conv_id} total={len(self._queues)}")

    async def push(self, conv_id: str, message: dict) -> None:
        """Push message to SSE queue. No-op if no active connection (client uses polling)."""
        if conv_id in self._queues:
            try:
                self._queues[conv_id].put_nowait(message)
            except asyncio.QueueFull:
                logger.warning(
                    f"[sse] queue full for conv={conv_id} — client likely disconnected"
                )


# Module-level singleton — import this instance everywhere
manager = ConnectionManager()
