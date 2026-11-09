"""In-memory per-IP rate limiter for the chat endpoint."""

import time
import logging
from collections import OrderedDict
from threading import Lock

from fastapi import HTTPException, Request

from config.settings import settings
from app.flags import flag
from app.security.client_ip import client_ip

logger = logging.getLogger(__name__)


class _Bucket:
    """Token bucket + sliding window counters for one IP."""

    __slots__ = (
        "tokens", "last_refill",
        "hourly_hits", "hourly_reset",
        "daily_hits", "daily_reset",
    )

    def __init__(self) -> None:
        now = time.monotonic()
        self.tokens: float = float(settings.rate_burst)
        self.last_refill: float = now
        self.hourly_hits: int = 0
        self.hourly_reset: float = now + 3600
        self.daily_hits: int = 0
        self.daily_reset: float = now + 86400


# Bounded: one entry per address ever seen used to live for the life of the
# process. Oldest out first once RATE_BUCKETS_MAX is reached.
_buckets: "OrderedDict[str, _Bucket]" = OrderedDict()
_lock = Lock()


def _get_ip(request: Request) -> str:
    """The client's address — app/security/client_ip.py decides which header."""
    return client_ip(request, prefer_cf=False) or "unknown"


def _bucket_for(ip: str) -> _Bucket:
    """Caller holds _lock."""
    b = _buckets.get(ip)
    if b is None:
        b = _Bucket()
        _buckets[ip] = b
        cap = max(1, int(flag("RATE_BUCKETS_MAX", 50000)))
        while len(_buckets) > cap:
            _buckets.popitem(last=False)
    else:
        _buckets.move_to_end(ip)
    return b


async def check_rate_limit(request: Request) -> None:
    """FastAPI dependency — raises 429 if any limit is exceeded."""
    ip = _get_ip(request)
    now = time.monotonic()

    with _lock:
        b = _bucket_for(ip)

        # ── Reset sliding windows ──────────────────────────────
        if now >= b.hourly_reset:
            b.hourly_hits = 0
            b.hourly_reset = now + 3600
        if now >= b.daily_reset:
            b.daily_hits = 0
            b.daily_reset = now + 86400

        # ── Refill token bucket ────────────────────────────────
        elapsed = max(0.0, now - b.last_refill)
        refill = elapsed / settings.rate_sustained_seconds
        b.tokens = min(float(settings.rate_burst), b.tokens + refill)
        b.last_refill = now

        # ── Check limits ───────────────────────────────────────
        if b.tokens < 1:
            logger.warning("Rate limit (burst) hit for %s", ip)
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please slow down.",
            )

        if b.hourly_hits >= settings.rate_hourly_max:
            logger.warning("Rate limit (hourly) hit for %s", ip)
            raise HTTPException(
                status_code=429,
                detail="Hourly message limit reached.",
            )

        if b.daily_hits >= settings.rate_daily_max:
            logger.warning("Rate limit (daily) hit for %s", ip)
            raise HTTPException(
                status_code=429,
                detail="Daily message limit reached.",
            )

        # ── Consume ────────────────────────────────────────────
        b.tokens -= 1
        b.hourly_hits += 1
        b.daily_hits += 1
