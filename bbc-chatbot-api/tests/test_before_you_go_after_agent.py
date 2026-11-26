"""The before-you-go after an agent (lever 6 of "Mai multe chaturi și leaduri", owner, 25 Nov 2026), and as the chat
closes under a question for the contact (lever 5's gap: the email field left empty, the chat closed).

17-24 Nov: in 98 chats an agent wrote before the client left a contact, and 54 of those clients wrote nothing after
the agent's first message. The before-you-go never went out once an agent had written (agent_wrote). Now it goes out
as the client leaves the chat or the page with the agent's newest message unanswered: once per chat, with the SMS
and Email buttons, in words that say "your agent", and the agent with the chat open sees it.

Every contact and trip here is invented.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.ai.output_guard import has_price
from app.api import chat as chat_api
from app.pipeline import card_buttons
from app.realtime.manager import manager
from app.services import card_words

_CID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_ALEX_FIELD_ASK = ("Sure. An agent will check live fares for your trip and email them to you. "
                   "What's your email address?")
_AGENT_ASK = "Hi, I'm Maria. What's your mobile number? I'll text you the fares."
_AGENT_HELLO = "Hi, I'm Maria. Where are you flying to?"


@pytest.fixture(autouse=True)
def _counts(monkeypatch):
    monkeypatch.setattr("app.flags._snapshot", {})
    monkeypatch.delenv(card_buttons.AFTER_AGENT_FLAG, raising=False)
    saved = dict(card_buttons.CARD_BUTTONS_HEALTH)
    yield
    card_buttons.CARD_BUTTONS_HEALTH.clear()
    card_buttons.CARD_BUTTONS_HEALTH.update(saved)


def _conv(**over) -> dict:
    base = {"id": _CID, "status": "active", "mode": "human", "visitor_email": None, "visitor_phone": None,
            "metadata": {"site": "bbc"}}
    base.update(over)
    return base


class _Saved:
    """conversation_service.add_message: every saved row."""

    def __init__(self):
        self.rows = []

    async def __call__(self, cid, role, content, model_used=None, cost=0.0, live_extra=None):
        row = {"id": f"m{len(self.rows) + 1}", "role": role, "content": content,
               "created_at": "2026-11-25T10:00:00+00:00", "model_used": model_used}
        self.rows.append(row)
        return row


async def _ask(why, *, conv=None, users=1, agents=1, answered=False, newest=None, lead=None, claim=True):
    """before_you_go with the database stubbed. `answered`: _answered_agent's word (True, False, None);
    `newest`: the newest message as (role, text, seconds ago)."""
    saved = _Saved()
    counts = {"user": users, "agent": agents}
    recent = []
    if newest:
        role, text, ago = newest
        at = (datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat()
        recent = [{"role": role, "content": text, "created_at": at}]
    seen = AsyncMock(return_value=answered)
    pushed = AsyncMock()
    with patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=conv or _conv())), \
            patch("app.db.supabase.count_messages",
                  new=AsyncMock(side_effect=lambda cid, role=None: counts.get(role, 0))), \
            patch("app.db.supabase.get_recent_messages", new=AsyncMock(return_value=recent)), \
            patch("app.services.conversation_service.add_message", new=saved), \
            patch("app.services.lead_service.get_or_create_lead",
                  new=AsyncMock(return_value=lead if lead is not None else {"id": "L1"})), \
            patch.object(card_buttons, "_answered_agent", new=seen), \
            patch.object(card_buttons, "_claim", new=AsyncMock(return_value=claim)), \
            patch.object(manager, "push", new=pushed):
        out = await card_buttons.before_you_go(_CID, why=why)
    return out, saved, pushed, seen


class TestAfterAnAgent:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("why", ["closed", "hidden"])
    async def test_a_client_who_leaves_the_agent_unanswered_is_asked_where_to_write(self, why):
        out, saved, pushed, _ = await _ask(why)
        assert out["show"] is True
        assert out["message"] == "Before you go, where should your agent send you the fares: by text or by email?"
        assert [b["kind"] for b in out["buttons"]] == ["sms", "email"]
        assert [r["role"] for r in saved.rows] == ["ai"] and out["id"] == "m1"
        # The agent with the chat open sees it: the panel reads only the stream, and add_message pushes no AI row.
        pushed.assert_awaited_once()
        assert pushed.await_args.args == (_CID, saved.rows[0])
        assert card_buttons.CARD_BUTTONS_HEALTH["before_you_go_after_agent"] == 1
        assert card_buttons.CARD_BUTTONS_HEALTH["before_you_go"] == 1

    @pytest.mark.asyncio
    async def test_the_trip_on_the_lead_is_said_back(self):
        out, _, _, _ = await _ask("closed", lead={"id": "L1", "destination_code": "LHR"})
        assert out["message"].startswith("Before you go, your agent can send you the fares for ")
        assert "London" in out["message"]

    @pytest.mark.asyncio
    async def test_a_hello_from_the_agent_that_asks_no_contact_does_not_hold_it(self):
        out, _, _, _ = await _ask("hidden", newest=("agent", _AGENT_HELLO, 10))
        assert out["show"] is True

    @pytest.mark.asyncio
    async def test_said_once_per_chat(self):
        out, saved, pushed, _ = await _ask("closed", claim=False)
        assert out == {"show": False, "why": "already_said"} and saved.rows == []
        pushed.assert_not_awaited()


class TestRule5TheClientsItLeavesAlone:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("why", ["idle", None, "something_else"])
    async def test_a_quiet_page_or_an_older_widget_keeps_the_old_rule(self, why):
        """A client on a quiet page may be waiting for the agent ("one moment, checking the fares"); a widget from
        before 25 Nov says nothing; a word it does not know reads as nothing said."""
        out, saved, pushed, seen = await _ask(why)
        assert out == {"show": False, "why": "agent_wrote"} and saved.rows == []
        seen.assert_not_awaited()
        pushed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_switch_brings_back_the_rule_from_before(self, monkeypatch):
        monkeypatch.setattr("app.flags._snapshot", {card_buttons.AFTER_AGENT_FLAG: False})
        out, saved, pushed, seen = await _ask("closed")
        assert out == {"show": False, "why": "agent_wrote"} and saved.rows == []
        seen.assert_not_awaited()
        # Alex's chats are not touched by it.
        out, _, _, _ = await _ask("closed", agents=0, conv=_conv(mode="ai"))
        assert out["show"] is True

    @pytest.mark.asyncio
    async def test_a_client_who_answered_the_agent_is_the_agents_to_ask(self):
        out, saved, _, _ = await _ask("closed", answered=True)
        assert out == {"show": False, "why": "answered_agent"} and saved.rows == []

    @pytest.mark.asyncio
    async def test_never_on_a_guess_when_the_newest_words_are_unread(self):
        out, saved, _, _ = await _ask("closed", answered=None)
        assert out == {"show": False, "why": "unread"} and saved.rows == []

    @pytest.mark.asyncio
    async def test_the_agents_hello_to_a_visitor_who_never_wrote(self):
        """"On the site now": the agent said hello first and the visitor never wrote: nothing to ask after."""
        out, saved, _, _ = await _ask("closed", users=0)
        assert out == {"show": False, "why": "never_wrote"} and saved.rows == []

    @pytest.mark.asyncio
    async def test_a_contact_on_the_chat(self):
        out, _, _, seen = await _ask("closed", conv=_conv(visitor_phone="+12105550100"))
        assert out == {"show": False, "why": "has_contact"}
        seen.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_chat_the_agent_closed(self):
        out, _, _, _ = await _ask("closed", conv=_conv(status="completed"))
        assert out == {"show": False, "why": "closed"}

    @pytest.mark.asyncio
    async def test_without_an_agent_the_words_stay_alexs_and_nothing_is_pushed(self):
        out, _, pushed, seen = await _ask("closed", agents=0, conv=_conv(mode="ai"))
        assert out["message"] == "Before you go, where should an agent send you the fares: by text or by email?"
        seen.assert_not_awaited()
        pushed.assert_not_awaited()
        assert card_buttons.CARD_BUTTONS_HEALTH["before_you_go_after_agent"] == 0


class TestUnderAQuestionForTheContact:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("why, shows", [("closed", True), ("hidden", False), ("idle", False), (None, False)])
    async def test_alexs_field_left_empty_and_the_chat_closed(self, why, shows):
        """Lever 5's gap: the email field opened with Alex's question, nothing typed, the chat closed. Leaving the
        page or a quiet page still wait the two minutes (#3359: the client may be typing or fetching it)."""
        out, saved, _, _ = await _ask(why, agents=0, conv=_conv(mode="ai"), newest=("ai", _ALEX_FIELD_ASK, 20))
        assert out["show"] is shows
        if shows:
            assert card_buttons.CARD_BUTTONS_HEALTH["before_you_go_after_ask"] == 1
        else:
            assert out == {"show": False, "why": "just_asked"} and saved.rows == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("why, shows", [("closed", True), ("hidden", False)])
    async def test_the_agents_own_ask_for_the_number(self, why, shows):
        out, _, _, _ = await _ask(why, newest=("agent", _AGENT_ASK, 30))
        assert out["show"] is shows
        if not shows:
            assert out == {"show": False, "why": "just_asked"}

    @pytest.mark.asyncio
    async def test_the_agents_ask_long_ago_holds_nothing(self):
        out, _, _, _ = await _ask("hidden", newest=("agent", _AGENT_ASK, 180))
        assert out["show"] is True
        assert card_buttons.CARD_BUTTONS_HEALTH["before_you_go_after_ask"] == 0

    def test_the_agents_question_counts_as_one_and_the_clients_never_does(self):
        now = datetime.now(timezone.utc).isoformat()
        assert card_buttons._just_asked_contact([{"role": "agent", "content": _AGENT_ASK, "created_at": now}])
        assert not card_buttons._just_asked_contact([{"role": "user", "content": "What's your email?", "created_at": now}])
        assert not card_buttons._just_asked_contact([{"role": "system", "content": _AGENT_ASK, "created_at": now}])


class TestWhoseWordIsTheNewest:
    @staticmethod
    async def _read(data=None, fails=False):
        client = MagicMock()
        query = client.table.return_value.select.return_value.eq.return_value.in_.return_value
        if fails:
            query.order.return_value.limit.return_value.execute.side_effect = ConnectionError("reset")
        else:
            query.order.return_value.limit.return_value.execute.return_value = MagicMock(data=data)

        async def run(fn, *a, **k):
            return fn()

        with patch("app.db.supabase.get_client", return_value=client), patch("app.db.supabase._run_sync", new=run):
            out = await card_buttons._answered_agent(_CID)
        return out, client, query

    @pytest.mark.asyncio
    async def test_the_clients_word_after_the_agents_is_an_answer(self):
        out, client, query = await self._read([{"role": "user"}])
        assert out is True
        client.table.assert_called_with("messages")
        client.table.return_value.select.return_value.eq.return_value.in_.assert_called_with("role", ["user", "agent"])
        query.order.assert_called_with("created_at", desc=True)
        query.order.return_value.limit.assert_called_with(1)

    @pytest.mark.asyncio
    async def test_the_agents_word_last_is_unanswered(self):
        out, _, _ = await self._read([{"role": "agent"}])
        assert out is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("data, fails", [([], False), (None, False), (None, True)])
    async def test_a_read_that_brings_nothing_is_counted_never_guessed(self, data, fails, caplog):
        out, _, _ = await self._read(data, fails=fails)
        assert out is None
        assert card_buttons.CARD_BUTTONS_HEALTH["before_you_go_unread"] == 1
        assert "reset" not in caplog.text  # the type, never the error's text


class TestTheWords:
    @pytest.mark.parametrize("lang, yours", [(None, "your agent"), ("es", "su agente"), ("fr", "votre agent")])
    def test_your_agent_in_three_languages(self, lang, yours):
        for line in (None, "New York → London"):
            text, buttons = card_words.before_you_go(line, lang, agent=True)
            assert yours in text
            assert [b["kind"] for b in buttons] == ["sms", "email"]
            assert "—" not in text and not has_price(text) and "whatsapp" not in text.lower()
            if line:
                assert line in text

    def test_alexs_words_are_unchanged(self):
        assert card_words.before_you_go(None)[0] == (
            "Before you go, where should an agent send you the fares: by text or by email?"
        )


class TestTheEndpoint:
    @pytest.mark.asyncio
    async def test_it_passes_how_the_client_left(self):
        inner = AsyncMock(return_value={"show": False, "why": "agent_wrote"})
        with patch.object(card_buttons, "before_you_go", new=inner):
            out = await chat_api.before_you_go(_CID, why="closed", _owner=None)
        inner.assert_awaited_once_with(_CID, why="closed")
        assert out == {"show": False, "why": "agent_wrote"}
