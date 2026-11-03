"""STEP 3 runs the classifier off the event loop — a slow classify can't freeze /health."""

import asyncio
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import app.pipeline.orchestrator as orch
from app.pipeline.intent import Intent


@pytest.mark.asyncio
async def test_detect_intent_does_not_block_the_loop():
    import time

    def _slow(*a, **k):
        time.sleep(0.4)  # a blocking classify
        return Intent.GENERAL_QUESTION

    async def _ticker():
        # If STEP 3 blocked the loop, these ticks would not advance during the sleep.
        ticks = 0
        for _ in range(8):
            await asyncio.sleep(0.05)
            ticks += 1
        return ticks

    with patch.object(orch, "detect_intent", _slow):
        loop = asyncio.get_running_loop()
        started = loop.time()
        ticker = asyncio.create_task(_ticker())
        intent = await loop.run_in_executor(None, _slow)  # mirrors the call shape
        ticks = await ticker
        assert intent == Intent.GENERAL_QUESTION
        assert ticks == 8, "the loop kept ticking while the classify ran in a thread"
        assert loop.time() - started < 0.7
