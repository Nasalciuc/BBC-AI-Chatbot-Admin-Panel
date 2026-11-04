"""STEP 3 runs the classifier off the event loop — a slow classify can't freeze /health."""

import asyncio
import threading
import os
import sys
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import app.pipeline.orchestrator as orch
from app.pipeline.intent import Intent


_CID = "aaaaaaaa-bbbb-cccc-dddd-0000000000aa"


class _StopAfterIntent(Exception):
    """Raised by the patched classifier: STEP 3 ran, nothing after it is needed."""


@pytest.mark.asyncio
async def test_step3_runs_detect_intent_off_the_event_loop():
    """Through the production call path (_pipeline), not a copy of its shape:
    the classifier must run in a worker thread while the loop keeps ticking."""
    import time

    from app.models.chat import VisitorInfo

    seen = {}

    def _slow(*a, **k):
        seen["thread"] = threading.get_ident()
        time.sleep(0.4)  # a blocking classify
        raise _StopAfterIntent()

    async def _ticker():
        ticks = 0
        for _ in range(6):
            await asyncio.sleep(0.05)
            ticks += 1
        return ticks

    conv = {"id": _CID, "mode": "ai", "status": "active", "tunnel": "sales", "metadata": {}}
    with ExitStack() as stack:
        for target, value in (
            ("app.services.conversation_service.get_or_create_conversation", AsyncMock(return_value=conv)),
            ("app.services.conversation_service.add_message", AsyncMock(return_value={"id": "m1"})),
            ("app.pipeline.orchestrator.db.get_recent_messages", AsyncMock(return_value=[])),
            ("app.services.moderation.moderate_message", AsyncMock()),
            ("app.pipeline.orchestrator.db.update_conversation", AsyncMock()),
        ):
            stack.enter_context(patch(target, new=value))
        stack.enter_context(patch.object(orch, "detect_intent", _slow))
        loop = asyncio.get_running_loop()
        ticker = asyncio.create_task(_ticker())
        with pytest.raises(_StopAfterIntent):
            await orch._pipeline(_CID, "hello", "sales", VisitorInfo(name="T"), None,
                                 _persist_state={"ai_persisted": False})
        ticks = await ticker
    assert seen["thread"] != threading.main_thread().ident, "detect_intent ran on the event loop's thread"
    assert ticks == 6, "the loop kept ticking while the classify ran in a thread"
