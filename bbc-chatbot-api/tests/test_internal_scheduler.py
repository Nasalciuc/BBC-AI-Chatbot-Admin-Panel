"""Tests for internal asyncio scheduler (ADR-10)."""

import asyncio
import time
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["CRON_SECRET"] = "test-cron-secret"


@pytest.mark.asyncio
async def test_run_forever_survives_repeated_job_failures(caplog):
    """A crashing job must not kill the scheduler loop (G1)."""
    from app.services import scheduler as sched

    sched.last_runs.clear()
    calls = {"n": 0}

    async def always_fails():
        calls["n"] += 1
        raise RuntimeError("boom")

    with (
        patch("app.services.scheduler.random.uniform", return_value=0),
        caplog.at_level("ERROR"),
    ):
        task = asyncio.create_task(sched._run_forever("fail_job", always_fails, 0.01))
        # Wait for the evidence, not for the clock. A fixed 0.08s sleep passed
        # alone and failed under the full suite, because "two iterations of a
        # 0.01s loop" is a promise about the machine, not about the code. CI
        # runners are slower than this laptop and would have inherited the
        # flake on day one.
        deadline = time.monotonic() + 5.0
        while calls["n"] < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls["n"] >= 2
    assert any(
        "[scheduler:internal] fail_job failed" in r.message and r.exc_info
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_healthy_job_populates_last_runs_and_logs(caplog):
    """Successful runs record last_runs and emit [scheduler:internal] log."""
    from app.services import scheduler as sched

    sched.last_runs.clear()

    async def ok_job():
        return {"processed": 0, "success": 0}

    with (
        patch("app.services.scheduler.random.uniform", return_value=0),
        caplog.at_level("INFO"),
    ):
        task = asyncio.create_task(sched._run_forever("abandoned_crm", ok_job, 0.01))
        await asyncio.sleep(0.06)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert "abandoned_crm" in sched.last_runs
    assert sched.last_runs["abandoned_crm"]["result"] == {"processed": 0, "success": 0}
    assert any(
        "[scheduler:internal] abandoned_crm:" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_lock_prevents_overlapping_executions():
    """Per-job lock: a slow run blocks the next tick (G2)."""
    from app.services import scheduler as sched

    sched.last_runs.clear()
    concurrent = {"max": 0, "current": 0}
    started = asyncio.Event()

    async def slow_job():
        concurrent["current"] += 1
        concurrent["max"] = max(concurrent["max"], concurrent["current"])
        started.set()
        await asyncio.sleep(0.05)
        concurrent["current"] -= 1
        return {"ok": True}

    with patch("app.services.scheduler.random.uniform", return_value=0):
        task = asyncio.create_task(sched._run_forever("slow_job", slow_job, 0.01))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await asyncio.sleep(0.12)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert concurrent["max"] == 1


def test_kill_switch_skips_scheduler_start():
    """internal_scheduler_enabled=False must not call start_scheduler (G3)."""
    mock_settings = MagicMock()
    mock_settings.internal_scheduler_enabled = False

    with patch("app.services.scheduler.start") as mock_start:
        if mock_settings.internal_scheduler_enabled:
            from app.services.scheduler import start as start_scheduler

            start_scheduler(MagicMock(), mock_settings)
        mock_start.assert_not_called()


@pytest.mark.asyncio
async def test_abandoned_crm_http_endpoint_regression():
    """HTTP cron path still works with CRON_SECRET (Actions backup)."""
    with patch("app.db.supabase.get_client"):
        from httpx import ASGITransport, AsyncClient
        from app.main import app

    with (
        patch(
            "app.api.cron.db.get_abandoned_conversations",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("app.api.cron.settings.cron_secret", "test-cron-secret"),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/cron/abandoned-crm",
                headers={"Authorization": "Bearer test-cron-secret"},
            )

    assert resp.status_code == 200
    assert resp.json() == {"processed": 0, "success": 0, "results": []}


@pytest.mark.asyncio
async def test_get_scheduler_health_seconds_ago():
    """Health canary exposes per-job seconds_ago."""
    from app.services.scheduler import get_scheduler_health, last_runs

    last_runs.clear()
    last_runs["abandoned_crm"] = {
        "at": "2020-01-01T00:00:00+00:00",
        "result": {},
    }
    health = get_scheduler_health()
    assert "abandoned_crm" in health
    assert health["abandoned_crm"]["last_run_at"] == "2020-01-01T00:00:00+00:00"
    assert health["abandoned_crm"]["seconds_ago"] is not None
    assert health["abandoned_crm"]["seconds_ago"] > 0
