"""/api/chat/start — the AI greets first; no fake client message.

The old widget auto-sent "Hello, I'm looking for business class flights."
AS the client on every fresh session. These tests pin the replacement:
/start creates the conversation with exactly one role="ai" opening, zero
user messages (last_user_message_at stays NULL → no_engagement derivable),
the live path personalizes, failures fall back to templates, and a second
/start never duplicates the conversation. Plus: a client message implies
presence=online (guarded), and stage counters ignore the AI opening.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api.chat import ChatStartRequest, chat_start, _mark_client_active
from app.ai.templates import TEMPLATES
from app.models.chat import VisitorInfo
from app.pipeline.generator import GeneratedResponse
from app.pipeline.intent import Intent

_CONV_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _request_mock() -> MagicMock:
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="127.0.0.1")
    return req


def _start_request(**kwargs) -> ChatStartRequest:
    defaults = {
        "tunnel": "sales",
        "visitor": VisitorInfo(name="Sheniquia", email="s@example.com", phone="+15551234567"),
        "metadata": {"utm_source": "google", "page_url": "/flight/country/france"},
        "visitor_id": "visitor-uuid-1",
    }
    defaults.update(kwargs)
    return ChatStartRequest(**defaults)


def _patches(conv=None, msg_count=0, gen=None, gen_raises=False):
    conv = conv or {"id": _CONV_ID}
    gen_mock = MagicMock()
    if gen_raises:
        gen_mock.side_effect = RuntimeError("LLM down")
    else:
        gen_mock.return_value = gen
    return (
        patch("app.api.chat.is_blocked", new=AsyncMock(return_value=False)),
        patch(
            "app.services.conversation_service.get_or_create_conversation",
            new=AsyncMock(return_value=conv),
        ),
        patch("app.api.chat.db.count_messages", new=AsyncMock(return_value=msg_count)),
        patch("app.pipeline.generator.generate_response", gen_mock),
        patch("app.services.conversation_service.add_message", new=AsyncMock()),
    )


class TestChatStart:
    @pytest.mark.asyncio
    async def test_creates_conv_one_ai_message_zero_user_messages(self):
        gen = GeneratedResponse(
            text="Good evening, Sheniquia! I see Paris is calling — when are you planning to travel?",
            model_used="sonnet",
            cost=0.004,
        )
        p_block, p_conv, p_count, p_gen, p_add = _patches(gen=gen)
        with p_block, p_conv, p_count, p_gen as gen_mock, p_add as add_mock:
            res = await chat_start(_start_request(), _request_mock(), None)

        assert res.conversation_id == _CONV_ID
        assert "Paris is calling" in res.message
        # Exactly ONE message written, and it is the AI's — never role="user".
        assert add_mock.await_count == 1
        args, kwargs = add_mock.await_args
        assert args[1] == "ai"
        assert args[2] == res.message
        roles = [c.args[1] for c in add_mock.await_args_list]
        assert "user" not in roles
        # The live path got the personalization inputs (metadata → SITE CONTEXT).
        gen_args, gen_kwargs = gen_mock.call_args
        entities = gen_args[1]
        assert entities["_metadata"]["utm_source"] == "google"
        assert gen_args[0] == Intent.GREETING

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_template(self):
        p_block, p_conv, p_count, p_gen, p_add = _patches(gen_raises=True)
        visitor = VisitorInfo()  # anonymous → anonymous welcome variants
        with p_block, p_conv, p_count, p_gen, p_add as add_mock:
            res = await chat_start(_start_request(visitor=visitor), _request_mock(), None)

        assert res.message in TEMPLATES["welcome:sales:anonymous"]
        assert add_mock.await_count == 1
        assert add_mock.await_args.args[1] == "ai"

    @pytest.mark.asyncio
    async def test_support_greets_by_template_without_llm(self):
        # No mocking of generate_response internals needed: support GREETING is
        # template-only inside the generator — no LLM call can happen.
        p_block, p_conv, p_count, _p_gen, p_add = _patches()
        with p_block, p_conv, p_count, p_add as add_mock, \
             patch("app.ai.claude.call_haiku") as haiku, \
             patch("app.ai.claude.call_sonnet") as sonnet:
            res = await chat_start(
                _start_request(tunnel="support", visitor=VisitorInfo()),
                _request_mock(),
                None,
            )
        assert res.message in TEMPLATES["welcome:support:anonymous"]
        haiku.assert_not_called()
        sonnet.assert_not_called()
        assert add_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_double_start_reuses_conversation_and_never_regreets(self):
        gen = GeneratedResponse(text="should not be used", model_used="sonnet")
        p_block, p_conv, p_count, p_gen, p_add = _patches(msg_count=2, gen=gen)
        existing = AsyncMock(return_value="Welcome back opening")
        with p_block, p_conv, p_count, p_gen as gen_mock, p_add as add_mock, \
             patch("app.api.chat._existing_opening", existing):
            res = await chat_start(_start_request(), _request_mock(), None)

        assert res.conversation_id == _CONV_ID
        assert res.message == "Welcome back opening"
        gen_mock.assert_not_called()
        add_mock.assert_not_awaited()


class TestMessageImpliesPresence:
    @pytest.mark.asyncio
    async def test_message_flips_left_to_online(self):
        conv = {"id": _CONV_ID, "metadata": {"widget_presence": "left", "widget_open": False}}
        with patch("app.api.chat.db.get_conversation", new=AsyncMock(return_value=conv)), \
             patch("app.api.chat.db.update_conversation", new=AsyncMock()) as upd:
            await _mark_client_active(_CONV_ID)
        upd.assert_awaited_once()
        meta = upd.await_args.args[1]["metadata"]
        assert meta["widget_presence"] == "online"
        assert meta["widget_open"] is True
        assert meta["widget_last_event"] == "message"
        assert meta["widget_last_event_at"]

    @pytest.mark.asyncio
    async def test_no_write_when_already_online(self):
        conv = {"id": _CONV_ID, "metadata": {"widget_presence": "online"}}
        with patch("app.api.chat.db.get_conversation", new=AsyncMock(return_value=conv)), \
             patch("app.api.chat.db.update_conversation", new=AsyncMock()) as upd:
            await _mark_client_active(_CONV_ID)
        upd.assert_not_awaited()  # updated_at trigger — no churn


class TestStageCountersIgnoreAiOpening:
    def test_stage_counts_user_messages_only(self):
        # The prompt's conversation stage counts role="user" only, so an AI
        # opening does not advance thresholds that assumed a user message
        # opened the conversation.
        from app.ai.prompts import _conversation_stage

        assert _conversation_stage(0) == "early"
        assert _conversation_stage(3) == "early"

    def test_greeting_intent_survives_ai_opening_in_history(self):
        # generate() flips GREETING → GENERAL_QUESTION only when the client
        # already has >1 user messages; an AI-opened history has zero.
        history = [{"role": "ai", "content": "Welcome! Where are you flying?"}]
        user_msg_count = sum(1 for m in history if m.get("role") == "user")
        assert user_msg_count == 0
