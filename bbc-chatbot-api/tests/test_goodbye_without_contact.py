"""A goodbye from a client nobody can reach (19 Nov 2026, Sydney → Amman).

The client gave the whole trip, was asked for the email once — after the dates, the flexibility, the
travelers and the occasion — answered "thank you price matters", and was told "Your search is in expert
hands. We'll be in touch soon"; a second "thank you" got "your consultant takes it from here". No email, no
number: nobody could be in touch. Three things changed:

- a goodbye is a message that says only goodbye ("thank you price matters" is a price worry);
- a goodbye with no contact on file asks where the agent should write (WhatsApp or email, with the
  buttons), once; a later one promises nothing;
- in a chat with no form, once the route is known, the email is the next question.

Every message, trip and contact here is invented."""

import os
import sys
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.models.chat import VisitorInfo
from app.pipeline import card_buttons
from app.pipeline.intent import Intent, detect_intent, is_only_goodbye

_CID = "11111111-2222-3333-4444-555555555555"
_LEAD = {"id": "L-1", "conversation_id": _CID, "origin_code": "SYD", "destination_code": "AMM",
         "departure_date": "2027-01-09", "return_date": "2027-01-16", "passengers": 1}


@pytest.fixture(autouse=True)
def _health():
    saved = dict(card_buttons.CARD_BUTTONS_HEALTH)
    yield
    card_buttons.CARD_BUTTONS_HEALTH.clear()
    card_buttons.CARD_BUTTONS_HEALTH.update(saved)


# ── A goodbye says only goodbye ───────────────────────────────

@pytest.mark.parametrize("text", [
    "Thank you so much!", "Bye for now", "Goodbye", "That's all I needed", "ok thanks", "thank you",
    "thank you, have a nice day", "Thanks Alex", "thanks a lot", "Thanks for your help!", "thank you, bye",
])
def test_a_plain_thanks_or_goodbye_is_a_goodbye(text):
    assert is_only_goodbye(text)
    assert detect_intent(text, use_llm=False) == Intent.CLOSING


@pytest.mark.parametrize("text, intent", [
    ("thank you price matters", Intent.PRICE_INQUIRY),
    ("thanks, how much?", Intent.PRICE_INQUIRY),
    ("no thanks", Intent.GENERAL_QUESTION),
    ("thanks, I'll wait for the email", Intent.GENERAL_QUESTION),
    ("thanks, my email is client@example.com", Intent.GENERAL_QUESTION),
])
def test_a_thanks_that_says_more_is_answered_not_closed(text, intent):
    assert not is_only_goodbye(text)
    assert detect_intent(text, use_llm=False) == intent


def test_the_classifiers_goodbye_counts_only_for_a_plain_goodbye():
    with patch("app.pipeline.intent.claude_classify", return_value=("closing", 0.0)):
        assert detect_intent("thanks, I will think about the dates") == Intent.GENERAL_QUESTION


# ── The goodbye with no contact on file ───────────────────────

def _saved(text_box):
    async def _add(*args, **kwargs):
        # add_message(cid, role, text) from the card buttons; keywords from the turn.
        role = args[1] if len(args) > 1 else kwargs.get("role")
        text = args[2] if len(args) > 2 else kwargs.get("content")
        text_box.append((role, text))
        return {"id": f"m-{role}", "content": text, "created_at": "2026-11-19T00:27:26+00:00"}
    return _add


