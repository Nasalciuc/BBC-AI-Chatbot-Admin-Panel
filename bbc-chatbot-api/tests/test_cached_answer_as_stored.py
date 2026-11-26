"""A cached answer goes out as it was stored (app.api.chat, the cache hit): the output guard ran on it in
db.add_message for this conversation, as for the pipeline's answer. The words read from the cache were guarded for
the conversation they were made in, and never go out raw (CodeRabbit #288). Every text here is invented."""

import os
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api.chat import chat  # noqa: E402
from app.models.chat import ChatRequest, VisitorInfo  # noqa: E402

CONV = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _request() -> MagicMock:
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="127.0.0.1")
    return req


async def _cache_hit(cached: str, stored_ai):
    """One widget message whose answer is in the cache; `stored_ai` is what saving the answer gave back."""
    conv = {"id": CONV, "status": "active", "mode": "ai", "visitor_id": "visitor-1", "metadata": {"site": "bbc"}}
    add = AsyncMock(side_effect=[{"id": "m-user", "role": "user"}, stored_ai])
    pipeline = AsyncMock()
    with ExitStack() as stack:
        for target, kw in (
            ("app.api.chat.db.count_messages", {"new_callable": AsyncMock, "return_value": 3}),
            ("app.api.chat.db.get_conversation_simple", {"new_callable": AsyncMock, "return_value": conv}),
            ("app.api.chat.db.get_conversation", {"new_callable": AsyncMock, "return_value": conv}),
            ("app.api.chat.db.get_conversation_mode", {"new_callable": AsyncMock, "return_value": "ai"}),
            ("app.api.chat.lead_service.get_or_create_lead", {"new_callable": AsyncMock, "return_value": None}),
            ("app.api.chat.is_blocked", {"new_callable": AsyncMock, "return_value": False}),
            ("app.security.behavior.chat_door", {"new_callable": AsyncMock, "return_value": None}),
            ("app.security.behavior.frustration_door", {"new_callable": AsyncMock, "return_value": None}),
            ("app.api.chat._fire_and_forget", {"side_effect": lambda coro: coro.close()}),
            ("app.services.response_cache.get_cached", {"return_value": cached}),
            ("app.services.response_cache.set_cached", {}),
        ):
            stack.enter_context(patch(target, **kw))
        stack.enter_context(patch("app.api.chat.conversation_service.add_message", new=add))
        stack.enter_context(patch("app.api.chat.process_message", new=pipeline))
        resp = await chat(ChatRequest(message="How can I reach you?", conversation_id=CONV, tunnel="sales",
                                      visitor=VisitorInfo(), visitor_id="visitor-1"), _request(), None)
    pipeline.assert_not_awaited()
    return resp


@pytest.mark.asyncio
async def test_a_cached_answer_goes_out_as_it_was_stored():
    resp = await _cache_hit("Call our team any time, or 555-0100.",
                            {"id": "m-ai", "role": "ai", "content": "Call our team any time."})
    assert resp.message == "Call our team any time." and resp.model_used == "cache"


@pytest.mark.asyncio
async def test_a_cached_answer_that_could_not_be_stored_goes_out_guarded():
    """Rule 5: no stored row to read it from: the guard runs on the cached words here."""
    resp = await _cache_hit("Sure — any time.", None)
    assert resp.message == "Sure, any time."
