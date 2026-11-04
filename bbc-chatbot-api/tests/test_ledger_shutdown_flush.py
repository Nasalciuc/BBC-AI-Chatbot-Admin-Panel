"""CodeRabbit #226: shutdown waits for the ledger's final flush.

cancel() only schedules the cancellation; the last flush_once runs in the
flusher's CancelledError handler. Without the await, the event loop could
close first and the last rows of ai_calls never reached the table."""

import asyncio
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

with patch("app.db.supabase.get_client"):
    from app import main

from app.ai import ledger


@pytest.mark.asyncio
async def test_shutdown_waits_for_the_final_flush(monkeypatch):
    ledger._reset_for_tests()
    written = []

    async def _slow_insert(rows):
        await asyncio.sleep(0.05)  # the write takes a moment, as a real one does
        written.extend(rows)

    monkeypatch.setattr(ledger, "_insert", _slow_insert)
    task = asyncio.create_task(ledger._flush_forever())
    await asyncio.sleep(0)  # parked in the 2 s sleep
    ledger.record_ai_call(task="classify", model="claude-haiku-4-5-20251001", cost=0.001)
    monkeypatch.setattr(main.app.state, "ledger_task", task, raising=False)
    monkeypatch.setattr(main.app.state, "flags_task", None, raising=False)
    monkeypatch.setattr(main.app.state, "scheduler_tasks", [], raising=False)

    await main.shutdown()

    assert task.done()
    assert len(written) == 1, "the queued row was lost at shutdown"
    ledger._reset_for_tests()