@pytest.mark.asyncio
async def test_the_first_goodbye_asks_where_to_write_with_both_buttons():
    said: list = []
    claim = AsyncMock(return_value=True)
    with patch.object(card_buttons, "_claim", new=claim), \
         patch("app.services.conversation_service.add_message", new=_saved(said)):
        out = await card_buttons.goodbye_without_contact(_CID, {}, VisitorInfo(), _LEAD)
    claim.assert_awaited_once_with(_CID, card_buttons.BEFORE_YOU_GO_KEY)
    assert out is not None and out.message.startswith("Before you go — an agent can send you the fares for ")
    assert "WhatsApp or email?" in out.message
    assert [b["kind"] for b in out.buttons] == ["whatsapp", "email"]
    assert "in touch" not in out.message.lower()
    assert said == [("ai", out.message)] and out.message_id == "m-ai"
    assert card_buttons.CARD_BUTTONS_HEALTH["goodbye_asked"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("meta, claimed", [({card_buttons.BEFORE_YOU_GO_KEY: "2026-11-19T00:27:26Z"}, True),
                                           ({}, False)])
async def test_a_later_goodbye_promises_nothing(meta, claimed):
    """Asked already (here, or by the before-you-go as the client left): no second ask, no promise."""
    said: list = []
    with patch.object(card_buttons, "_claim", new=AsyncMock(return_value=claimed)), \
         patch("app.services.conversation_service.add_message", new=_saved(said)):
        out = await card_buttons.goodbye_without_contact(_CID, meta, VisitorInfo(), _LEAD)
    assert out.message == ("Thanks for stopping by. Whenever you'd like the fares, leave your WhatsApp or email "
                           "here and an agent will send them.")
    assert [b["kind"] for b in out.buttons] == ["whatsapp", "email"]
    assert card_buttons.CARD_BUTTONS_HEALTH["goodbye_no_contact"] == 1


@pytest.mark.asyncio
async def test_a_client_who_turned_a_channel_down_is_not_asked_again():
    said: list = []
    claim = AsyncMock(return_value=True)
    with patch.object(card_buttons, "_claim", new=claim), \
         patch("app.services.conversation_service.add_message", new=_saved(said)):
        out = await card_buttons.goodbye_without_contact(_CID, {}, VisitorInfo(), _LEAD, "no calls, no texts")
    assert out.message == "Thanks for stopping by — whenever you'd like the fares, just write here."
    assert not out.buttons
    claim.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("visitor, meta", [
    (VisitorInfo(email="client@example.com"), {}),
    (VisitorInfo(phone="+12105550100"), {}),
    (VisitorInfo(), {"phone_to_check": "0412 345 678"}),
])
async def test_with_a_way_to_reach_them_the_usual_goodbye_stands(visitor, meta):
    with patch.object(card_buttons, "_claim", new=AsyncMock(return_value=True)) as claim:
        assert await card_buttons.goodbye_without_contact(_CID, meta, visitor, _LEAD) is None
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_goodbye_speaks_the_clients_language():
    with patch.object(card_buttons, "_claim", new=AsyncMock(return_value=False)), \
         patch("app.services.conversation_service.add_message", new=_saved([])):
        out = await card_buttons.goodbye_without_contact(_CID, {"client_lang": "es"}, VisitorInfo(), _LEAD)
    assert out.message.startswith("Gracias por su visita.") and [b["label"] for b in out.buttons] == ["WhatsApp", "Correo"]


# ── The turn: "thank you" with no contact never reaches the goodbye template ──

class _Generated(Exception):
    """The turn reached the model (or the usual goodbye template): the step under test is behind it."""


def _turn(message, *, conv_extra=None, visitor=None, claimed=True):
    from app.pipeline.orchestrator import _pipeline

    conv = {"id": _CID, "mode": "ai", "status": "active", "tunnel": "sales",
            "metadata": {"proactive_trigger": "quote"}, **(conv_extra or {})}
    history = [{"role": "ai", "content": "A personal trip to Amman. Where should they send the private fare "
                                          "options — what's your email?"},
               {"role": "user", "content": message}]
    gen = MagicMock(side_effect=_Generated("the model's turn: stop here"))
    said: list = []

    async def run():
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.conversation_service.get_or_create_conversation",
                                      new=AsyncMock(return_value=conv)))
            stack.enter_context(patch("app.services.conversation_service.add_message", new=_saved(said)))
            stack.enter_context(patch("app.pipeline.orchestrator.db.get_recent_messages",
                                      new=AsyncMock(return_value=history)))
            stack.enter_context(patch("app.services.moderation.moderate_message", new=AsyncMock()))
            stack.enter_context(patch("app.pipeline.orchestrator.db.update_conversation", new=AsyncMock()))
            stack.enter_context(patch("app.pipeline.orchestrator.db.update_lead", new=AsyncMock()))
            stack.enter_context(patch("app.pipeline.orchestrator.db.get_today_cost", new=AsyncMock(return_value=0.0)))
            stack.enter_context(patch("app.pipeline.orchestrator.lead_service.get_or_create_lead",
                                      new=AsyncMock(return_value=dict(_LEAD))))
            stack.enter_context(patch("app.pipeline.orchestrator.lead_service.update_lead_from_entities",
                                      new=AsyncMock(return_value=True)))
            stack.enter_context(patch.object(card_buttons, "_claim", new=AsyncMock(return_value=claimed)))
            stack.enter_context(patch("app.pipeline.orchestrator.generate_response", new=gen))
            try:
                return await _pipeline(_CID, message, "sales", visitor or VisitorInfo(), None,
                                       _persist_state={"ai_persisted": False})
            except _Generated:
                return None

    return run, gen, said


