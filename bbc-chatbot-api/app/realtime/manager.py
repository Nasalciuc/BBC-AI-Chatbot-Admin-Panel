"""SSE ConnectionManager — in-memory fan-out per conversation.

Single worker deployment — asyncio.Queue is safe. If scaling to 2+ workers,
replace with Redis pub/sub.

Multi-subscriber since 1 Sep 2026: the widget (visitor) and the admin panel
(operator) both listen to the same conversation. The old shape was one queue
per conversation and connect() OVERWROTE it — the moment the panel subscribed,
the visitor's widget silently lost its stream.
"""
import asyncio
import logging
from typing import Dict, Set

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._subs: Dict[str, Set[asyncio.Queue]] = {}
        # Subscribers that understand event types added after v1 (PR-10:
        # `typing`). A widget cached before the change would render an unknown
        # event as an empty message, so it never receives them.
        self._v2: Set[asyncio.Queue] = set()

    async def connect(self, conv_id: str, *, v2: bool = False) -> asyncio.Queue:
        """Register one subscriber for conv_id. Returns ITS queue; pass it back
        to disconnect() so only this subscriber is removed."""
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.setdefault(conv_id, set()).add(q)
        if v2:
            self._v2.add(q)
        logger.info(
            f"[sse] connected conv={conv_id} subs={len(self._subs[conv_id])} "
            f"conversations={len(self._subs)}"
        )
        return q

    def disconnect(self, conv_id: str, queue: asyncio.Queue) -> None:
        """Remove ONE subscriber. The conversation entry goes when the last leaves."""
        self._v2.discard(queue)
        subs = self._subs.get(conv_id)
        if not subs:
            return
        subs.discard(queue)
        if not subs:
            self._subs.pop(conv_id, None)
        logger.info(
            f"[sse] disconnected conv={conv_id} subs={len(subs)} "
            f"conversations={len(self._subs)}"
        )

    def subscriber_count(self, conv_id: str | None = None) -> int:
        if conv_id is not None:
            return len(self._subs.get(conv_id, ()))
        return sum(len(s) for s in self._subs.values())

    # Chunks may use at most this share of a subscriber's queue. Above it they
    # are dropped so that whole messages and stream_end always find room. A
    # 500-token reply on a slow client used to fill the queue with chunks the
    # panel does not even render, and then drop the one event that carried
    # the complete message — with the stream still "open", so polling never
    # stepped in. The operator simply never saw the reply.
    _CHUNK_HIGH_WATER = 0.8

    def _fanout(self, conv_id: str, payload: dict, *, is_chunk: bool = False, v2_only: bool = False) -> None:
        for q in tuple(self._subs.get(conv_id, ())):
            if v2_only and q not in self._v2:
                continue
            if is_chunk and q.qsize() >= int(q.maxsize * self._CHUNK_HIGH_WATER):
                continue  # chunk backpressure: keep headroom for terminal events
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull as e:
                # A non-chunk payload found no room even above the chunk cap:
                # this subscriber is not draining at all. Drop its copy, keep
                # the loop going for the other subscriber, and say so loudly —
                # a dropped stream_end is a message the operator will not see.
                logger.warning(
                    f"[sse] {type(e).__name__} for conv={conv_id} "
                    f"(event={payload.get('event', 'message')}) — "
                    "subscriber not draining; terminal event dropped"
                )

    async def push(self, conv_id: str, message: dict) -> None:
        """Push message to every subscriber. No-op if none (clients poll)."""
        self._fanout(conv_id, message)

    async def push_chunk(self, conv_id: str, delta: str) -> None:
        """Streaming text chunk. Dropped on a full queue — the subscriber gets
        the full message at stream_end."""
        self._fanout(conv_id, {"event": "stream_chunk", "delta": delta}, is_chunk=True)

    async def push_stream_end(self, conv_id: str, message: dict) -> None:
        self._fanout(conv_id, {"event": "stream_end", **message})

    async def push_typing(self, conv_id: str, is_typing: bool, name: str = "") -> None:
        """Operator typing → the widget, only to v2 subscribers (PR-10). Dropped
        like a chunk on a busy queue: the next refresh or the message fixes it."""
        self._fanout(
            conv_id,
            {"event": "typing", "is_typing": bool(is_typing), "name": name if is_typing else ""},
            is_chunk=True,
            v2_only=True,
        )


# Module-level singleton — import this instance everywhere
manager = ConnectionManager()
