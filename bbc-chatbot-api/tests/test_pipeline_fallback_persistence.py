"""Pinning tests — pipeline failure handlers must persist fallback AI rows."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.models.chat import ChatResponse, VisitorInfo
from app.pipeline.orchestrator import process_message

_FALLBACK_TEXT = "Let me connect you with a specialist right away."
_CONV_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_VISITOR = VisitorInfo(name="Test", email="t@example.com", phone="+15551234567")


@pytest.fixture
def mock_template():
    with patch(
        "app.pipeline.orchestrator.get_template",
        return_value=_FALLBACK_TEXT,
    ):
        yield


@pytest.mark.asyncio
async def test_exception_after_extraction_persists_fallback_once(mock_template):
    """Pipeline Exception → HTTP fallback + exactly one template_fallback row."""
    with patch(
        "app.pipeline.orchestrator._pipeline",
        new_callable=AsyncMock,
        side_effect=RuntimeError("after extraction"),
    ):
        with patch(
            "app.pipeline.orchestrator.conversation_service.add_message",
            new_callable=AsyncMock,
            return_value={"id": "fb-1"},
        ) as add_msg:
            resp = await process_message(
                _CONV_ID, "Toronto to Istanbul", "sales", _VISITOR
            )

    assert resp.message == _FALLBACK_TEXT
    assert resp.type == "template_fallback"
    add_msg.assert_awaited_once()
    _kwargs = add_msg.await_args.kwargs
    assert _kwargs["conversation_id"] == _CONV_ID
    assert _kwargs["role"] == "ai"
    assert _kwargs["content"] == _FALLBACK_TEXT
    assert _kwargs["model_used"] == "template_fallback"
    assert _kwargs["cost"] == 0.0


@pytest.mark.asyncio
async def test_timeout_persists_fallback_once(mock_template):
    """Pipeline TimeoutError → HTTP fallback + exactly one template_fallback row."""
    with patch(
        "app.pipeline.orchestrator.asyncio.wait_for",
        new_callable=AsyncMock,
        side_effect=asyncio.TimeoutError(),
    ):
        with patch(
            "app.pipeline.orchestrator.conversation_service.add_message",
            new_callable=AsyncMock,
            return_value={"id": "fb-2"},
        ) as add_msg:
            resp = await process_message(_CONV_ID, "hello", "sales", _VISITOR)

    assert resp.message == _FALLBACK_TEXT
    add_msg.assert_awaited_once()
    assert add_msg.await_args.kwargs["model_used"] == "template_fallback"


@pytest.mark.asyncio
async def test_post_save_crash_does_not_duplicate_fallback(mock_template):
    """If AI was already persisted, failure handlers must not write a second row."""

    async def _pipeline_post_save_crash(*_args, _persist_state=None, **_kwargs):
        if _persist_state is not None:
            _persist_state["ai_persisted"] = True
        raise RuntimeError("post-save crash")

    with patch(
        "app.pipeline.orchestrator._pipeline",
        side_effect=_pipeline_post_save_crash,
    ):
        with patch(
            "app.pipeline.orchestrator.conversation_service.add_message",
            new_callable=AsyncMock,
        ) as add_msg:
            resp = await process_message(_CONV_ID, "hello", "sales", _VISITOR)

    assert resp.message == _FALLBACK_TEXT
    add_msg.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_message_none_on_normal_path_logs_error_and_continues(
    mock_template, caplog
):
    """add_message returning None on the normal AI save path is loud, not silent."""
    from app.pipeline.generator import GeneratedResponse
    from app.pipeline.orchestrator import _pipeline

    gen = GeneratedResponse(text="AI says hi", model_used="haiku", cost=0.001)

    async def _add_message_side_effect(**kwargs):
        if kwargs.get("role") == "user":
            return {"id": "user-1"}
        if kwargs.get("role") == "ai":
            return None
        return {"id": "other-1"}

    with (
        patch(
            "app.pipeline.orchestrator.conversation_service.get_or_create_conversation",
            new_callable=AsyncMock,
            return_value={"id": _CONV_ID},
        ),
        patch(
            "app.pipeline.orchestrator.conversation_service.add_message",
            new_callable=AsyncMock,
            side_effect=_add_message_side_effect,
        ),
        patch("app.pipeline.orchestrator.db.get_recent_messages", new_callable=AsyncMock, return_value=[]),
        patch(
            "app.pipeline.orchestrator.lead_service.get_or_create_lead",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "app.pipeline.orchestrator.lead_service.update_lead_from_entities",
            new_callable=AsyncMock,
        ),
        patch("app.pipeline.orchestrator.detect_intent", return_value=__import__("app.pipeline.intent", fromlist=["Intent"]).Intent.GREETING),
        patch("app.pipeline.orchestrator.db.get_today_cost", new_callable=AsyncMock, return_value=0.0),
        patch(
            "app.pipeline.orchestrator.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=gen,
        ),
        patch("app.pipeline.orchestrator.validate_response", side_effect=lambda t: t),
        patch("app.pipeline.orchestrator.db.get_conversation_mode", new_callable=AsyncMock, return_value="ai"),
        patch("app.pipeline.orchestrator.db.create_pipeline_run", new_callable=AsyncMock),
        patch("app.pipeline.orchestrator.manager.push_stream_end", new_callable=AsyncMock),
    ):
        caplog.set_level("ERROR")
        resp = await _pipeline(
            _CONV_ID,
            "hello",
            "sales",
            _VISITOR,
            None,
            _persist_state={"ai_persisted": False},
        )

    assert isinstance(resp, ChatResponse)
    assert "AI reply generated but NOT persisted" in caplog.text
    assert not caplog.records[-1].exc_info  # flow completed without re-raise
