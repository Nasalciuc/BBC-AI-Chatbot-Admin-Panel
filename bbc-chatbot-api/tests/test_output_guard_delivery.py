"""CodeRabbit #226: the output guard guards what reaches the customer.

The guard ran only on the stored reply. The reply in the response, the
greeting and every streamed chunk were the model's raw text, so in
OUTPUT_GUARD_MODE=enforce a foreign contact still reached the customer while
the stored copy was clean. Through the production path (_pipeline).
"""

import os
import sys
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags  # noqa: E402
from app.ai import output_guard  # noqa: E402
from app.models.chat import VisitorInfo  # noqa: E402
from app.pipeline import orchestrator as orch  # noqa: E402
from app.pipeline.intent import Intent  # noqa: E402

_CID = "aaaaaaaa-bbbb-cccc-dddd-0000000000bb"
_RAW = "Great choice. Book it at evil-deals.com today. Anything else?"
_STORED = "Great choice. Book it at [removed] today. Anything else?"


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    flags._reset_for_tests()
    output_guard._reset_for_tests()
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "enforce")
    yield
    flags._reset_for_tests()


async def _run(gen, stored_row):
    conv = {"id": _CID, "mode": "ai", "status": "active", "tunnel": "sales", "metadata": {}}
    pushed: list[str] = []

    async def _chunk(cid, delta):
        pushed.append(delta)

    with ExitStack() as stack:
        for target, value in (
            ("app.services.conversation_service.get_or_create_conversation", AsyncMock(return_value=conv)),
            ("app.services.conversation_service.add_message", AsyncMock(side_effect=lambda *a, **k: (
                stored_row if k.get("role") == "ai" else {"id": "u1"}))),
            ("app.pipeline.orchestrator.db.get_recent_messages", AsyncMock(return_value=[])),
            ("app.services.moderation.moderate_message", AsyncMock()),
            ("app.pipeline.orchestrator.db.update_conversation", AsyncMock()),
            ("app.pipeline.orchestrator.lead_service.get_or_create_lead", AsyncMock(return_value={"id": "l1"})),
            ("app.pipeline.orchestrator.db.update_lead", AsyncMock()),
            ("app.services.email.send_ops_alert_email", AsyncMock()),
            ("app.pipeline.orchestrator.db.get_conversation_mode", AsyncMock(return_value="ai")),
            ("app.pipeline.orchestrator.db.get_conversation_simple", AsyncMock(return_value=dict(conv))),
            ("app.db.supabase.get_conversation_simple", AsyncMock(return_value=dict(conv))),
        ):
            stack.enter_context(patch(target, new=value))
        stack.enter_context(patch.object(orch, "detect_intent", lambda *a, **k: Intent.GENERAL_QUESTION))
        stack.enter_context(patch.object(orch, "generate_response", gen))
        stack.enter_context(patch.object(orch.manager, "push_chunk", _chunk))
        stack.enter_context(patch.object(orch.manager, "push_stream_end", AsyncMock()))
        resp = await orch._pipeline(_CID, "Is there a cheaper option to London?", "sales",
                                    VisitorInfo(name="Ana"), None, _persist_state={"ai_persisted": False})
    return resp, pushed


def _reply(**_kw):
    return SimpleNamespace(text=_RAW, model_used="sonnet", cost=0.001, route_card=None, tool_entities=None)


@pytest.mark.asyncio
async def test_the_reply_in_the_response_is_the_stored_one():
    resp, _ = await _run(_reply, {"id": "a1", "content": _STORED})
    assert resp.message == _STORED and "evil-deals" not in resp.message


@pytest.mark.asyncio
async def test_a_reply_that_wasnt_stored_is_still_guarded():
    resp, _ = await _run(_reply, None)
    assert resp.message == _STORED


@pytest.mark.asyncio
async def test_streamed_chunks_go_out_scrubbed_a_sentence_at_a_time():
    def _streams(**kw):
        for piece in ("Great choice. Book it at evil-", "deals.com today. Any", "thing else?"):
            kw["on_chunk"](piece)
        return _reply()

    resp, pushed = await _run(_streams, {"id": "a1", "content": _STORED})
    assert resp.streaming and resp.message == ""
    sent = "".join(pushed)
    assert "evil" not in sent and "deals.com" not in sent
    # The last sentence is still held: stream_end brings the stored reply.
    assert sent == "Great choice. Book it at [removed] today. "
