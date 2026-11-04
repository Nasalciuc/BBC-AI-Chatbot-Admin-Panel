"""record_ai_call — the one way an Anthropic call's cost is written down.

Every call to the model records a row in ai_calls: the task, the model, the
four token counts, the dollar cost (from app/ai/prices.cost_from_usage), the
latency and status. A call that does not go through here is invisible on the
invoice — tests/test_code_discipline enforces that `.messages.create(` etc.
live only in the allowed modules.

The write never sits in the reply's path. `record_ai_call` is sync and cheap:
it drops a row onto a bounded in-memory queue and returns. A background task
started from the app's startup hook drains the queue every 2 s (or at 50 rows)
with one batched insert. A full queue drops the row and counts it; a missing
table logs once and then stays quiet. None of this can fail a chat turn.
"""

from __future__ import annotations

import logging
import queue
import time
from typing import Any, Optional

from app.ai.prices import cost_from_usage, rates_for
from app.flags import flag

logger = logging.getLogger(__name__)

_QUEUE_MAX = 10_000
_FLUSH_EVERY_SECONDS = 2.0
_FLUSH_AT_ROWS = 50

# queue.Queue, not SimpleQueue: SimpleQueue cannot be bounded, and an unbounded
# queue behind a wedged DB is an OOM, not a dropped metric.
_q: "queue.Queue[dict]" = queue.Queue(maxsize=_QUEUE_MAX)

LEDGER_HEALTH: dict[str, Any] = {
    "recorded": 0,
    "dropped": 0,
    "flushed": 0,
    "flush_errors": 0,
    "table_missing": False,
    "by_task": {},       # task -> {"calls", "cost_usd"}
    "by_model": {},
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "reconcile": None,    # last reconciliation diff, set by the cron route
}
_warned_missing = False


def _tally(task: str, model: str, cost: float, cache_read: int, cache_write: int) -> None:
    t = LEDGER_HEALTH["by_task"].setdefault(task, {"calls": 0, "cost_usd": 0.0})
    t["calls"] += 1
    t["cost_usd"] = round(t["cost_usd"] + cost, 6)
    m = LEDGER_HEALTH["by_model"].setdefault(model, {"calls": 0, "cost_usd": 0.0})
    m["calls"] += 1
    m["cost_usd"] = round(m["cost_usd"] + cost, 6)
    LEDGER_HEALTH["cache_read_tokens"] += cache_read
    LEDGER_HEALTH["cache_write_tokens"] += cache_write


def record_ai_call(
    *,
    task: str,
    model: str,
    usage: Any = None,
    cost: Optional[float] = None,
    latency_ms: Optional[int] = None,
    status: str = "ok",
    conversation_id: Optional[str] = None,
    request_id: Optional[str] = None,
    batch: bool = False,
) -> float:
    """Record one call; returns its dollar cost (0.0 when the ledger is off)."""
    input_tokens = output_tokens = cache_read = cache_write = 0
    if usage is not None:
        from app.ai.prices import _breakdown, _int

        input_tokens = _int(usage, "input_tokens")
        output_tokens = _int(usage, "output_tokens")
        cache_write, cache_read, _, _ = _breakdown(usage)
    if cost is None:
        cost = cost_from_usage(model, usage) if usage is not None else 0.0

    if not flag("AI_LEDGER_ENABLED", True):
        return cost

    _tally(task, model, cost, cache_read, cache_write)
    row = {
        "task": task,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_write_tokens": cache_write,
        "cache_read_tokens": cache_read,
        "cost_usd": round(cost, 6),
        "latency_ms": latency_ms,
        "status": status if status in ("ok", "timeout", "error", "refusal", "fallback") else "ok",
        "conversation_id": conversation_id,
        "request_id": request_id,
        "batch": batch,
    }
    try:
        _q.put_nowait(row)
        LEDGER_HEALTH["recorded"] += 1
    except queue.Full:  # noqa: silent — a dropped metric is counted below, never a failed chat turn
        LEDGER_HEALTH["dropped"] += 1
        if LEDGER_HEALTH["dropped"] % 100 == 1:
            logger.warning(f"[ledger] queue full — dropped {LEDGER_HEALTH['dropped']} rows so far")
    return cost


def _drain(limit: int) -> list[dict]:
    rows: list[dict] = []
    while len(rows) < limit:
        try:
            rows.append(_q.get_nowait())
        except queue.Empty:  # noqa: silent — the queue is empty, which is the normal stop condition
            break
    return rows


async def _insert(rows: list[dict]) -> None:
    global _warned_missing
    from app.db.supabase import _run_sync, get_client

    try:
        await _run_sync(
            lambda: get_client().table("ai_calls").insert(rows).execute(),
            idempotent=False,
            label="ai_calls",
        )
        LEDGER_HEALTH["flushed"] += len(rows)
    except Exception as e:
        text = str(e).lower()
        if "ai_calls" in text and ("does not exist" in text or "could not find" in text or "42p01" in text):
            LEDGER_HEALTH["table_missing"] = True
            if not _warned_missing:
                _warned_missing = True
                logger.warning(f"[ledger] ai_calls table missing — ledger disabled until migrated: {type(e).__name__}")
            return
        LEDGER_HEALTH["flush_errors"] += 1
        logger.warning(f"[ledger] flush of {len(rows)} rows failed: {type(e).__name__}: {e}")


async def flush_once() -> int:
    rows = _drain(_QUEUE_MAX)
    if rows:
        await _insert(rows)
    return len(rows)


async def _flush_forever() -> None:
    import asyncio

    while True:
        try:
            await asyncio.sleep(_FLUSH_EVERY_SECONDS)
            # Flush in batches so a backlog does not become one giant insert.
            while _q.qsize() >= _FLUSH_AT_ROWS:
                await _insert(_drain(_FLUSH_AT_ROWS))
            await flush_once()
        except asyncio.CancelledError:
            await flush_once()  # drain on shutdown
            raise
        except Exception as e:
            LEDGER_HEALTH["flush_errors"] += 1
            logger.warning(f"[ledger] flusher iteration failed: {type(e).__name__}: {e}")


def start_flusher():
    import asyncio

    return asyncio.create_task(_flush_forever(), name="ai_ledger_flusher")


def _reset_for_tests() -> None:
    global _warned_missing
    _warned_missing = False
    try:
        while True:
            _q.get_nowait()
    except queue.Empty:  # noqa: silent — draining a possibly-empty queue in a test reset
        pass
    LEDGER_HEALTH.update(
        recorded=0, dropped=0, flushed=0, flush_errors=0, table_missing=False,
        by_task={}, by_model={}, cache_read_tokens=0, cache_write_tokens=0, reconcile=None,
    )
