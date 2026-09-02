"""Background DB work waits its turn — the reply never does."""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.pipeline import orchestrator as orch  # noqa: E402


async def _reset_bg():
    # cancel() only schedules; the task's finally (running -= 1) runs LATER.
    # Await the cancelled tasks first, THEN zero the counters — otherwise a
    # late decrement drives `running` negative and poisons the next assertion.
    tasks = list(orch._background_tasks)
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    orch._background_tasks.clear()
    orch._bg_semaphore = None
    orch.BG_HEALTH.update({"running": 0, "peak_running": 0, "waited": 0})


@pytest.mark.asyncio
async def test_twenty_background_jobs_peak_at_eight_and_twelve_wait():
    await _reset_bg()
    inside = asyncio.Event()
    release = asyncio.Event()
    n_inside = 0

    async def hold():
        nonlocal n_inside
        n_inside += 1
        if n_inside >= 8:
            inside.set()
        await release.wait()

    for _ in range(20):
        orch._fire_and_forget(hold())
    await inside.wait()
    await asyncio.sleep(0.05)
    assert orch.BG_HEALTH["peak_running"] <= 8
    assert orch.BG_HEALTH["peak_running"] == 8
    assert orch.BG_HEALTH["waited"] == 12
    release.set()
    if orch._background_tasks:
        await asyncio.gather(*list(orch._background_tasks), return_exceptions=True)
    assert orch.BG_HEALTH["running"] == 0


@pytest.mark.asyncio
async def test_a_failing_job_does_not_jam_the_semaphore():
    await _reset_bg()

    async def boom():
        raise RuntimeError("bg failed")

    orch._fire_and_forget(boom())
    await asyncio.sleep(0.05)
    if orch._background_tasks:
        await asyncio.gather(*list(orch._background_tasks), return_exceptions=True)
    assert orch.BG_HEALTH["running"] == 0

    async def ok():
        return 1

    orch._fire_and_forget(ok())
    await asyncio.sleep(0.05)
    if orch._background_tasks:
        await asyncio.gather(*list(orch._background_tasks), return_exceptions=True)
    assert orch.BG_HEALTH["running"] == 0


@pytest.mark.asyncio
async def test_reset_awaits_cancelled_tasks_before_zeroing_counters():
    await _reset_bg()
    started = asyncio.Event()
    blocker = asyncio.Event()

    async def hold():
        started.set()
        await blocker.wait()

    orch._fire_and_forget(hold())
    await started.wait()
    assert orch.BG_HEALTH["running"] == 1
    await _reset_bg()
    assert orch.BG_HEALTH["running"] == 0
    await asyncio.sleep(0)
    assert orch.BG_HEALTH["running"] == 0
    assert orch.BG_HEALTH["running"] >= 0
