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
from config.settings import settings

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
    "budget_durable": None,  # False: AI_LEDGER_ENABLED off, cap resets on restart
    # The startup seed: "seeded", "failed" (logged) or "timed_out". Not
    # seeded, the cap counts only the spend recorded since boot.
    "budget_seed": None,
}
_warned_missing = False

# ── Today's spend, in-process (PR-6 BudgetState) ──────────────
# Seeded once at startup from the ai_calls sum for the UTC day, then advanced
# in-process on every record_ai_call. Replaces get_today_cost (capped at 500
# rows) for the router's budget decisions. One process (ADR-5), but the sync
# Claude wrappers run in worker threads (asyncio.to_thread / run_in_executor),
# so every read and write of _budget goes through _budget_lock.
#
# Durability: the total survives a restart only because seed_budget re-sums
# the day's ai_calls rows. With AI_LEDGER_ENABLED=false no rows are written,
# so the cap is in-memory only and a restart that day starts again from 0;
# LEDGER_HEALTH["budget_durable"] says which case is live.
import datetime as _dt
import threading

_budget: dict = {"day": None, "total": 0.0}
_budget_lock = threading.Lock()
_SEED_PAGE = 1000  # PostgREST's default max rows per response


def _today_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def _advance_budget(cost: float) -> None:
    today = _today_utc()
    with _budget_lock:
        if _budget["day"] != today:
            _budget["day"] = today
            _budget["total"] = 0.0
        _budget["total"] = round(_budget["total"] + max(0.0, cost), 6)


def today_cost() -> float:
    today = _today_utc()
    with _budget_lock:
        if _budget["day"] != today:
            return 0.0
        return _budget["total"]


def budget_fraction() -> float:
    try:
        cap = float(settings.daily_budget)
    except (TypeError, ValueError):  # noqa: silent — a misconfigured cap yields fraction 0, never a crash
        return 0.0
    if cap <= 0:
        return 0.0
    return today_cost() / cap


async def seed_budget() -> None:
    """Sum today's ai_calls once at startup so a restart does not reset spend.

    Reads every page: one PostgREST response stops at 1,000 rows, and a
    partial sum would leave the router under the downshift threshold.
    """
    from app.db.supabase import _run_sync, get_client

    durable = bool(flag("AI_LEDGER_ENABLED", True))
    LEDGER_HEALTH["budget_durable"] = durable
    if not durable:
        logger.warning("[budget] AI_LEDGER_ENABLED=false: the daily cap is in-memory only and resets on restart")
    try:
        day = _today_utc()
        total = 0.0
        offset = 0
        while True:
            start = offset
            res = await _run_sync(
                lambda: get_client().table("ai_calls").select("cost_usd")
                .gte("created_at", f"{day}T00:00:00+00:00")
                .order("created_at").order("id")
                .range(start, start + _SEED_PAGE - 1).execute(),
                label="ai_calls",
            )
            rows = getattr(res, "data", None) or []
            total += sum(float(r.get("cost_usd", 0) or 0) for r in rows)
            if len(rows) < _SEED_PAGE:
                break
            offset += _SEED_PAGE
        total = round(total, 6)
        with _budget_lock:
            # Calls recorded while the seed ran are already in memory; keep
            # the larger figure rather than double-count or drop them.
            if _budget["day"] == day:
                total = max(total, _budget["total"])
            _budget["day"] = day
            _budget["total"] = total
        LEDGER_HEALTH["budget_seed"] = "seeded"
        logger.info(f"[budget] seeded today's spend: ${total:.4f}")
    except Exception as e:
        LEDGER_HEALTH["budget_seed"] = "failed"
        logger.warning(f"[budget] seed skipped: {type(e).__name__}: {e}")


_SEED_TIMEOUT_S = 15.0


async def seed_budget_bounded(timeout: float = _SEED_TIMEOUT_S) -> None:
    """seed_budget at startup, bounded: it reads a page per 1,000 calls, and a
    slow database must not hold startup and the healthcheck (the flags'
    first read is bounded the same way). On a timeout the in-memory total
    stands, so the cap counts what is recorded from boot on."""
    import asyncio

    try:
        await asyncio.wait_for(seed_budget(), timeout=timeout)
    except asyncio.TimeoutError as e:
        LEDGER_HEALTH["budget_seed"] = "timed_out"
        logger.warning(
            f"[budget] seed took over {timeout:.0f} s ({type(e).__name__}): "
            "the daily cap counts only the spend since boot"
        )


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

    # Track spend even when the ai_calls write is disabled: the budget guard
    # must still see today's cost.
    _advance_budget(cost)

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
        # Migration 037 applied while running: the table is there now. Say so,
        # and warn again if it ever goes missing.
        LEDGER_HEALTH["table_missing"] = False
        _warned_missing = False
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
        budget_durable=None, budget_seed=None,
    )
    _budget["day"] = None
    _budget["total"] = 0.0
