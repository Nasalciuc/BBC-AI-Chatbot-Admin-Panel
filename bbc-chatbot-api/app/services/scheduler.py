"""Internal heartbeat — replaces reliance on GitHub Actions cadence.

GitHub throttles scheduled workflows on low-activity repos: our
'*/5 * * * *' ran every 1-2 HOURS in reality (measured 12 Jun).
Consequences: abandoned conversations closed at 36-108 min instead of
~35, and — worse — the #101 agent-response sweep (the 8-minute
deadline's proactive half) released silently-waiting clients only when
GitHub felt like it. The app now beats its own drum; Actions stays on
as best-effort redundancy (jobs are idempotent). See ADR-10.

Safe under the single-worker invariant (ADR-5): one process, one
scheduler, no distributed coordination. If the app ever scales out,
this migrates together with SSE/rate-limit, not separately.
"""

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Canary: readable heartbeat per job (exposed via /health).
last_runs: dict[str, dict] = {}

_locks: dict[str, asyncio.Lock] = {}


def get_scheduler_health() -> dict[str, dict[str, Any]]:
    """Per-job last_run_at and seconds_ago for /health canary."""
    now = datetime.now(timezone.utc)
    out: dict[str, dict[str, Any]] = {}
    for job, v in last_runs.items():
        raw_at = v.get("at", "")
        try:
            at = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
            seconds_ago = int((now - at).total_seconds())
        except (ValueError, TypeError):
            seconds_ago = None
        out[job] = {"last_run_at": raw_at, "seconds_ago": seconds_ago}
    return out


async def _run_forever(job_name: str, job_fn, interval_seconds: int):
    # G5: small start jitter so Railway restarts don't sync with Actions
    await asyncio.sleep(random.uniform(5, 15))
    _locks.setdefault(job_name, asyncio.Lock())
    while True:
        try:
            # G2: no internal overlap; overlap WITH Actions is covered
            # by job idempotency (close-if-abandoned is a no-op twice)
            async with _locks[job_name]:
                result = await job_fn()
            last_runs[job_name] = {
                "at": datetime.now(timezone.utc).isoformat(),
                "result": result,
            }
            logger.info(f"[scheduler:internal] {job_name}: {result}")
        except Exception as e:
            # G1: a job crash must never kill the loop — and never be
            # silent (the RC6 lesson, learned four times this month)
            logger.error(
                f"[scheduler:internal] {job_name} failed: {e}",
                exc_info=True,
            )
        await asyncio.sleep(interval_seconds)


def start(app_state, settings) -> list[asyncio.Task]:
    """Called from startup. Returns tasks so shutdown can cancel them."""
    from app.api.cron import (
        run_abandoned_crm,
        run_agent_sweep,
        run_attention_emails,
        run_cleanup_stale_ready,
        run_close_stale_presence,
        run_crm_orphan_backstop,
        run_db_saturation_alert,
        run_queue_stall_alert,
    )

    # (name, fn, interval_override) — None = the shared default cadence.
    # The CRM backstop runs every 30 min: fast enough that a fresh failed
    # push retries within the client's attention window, slow enough that
    # 3 attempts spread over a real CRM outage instead of burning out in
    # minutes.
    jobs = [
        ("abandoned_crm", run_abandoned_crm, None),
        # One sweep for everyone. Interval from settings (15s): the heartbeat no
        # longer sweeps, so this is now the only thing that falls conversations
        # back — it must run reliably and not slower than a fraction of the 90s
        # first-response deadline.
        ("agent_sweep", run_agent_sweep, settings.agent_sweep_interval_seconds),
        ("db_saturation_alert", run_db_saturation_alert, 60),
        ("stale_ready", run_cleanup_stale_ready, None),
        ("stale_presence", run_close_stale_presence, None),
        ("attention_emails", run_attention_emails, None),
        ("crm_orphan_backstop", run_crm_orphan_backstop, 1800),
        # Every 60s: the two-minute stall must be noticed inside the client's
        # attention window, not on the shared cadence.
        ("queue_stall_alert", run_queue_stall_alert, 60),
    ]
    tasks = [
        asyncio.create_task(
            _run_forever(name, fn, interval or settings.scheduler_interval_seconds),
            name=f"scheduler_{name}",
        )
        for name, fn, interval in jobs
    ]
    logger.info(
        f"[scheduler:internal] started {len(tasks)} jobs @ "
        f"{settings.scheduler_interval_seconds}s"
    )
    return tasks
