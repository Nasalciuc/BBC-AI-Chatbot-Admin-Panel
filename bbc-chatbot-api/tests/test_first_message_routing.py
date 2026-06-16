"""First-message routing: operator-first with AI fallback."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.models.chat import ChatRequest, ChatResponse, VisitorInfo
from app.api.chat import chat

_CONV_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_AGENT_ID = "11111111-2222-3333-4444-555555555555"
_VISITOR = VisitorInfo(name="Test", email="t@example.com", phone="+15551234567")


def _request_mock() -> MagicMock:
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="127.0.0.1")
    return req


def _chat_request(**kwargs) -> ChatRequest:
    defaults = {
        "message": "Hello",
        "conversation_id": _CONV_ID,
        "tunnel": "sales",
        "visitor": _VISITOR,
        "visitor_id": "visitor-uuid-1",
    }
    defaults.update(kwargs)
    return ChatRequest(**defaults)


async def _call_chat(**kwargs):
    return await chat(_chat_request(**kwargs), _request_mock(), None)


@pytest.fixture
def _fresh_ai_conv():
    return {"id": _CONV_ID, "mode": "ai", "assigned_agent_id": None, "status": "active"}


@pytest.mark.asyncio
async def test_first_message_routes_to_available_agent(_fresh_ai_conv):
    pipeline_resp = ChatResponse(
        conversation_id=_CONV_ID,
        message="should not run",
        type="ai",
        model_used="haiku",
    )
    with (
        patch("app.api.chat.db.count_messages", new_callable=AsyncMock, return_value=0),
        patch("app.api.chat.db.get_conversation", new_callable=AsyncMock, return_value=_fresh_ai_conv),
        patch("app.api.chat.db.get_conversation_mode", new_callable=AsyncMock, return_value="ai"),
        patch("app.api.chat.lead_service.get_or_create_lead", new_callable=AsyncMock, return_value=None),
        patch(
            "app.api.chat.route_conversation",
            new_callable=AsyncMock,
            return_value={"agent_id": _AGENT_ID, "agent_name": "Emma", "mode": "human"},
        ) as route_mock,
        patch(
            "app.api.chat.perform_handoff_to_agent",
            new_callable=AsyncMock,
            return_value={},
        ) as handoff_mock,
        patch(
            "app.api.chat.process_message",
            new_callable=AsyncMock,
            return_value=pipeline_resp,
        ) as pipeline_mock,
        patch("app.services.response_cache.get_cached", return_value=None),
        patch("app.services.response_cache.set_cached"),
        patch(
            "app.pipeline.intent.detect_intent",
            return_value=MagicMock(value="greeting"),
        ),
    ):
        resp = await _call_chat()

    route_mock.assert_awaited_once()
    handoff_mock.assert_awaited_once()
    assert handoff_mock.await_args.kwargs["handoff_reason"] == "first_message"
    assert handoff_mock.await_args.kwargs["emit_messages"] is False
    pipeline_mock.assert_awaited_once()
    assert resp.type == "ai"
    assert resp.message == pipeline_resp.message


@pytest.mark.asyncio
async def test_first_message_saves_user_via_pipeline(_fresh_ai_conv):
    pipeline_resp = ChatResponse(
        conversation_id=_CONV_ID,
        message="Hi! Where would you like to fly?",
        type="ai",
        model_used="haiku",
    )
    with (
        patch("app.api.chat.db.count_messages", new_callable=AsyncMock, return_value=0),
        patch("app.api.chat.db.get_conversation", new_callable=AsyncMock, return_value=_fresh_ai_conv),
        patch("app.api.chat.db.get_conversation_mode", new_callable=AsyncMock, return_value="ai"),
        patch("app.api.chat.lead_service.get_or_create_lead", new_callable=AsyncMock, return_value=None),
        patch(
            "app.api.chat.route_conversation",
            new_callable=AsyncMock,
            return_value={"agent_id": _AGENT_ID, "agent_name": "Emma", "mode": "human"},
        ),
        patch("app.api.chat.perform_handoff_to_agent", new_callable=AsyncMock, return_value={}),
        patch(
            "app.api.chat.process_message",
            new_callable=AsyncMock,
            return_value=pipeline_resp,
        ) as pipeline_mock,
        patch("app.services.response_cache.get_cached", return_value=None),
        patch("app.services.response_cache.set_cached"),
        patch(
            "app.pipeline.intent.detect_intent",
            return_value=MagicMock(value="greeting"),
        ),
    ):
        await _call_chat()

    pipeline_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_first_message_no_agent_falls_through_pipeline(_fresh_ai_conv):
    pipeline_resp = ChatResponse(
        conversation_id=_CONV_ID,
        message="Hi! Where would you like to fly?",
        type="ai",
        model_used="haiku",
    )
    with (
        patch("app.api.chat.db.count_messages", new_callable=AsyncMock, return_value=0),
        patch("app.api.chat.db.get_conversation", new_callable=AsyncMock, return_value=_fresh_ai_conv),
        patch("app.api.chat.db.get_conversation_mode", new_callable=AsyncMock, return_value="ai"),
        patch("app.api.chat.lead_service.get_or_create_lead", new_callable=AsyncMock, return_value=None),
        patch(
            "app.api.chat.route_conversation",
            new_callable=AsyncMock,
            return_value={"agent_id": None, "mode": "ai", "agent_name": None},
        ),
        patch("app.api.chat.perform_handoff_to_agent", new_callable=AsyncMock) as handoff_mock,
        patch(
            "app.api.chat.process_message",
            new_callable=AsyncMock,
            return_value=pipeline_resp,
        ) as pipeline_mock,
        patch(
            "app.services.response_cache.get_cached",
            return_value=None,
        ),
        patch("app.services.response_cache.set_cached"),
        patch(
            "app.pipeline.intent.detect_intent",
            return_value=MagicMock(value="GREETING"),
        ),
    ):
        resp = await _call_chat()

    handoff_mock.assert_not_awaited()
    pipeline_mock.assert_awaited_once()
    assert resp.message == pipeline_resp.message
    assert resp.type == "ai"


@pytest.mark.asyncio
async def test_first_message_gate_requires_zero_messages(_fresh_ai_conv):
    with (
        patch("app.api.chat.db.count_messages", new_callable=AsyncMock, return_value=3),
        patch("app.api.chat.db.get_conversation", new_callable=AsyncMock, return_value=_fresh_ai_conv),
        patch("app.api.chat.db.get_conversation_mode", new_callable=AsyncMock, return_value="ai"),
        patch("app.api.chat.lead_service.get_or_create_lead", new_callable=AsyncMock, return_value=None),
        patch("app.api.chat.route_conversation", new_callable=AsyncMock) as route_mock,
        patch(
            "app.api.chat.process_message",
            new_callable=AsyncMock,
            return_value=ChatResponse(
                conversation_id=_CONV_ID,
                message="ok",
                type="ai",
                model_used="haiku",
            ),
        ) as pipeline_mock,
        patch(
            "app.services.response_cache.get_cached",
            return_value=None,
        ),
        patch("app.services.response_cache.set_cached"),
        patch(
            "app.pipeline.intent.detect_intent",
            return_value=MagicMock(value="GREETING"),
        ),
    ):
        await _call_chat()

    route_mock.assert_not_awaited()
    pipeline_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_first_message_gate_requires_ai_mode():
    with (
        patch("app.api.chat.db.get_conversation_simple", new_callable=AsyncMock, return_value=None),
        patch("app.api.chat.db.get_conversation_mode", new_callable=AsyncMock, return_value="human"),
        patch(
            "app.services.handoff.is_agent_effectively_offline",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "app.api.chat.conversation_service.add_message",
            new_callable=AsyncMock,
            return_value={"id": "msg-1"},
        ),
        patch(
            "app.services.handoff._handoff_phrase_recently_sent",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("app.api.chat.route_conversation", new_callable=AsyncMock) as route_mock,
    ):
        resp = await _call_chat()

    route_mock.assert_not_awaited()
    assert resp.type == "queued"


@pytest.mark.asyncio
async def test_first_message_handoff_before_pipeline(_fresh_ai_conv):
    call_order: list[str] = []

    async def _handoff(**kwargs):
        call_order.append("perform_handoff")
        return {}

    async def _pipeline(**kwargs):
        call_order.append("process_message")
        return ChatResponse(
            conversation_id=_CONV_ID,
            message="Hi!",
            type="ai",
            model_used="haiku",
        )

    with (
        patch("app.api.chat.db.count_messages", new_callable=AsyncMock, return_value=0),
        patch("app.api.chat.db.get_conversation", new_callable=AsyncMock, return_value=_fresh_ai_conv),
        patch("app.api.chat.db.get_conversation_mode", new_callable=AsyncMock, return_value="ai"),
        patch("app.api.chat.lead_service.get_or_create_lead", new_callable=AsyncMock, return_value=None),
        patch(
            "app.api.chat.route_conversation",
            new_callable=AsyncMock,
            return_value={"agent_id": _AGENT_ID, "agent_name": "Emma", "mode": "human"},
        ),
        patch("app.api.chat.perform_handoff_to_agent", side_effect=_handoff),
        patch("app.api.chat.process_message", side_effect=_pipeline),
        patch("app.services.response_cache.get_cached", return_value=None),
        patch("app.services.response_cache.set_cached"),
        patch(
            "app.pipeline.intent.detect_intent",
            return_value=MagicMock(value="greeting"),
        ),
    ):
        await _call_chat()

    assert call_order == ["perform_handoff", "process_message"]


def test_is_ready_default_false():
    migration = (
        Path(__file__).resolve().parent.parent
        / "migrations"
        / "016_is_ready_default_false.sql"
    )
    text = migration.read_text(encoding="utf-8")
    assert "ALTER COLUMN is_ready SET DEFAULT false" in text


@pytest.mark.asyncio
async def test_fallback_resets_assign_count():
    from app.services.handoff import fall_back_to_ai

    captured: dict = {}

    async def _fake_update(conv_id, payload):
        captured.update(payload)

    with (
        patch(
            "app.services.handoff.db.get_conversation_simple",
            new_callable=AsyncMock,
            return_value={
                "id": _CONV_ID,
                "metadata": {
                    "agent_assign_count": 3,
                    "agent_cooldown_until": "2099-01-01T00:00:00+00:00",
                },
            },
        ),
        patch("app.services.handoff.db.update_conversation", side_effect=_fake_update),
        patch(
            "app.services.handoff._handoff_phrase_recently_sent",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        await fall_back_to_ai(_CONV_ID)

    meta = captured.get("metadata", {})
    assert "agent_assign_count" not in meta
    assert "agent_cooldown_until" not in meta
