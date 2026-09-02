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


def _reset_bg():
    orch._bg_semaphore = None
    orch.BG_HEALTH.update({"running": 0, "peak_running": 0, "waited": 0})
    for t in list(orch._background_tasks):
        t.cancel()
        orch._background_tasks.discard(t)


@pytest.mark.asyncio
async def test_twenty_background_jobs_peak_at_eight_and_twelve_wait():
    _reset_bg()
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
    _reset_bg()

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