@pytest.mark.asyncio
async def test_thank_you_with_no_contact_asks_where_to_write_instead_of_saying_goodbye():
    run, gen, said = _turn("thank you")
    resp = await run()
    gen.assert_not_called()
    assert resp.message.startswith("Before you go — an agent can send you the fares for ")
    assert [b["kind"] for b in resp.buttons] == ["whatsapp", "email"]
    assert ("ai", resp.message) in said
    assert all("in touch" not in text for _role, text in said)


@pytest.mark.asyncio
async def test_thank_you_with_an_email_on_file_keeps_the_usual_goodbye():
    run, gen, _said = _turn("thank you", conv_extra={"visitor_email": "client@example.com"})
    await run()
    gen.assert_called_once()
    assert gen.call_args.kwargs["intent"] == Intent.CLOSING


@pytest.mark.asyncio
async def test_a_price_worry_after_the_email_ask_reaches_the_model_not_a_goodbye():
    run, gen, _said = _turn("thank you price matters")
    await run()
    gen.assert_called_once()
    assert gen.call_args.kwargs["intent"] == Intent.PRICE_INQUIRY


# ── In a chat with no form, the email right after the route ──

def _prompt(lead, history, meta, visitor=None):
    from app.ai.prompts import build_conversational_prompt

    _static, dynamic = build_conversational_prompt("sales", visitor or VisitorInfo(), lead=lead, history=history,
                                                   entities={"_metadata": meta})
    return dynamic


_ROUTE_ONLY = {"origin_code": "SYD", "destination_code": "AMM", "score": 20}
_SO_FAR = [{"role": "ai", "content": "Tell me where and roughly when, and an agent will check fares for you."},
           {"role": "user", "content": "from sydney"},
           {"role": "ai", "content": "Sydney noted — and where are you heading?"},
           {"role": "user", "content": "amman jordan"}]


def test_a_card_chat_asks_the_email_as_soon_as_the_route_is_known():
    dynamic = _prompt(_ROUTE_ONLY, _SO_FAR, {"proactive_trigger": "quote"})
    assert "Still needed: email, departure date" in dynamic
    assert "Ask next: the email — in THIS reply" in dynamic


@pytest.mark.parametrize("lead, history, meta, visitor", [
    # the email was asked already: no second push
    (_ROUTE_ONLY, _SO_FAR + [{"role": "ai", "content": "Where should they send the options — your email?"},
                             {"role": "user", "content": "thank you price matters"}],
     {"proactive_trigger": "quote"}, None),
    # the route is not known yet
    ({"origin_code": "SYD", "score": 5}, _SO_FAR[:2], {"proactive_trigger": "quote"}, None),
    # a chat with a form behind it keeps its order
    (_ROUTE_ONLY, _SO_FAR, {}, None),
    # the email is on file
    (_ROUTE_ONLY, _SO_FAR, {"proactive_trigger": "quote"}, VisitorInfo(email="client@example.com")),
])
def test_the_email_is_not_pushed_otherwise(lead, history, meta, visitor):
    assert "Ask next: the email" not in _prompt(lead, history, meta, visitor)
