"""FIX-A / FIX-C — handoff hole (dead conversations on silent reservation).

FIX-A: human mode + announce_pending (operator assigned but not engaged)
       → the visitor's message must reach the AI pipeline, NOT the queue.
FIX-C: fall_back_to_ai must re-process the last visitor message when it
       never received an ai/agent reply.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.models.chat import ChatRequest, ChatResponse, VisitorInfo
from app.api.chat import chat

_CONV_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_VISITOR = VisitorInfo(name="Test", email="t@example.com", phone="+15551234567")


def _request_mock() -> MagicMock:
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="127.0.0.1")
    return req


def _chat_request(**kwargs) -> ChatRequest:
    defaults = {
        "message": "Dulles",
        "conversation_id": _CONV_ID,
        "tunnel": "sales",
        "visitor": _VISITOR,
        "visitor_id": "visitor-uuid-1",
    }
    defaults.update(kwargs)
    return ChatRequest(**defaults)


async def _call_chat(**kwargs):
    return await chat(_chat_request(**kwargs), _request_mock(), None)


def _conv(metadata: dict | None = None, agent: str | None = "agent-1") -> dict:
    return {
        "id": _CONV_ID,
        "status": "active",
        "mode": "human",
        "assigned_agent_id": agent,
        "metadata": metadata or {},
    }


# ── 1. FIX-A: silent reservation → pipeline, NOT queue ───────────────


@pytest.mark.asyncio
async def test_silent_reservation_runs_pipeline_not_queue():
    pipeline_resp = ChatResponse(
        conversation_id=_CONV_ID,
        message="Great — Dulles it is! When would you like to fly?",
        type="ai",
        model_used="haiku",
    )
    conv = _conv(metadata={"announce_pending": True})
    with (
        patch("app.api.chat.db.count_messages", new_callable=AsyncMock, return_value=3),
        patch("app.api.chat.db.get_conversation_simple", new_callable=AsyncMock, return_value=conv),
        patch("app.api.chat.lead_service.get_or_create_lead", new_callable=AsyncMock, return_value=None),
        patch("app.api.chat.db.get_conversation_mode", new_callable=AsyncMock, return_value="human"),
        patch("app.api.chat.db.get_conversation", new_callable=AsyncMock, return_value=conv),
        patch(
            "app.services.handoff.is_agent_effectively_offline",
            new_callable=AsyncMock,
        ) as offline_mock,
        patch(
            "app.services.conversation_service.add_message",
            new_callable=AsyncMock,
        ) as queue_save_mock,
        patch(
            "app.api.chat.process_message",
            new_callable=AsyncMock,
            return_value=pipeline_resp,
        ) as pipeline_mock,
        patch("app.services.response_cache.get_cached", return_value=None),
        patch("app.services.response_cache.set_cached"),
        patch("app.pipeline.intent.detect_intent", return_value=MagicMock(value="other")),
    ):
        resp = await _call_chat()

    pipeline_mock.assert_awaited_once()
    offline_mock.assert_not_awaited()   # short-circuited by the reservation check
    queue_save_mock.assert_not_awaited()  # pipeline saves the message, not the queue
    assert resp.type == "ai"
    assert resp.message == pipeline_resp.message


# ── 2. Engaged operator (no announce_pending) → still queues ─────────


@pytest.mark.asyncio
async def test_engaged_operator_still_queues():
    conv = _conv(metadata={})  # announce_pending absent → operator engaged
    with (
        patch("app.api.chat.db.count_messages", new_callable=AsyncMock, return_value=3),
        patch("app.api.chat.db.get_conversation_simple", new_callable=AsyncMock, return_value=conv),
        patch("app.api.chat.lead_service.get_or_create_lead", new_callable=AsyncMock, return_value=None),
        patch("app.api.chat.db.get_conversation_mode", new_callable=AsyncMock, return_value="human"),
        patch(
            "app.services.handoff.is_agent_effectively_offline",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "app.services.conversation_service.add_message",
            new_callable=AsyncMock,
            return_value={"id": "msg-1"},
        ) as save_mock,
        patch(
            "app.services.handoff._handoff_phrase_recently_sent",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("app.api.chat.process_message", new_callable=AsyncMock) as pipeline_mock,
    ):
        resp = await _call_chat()

    save_mock.assert_awaited_once()
    pipeline_mock.assert_not_awaited()
    assert resp.type == "queued"


# ── 3. Agent offline (no announce_pending) → still falls back ────────


@pytest.mark.asyncio
async def test_offline_agent_still_falls_back():
    conv = _conv(metadata={})
    with (
        patch("app.api.chat.db.count_messages", new_callable=AsyncMock, return_value=3),
        patch("app.api.chat.db.get_conversation_simple", new_callable=AsyncMock, return_value=conv),
        patch("app.api.chat.lead_service.get_or_create_lead", new_callable=AsyncMock, return_value=None),
        patch("app.api.chat.db.get_conversation_mode", new_callable=AsyncMock, return_value="human"),
        patch(
            "app.services.handoff.is_agent_effectively_offline",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.services.conversation_service.add_message",
            new_callable=AsyncMock,
            return_value={"id": "msg-1"},
        ),
        patch(
            "app.services.handoff.fall_back_to_ai",
            new_callable=AsyncMock,
        ) as fallback_mock,
        patch("app.api.chat.process_message", new_callable=AsyncMock) as pipeline_mock,
    ):
        resp = await _call_chat()

    fallback_mock.assert_awaited_once_with(_CONV_ID)
    pipeline_mock.assert_not_awaited()
    assert resp.type == "fallback"


# ── 4. FIX-C: fall_back_to_ai reprocesses an unanswered user message ─


def _fallback_patches(last_msgs, has_reply_after, fired: list):
    conv = {
        "id": _CONV_ID,
        "assigned_agent_id": None,
        "metadata": {"announce_pending": True},
    }

    def _capture(coro):
        fired.append(coro)
        coro.close()  # prevent "never awaited" warnings

    return (
        patch("app.services.handoff.db.get_conversation_simple", new_callable=AsyncMock, return_value=conv),
        patch("app.services.handoff.db.update_conversation", new_callable=AsyncMock),
        patch(
            "app.services.handoff._handoff_phrase_recently_sent",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("app.services.handoff.db.get_recent_messages", new_callable=AsyncMock, return_value=last_msgs),
        patch(
            "app.services.handoff.db.has_ai_or_agent_message_since",
            new_callable=AsyncMock,
            return_value=has_reply_after,
        ),
        patch("app.pipeline.orchestrator._fire_and_forget", side_effect=_capture),
        patch(
            "app.pipeline.orchestrator.reprocess_last_user_message",
            new_callable=AsyncMock,
        ),
    )


@pytest.mark.asyncio
async def test_fallback_reprocesses_unanswered_user_message():
    from app.services.handoff import fall_back_to_ai

    fired: list = []
    last_msgs = [{"role": "user", "content": "Dulles", "created_at": "2026-07-10T10:00:00+00:00"}]
    p1, p2, p3, p4, p5, p6, p7 = _fallback_patches(last_msgs, has_reply_after=False, fired=fired)
    with p1, p2, p3, p4, p5, p6, p7 as reprocess_mock:
        await fall_back_to_ai(_CONV_ID)

    reprocess_mock.assert_called_once_with(_CONV_ID)
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_fallback_skips_reprocess_when_already_answered():
    from app.services.handoff import fall_back_to_ai

    fired: list = []
    last_msgs = [{"role": "user", "content": "Dulles", "created_at": "2026-07-10T10:00:00+00:00"}]
    p1, p2, p3, p4, p5, p6, p7 = _fallback_patches(last_msgs, has_reply_after=True, fired=fired)
    with p1, p2, p3, p4, p5, p6, p7 as reprocess_mock:
        await fall_back_to_ai(_CONV_ID)

    reprocess_mock.assert_not_called()
    assert fired == []


@pytest.mark.asyncio
async def test_fallback_skips_reprocess_when_last_is_ai():
    from app.services.handoff import fall_back_to_ai

    fired: list = []
    last_msgs = [{"role": "ai", "content": "Anything else?", "created_at": "2026-07-10T10:00:00+00:00"}]
    p1, p2, p3, p4, p5, p6, p7 = _fallback_patches(last_msgs, has_reply_after=False, fired=fired)
    with p1, p2, p3, p4, p5, p6, p7 as reprocess_mock:
        await fall_back_to_ai(_CONV_ID)

    reprocess_mock.assert_not_called()
    assert fired == []
