"""The contact a client gives in the chat itself reaches the lead and the CRM.

16-17 Nov 2026, 13 chats with no form behind them (the card, the note, the
corner button): six emails, three phone numbers and six names were typed into
the conversation, and none of the 13 reached the CRM.

- The CRM gate read only the widget's request, which never carries a contact
  in these chats; the conversation row did hold the emails.
- The number went to leads.visitor_phone, a column that does not exist: the
  whole lead write failed, the consultant's note and the call-back flag with it.
- A name answered to Alex's own "your name?" was never read (the extractor
  never takes a name from free text), so the next reply asked for it again.
- "Text" answered to "a text or a quick call?" was lost, and three clients
  who had chosen texts — one "can’t always take a call" — were told a
  consultant would call them.

Every question shape below is one Alex asked that night; every name, email
and number is invented.
"""

import os
import sys
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.models.chat import VisitorInfo
from app.pipeline.callback_intent import (
    callback_note,
    detect_callback_request,
    notes_with_callback,
    notes_without_call,
)
from app.pipeline.contact_capture import (
    asks_for_channel,
    asks_for_last_name,
    asks_for_name,
    callback_channel,
    calls_refused,
    channel_after_call_request,
    channel_from_answer,
    full_name,
    last_question,
    name_from_answer,
    name_to_store,
    with_stored_contact,
    with_turn_contact,
)
from app.pipeline.entity_extractor import contact_channel_of, turns_down_calls

_CID ="11111111-2222-3333-4444-555555555555"

# The questions Alex asked on 17 Nov (client names replaced).
Q_NAME = "Noted. And your name, so the consultant knows who to address?"
Q_NAME_2 = (
    "Got it — your consultant will send the private fares for Boston to Lisbon right there.\n\n"
    "What's your name so they know who to address?"
)
Q_NAME_3 = "Email captured — your consultant will use that to send the options. What name should they address you by?"
Q_NAME_AFTER_CALLBACK = (
    "Of course — I'm putting a consultant on this to call you on +12105550100. "
    "While they get to you — let's get this moving — what's your name?"
)
Q_LAST_NAME = "Email confirmed, Anna. What's your last name so your consultant has it on file?"
Q_CHANNEL = (
    "Anna Rossi — noted. And a number for your consultant — would a text or a quick call "
    "work better for you?"
)
Q_CHANNEL_AFTER_NAME = (
    "Ilse, lovely to have your name. One last thing — a number for your consultant so they "
    "can reach you when the private options are ready. Would a text or a quick call work better for you?"
)
Q_DATES = "Thanks, your name is noted. Which dates work for you?"


# ── Reading the question that was asked ─────────────────────

class TestTheQuestionAsked:
    @pytest.mark.parametrize("q", [Q_NAME, Q_NAME_2, Q_NAME_3, Q_NAME_AFTER_CALLBACK, Q_LAST_NAME])
    def test_every_name_question_alex_asked_is_heard(self, q):
        assert asks_for_name(q)

    @pytest.mark.parametrize("q", [
        Q_DATES,                                         # the name in a statement, not the question
        Q_CHANNEL_AFTER_NAME,                            # "have your name." then a channel question
        "What's the name of the airline you prefer?",    # a name, not THEIR name
        "Your name is on the booking.",                  # no question at all
        "",
    ])
    def test_a_name_in_a_statement_or_another_question_is_not_a_name_question(self, q):
        assert not asks_for_name(q)

    def test_the_last_name_question(self):
        assert asks_for_last_name(Q_LAST_NAME)
        assert asks_for_last_name("Updated — anna@example.com it is. And your last name, Anna?")
        assert not asks_for_last_name(Q_NAME)

    @pytest.mark.parametrize("q", [Q_CHANNEL, Q_CHANNEL_AFTER_NAME, "Text or call — which works better?"])
    def test_the_channel_question(self, q):
        assert asks_for_channel(q)

    def test_a_channel_word_outside_a_question_is_not_one(self):
        assert not asks_for_channel("Of course — a consultant will call you. Or text, if you like.")

    def test_the_last_question_is_the_newest_from_alex_or_an_operator(self):
        history = [
            {"role": "ai", "content": Q_DATES},
            {"role": "user", "content": "Mid March"},
            {"role": "agent", "content": Q_NAME},
            {"role": "system", "content": "Consultant joined"},
            {"role": "user", "content": "Paul Moreau"},
        ]
        assert last_question(history) == Q_NAME
        assert last_question([]) == ""
        assert last_question(None) == ""


# ── Reading the answer ──────────────────────────────────────

class TestTheNameAnswer:
    @pytest.mark.parametrize("answer, name", [
        ("Paul Moreau", "Paul Moreau"),
        ("Lorien", "Lorien"),
        ("maribel", "Maribel"),
        ("Rossi", "Rossi"),
        ("It’s Mary-Ann O’Neil", "Mary-Ann O'Neil"),
        ("my name is josé garcía", "José García"),
        ("Tomas Silva.", "Tomas Silva"),
    ])
    def test_a_name(self, answer, name):
        assert name_from_answer(answer) == name

    @pytest.mark.parametrize("answer", [
        "Sorry .ca",                     # fixing the email, 17 Nov
        "anna@example.com",              # the email, answered to "your last name?"
        "Waiting for price",             # 17 Nov
        "Email is best",
        "Text",
        "Text please.  I can’t always take a call",
        "I would like business class or economy is fine. Something soon",
        "2105550100",
        "next week",
        "Round trip",
        "Just me",
        "Flexible",
        "yes",
        "",
    ])
    def test_anything_else_is_not_a_name(self, answer):
        assert name_from_answer(answer) is None

    def test_what_gets_stored(self):
        assert name_to_store(None, "Paul Moreau", False) == "Paul Moreau"
        # "your last name?" joins the first name we have.
        assert name_to_store("Anna", "Rossi", True) == "Anna Rossi"
        # The full name completes a first name.
        assert name_to_store("Lena", "Lena Smith", False) == "Lena Smith"
        # A known name is never replaced by another one.
        assert name_to_store("Anna Rossi", "Maria", False) is None
        assert name_to_store("Anna", "Maria", False) is None
        assert name_to_store("Anna Rossi", "Smith", True) is None
        assert name_to_store("Anna", None, True) is None
        # Without a first name, the last name alone is still the client's name.
        assert name_to_store(None, "Rossi", True) == "Rossi"

    def test_full_name(self):
        assert full_name("Anna", "Rossi", True) == "Anna Rossi"
        assert full_name("Anna", "Rossi", False) == "Rossi"
        assert full_name(None, "Rossi", True) == "Rossi"


class TestTheChannelAnswer:
    @pytest.mark.parametrize("answer, channel", [
        ("Text", "text"),
        ("Text please.  I can’t always take a call", "text"),   # 17 Nov, curly apostrophe
        ("Text please. I can't always take a call", "text"),
        ("Email is best", "email"),                             # 17 Nov
        ("email is better", "email"),                           # 17 Nov
        ("a call is fine", "call"),
        ("don't text me, call", "call"),
        ("No calls, text only", "text"),
        ("WhatsApp", "text"),
        ("Message me", "text"),
    ])
    def test_the_choice(self, answer, channel):
        assert channel_from_answer(answer) == channel

    @pytest.mark.parametrize("answer", ["either text or call", "Waiting for price", "Paul Moreau", "", "no calls"])
    def test_no_single_choice(self, answer):
        assert channel_from_answer(answer) is None


# ── How the client wants to be reached on a number ──────────

class TestCallbackChannel:
    def test_a_text_said_with_the_number(self):
        assert callback_channel("2105550100  text please", "number", None) == "text"

    def test_a_text_chosen_earlier(self):
        assert callback_channel("2105550100", "number", "text") == "text"
        assert callback_channel("2105550100", "number", "no calls") == "text"
        assert callback_channel("2105550100", "number", "email, no calls") == "text"

    def test_asked_to_be_called_is_a_call(self):
        """JOSEF: "please call me" — this message's words beat an older wish."""
        assert callback_channel("please call me on 210-555-0100", "words", "text") == "call"
        assert callback_channel("call me back", "words", None) == "call"

    def test_a_bare_number_states_no_preference(self):
        assert callback_channel("2105550100", "number", None) == "any"
        assert callback_channel("2105550100", "number", "email") == "any"

    def test_an_earlier_no_texts_is_never_overridden(self):
        """A number alone withdraws no refusal (CodeRabbit #262)."""
        assert callback_channel("2105550100", "number", "no calls, no texts") == "any"
        assert callback_channel("2105550100", "number", "no texts") == "any"
        assert calls_refused("no calls, no texts") is True
        assert calls_refused("text") is False

    def test_a_call_asked_for_now_withdraws_no_calls_and_texts_only(self):
        assert channel_after_call_request("text, no calls") is None
        assert channel_after_call_request("email, text, no calls") == "email"
        assert channel_after_call_request("no calls, no texts") == "no texts"
        assert channel_after_call_request(None) is None

    def test_only_the_newest_callback_wish_stays_in_the_notes(self):
        notes = "CLIENT ASKED TO BE CALLED — +12105550100\nItinerary: BOS-LHR\nCLIENT GAVE A NUMBER (no preference stated) — +12105550100"
        out = notes_with_callback(notes, "CLIENT ASKED FOR TEXTS, NOT CALLS — +12105550100")
        assert out == "CLIENT ASKED FOR TEXTS, NOT CALLS — +12105550100\nItinerary: BOS-LHR"
        assert notes_with_callback(None, "CLIENT ASKED TO BE CALLED — +12105550100") == (
            "CLIENT ASKED TO BE CALLED — +12105550100"
        )

    def test_the_note_says_what_the_client_asked_for(self):
        assert callback_note("+12105550100", channel="text").startswith("CLIENT ASKED FOR TEXTS, NOT CALLS")
        assert callback_note("+12105550100", channel="any").startswith("CLIENT GAVE A NUMBER (no preference stated)")
        assert callback_note("+12105550100").startswith("CLIENT ASKED TO BE CALLED")
        assert "number not captured" in callback_note(None, channel="text")


class TestCurlyApostrophes:
    """A phone types ’ by default. "don’t call me" was heard as "call me"."""

    @pytest.mark.parametrize("msg", ["don’t call me", "Please don’t call me back", "I don’t want a call, don’t call me"])
    def test_a_refusal_is_never_a_callback_request(self, msg):
        assert detect_callback_request(msg) is None

    def test_the_refusal_is_kept_as_a_wish(self):
        assert contact_channel_of("don’t call me") == "no calls"
        assert contact_channel_of("Please don’t text me") == "no texts"
        assert contact_channel_of("don’t call me") == contact_channel_of("don't call me")


# ── The conversation row fills the request's gaps ───────────

class TestStoredContact:
    def test_a_chat_with_no_form_gets_the_rows_contact(self):
        v = with_stored_contact(VisitorInfo(), {
            "visitor_name": "Paul Moreau", "visitor_email": "paul@example.com", "visitor_phone": "+12105550100",
        })
        assert (v.name, v.email, v.phone) == ("Paul Moreau", "paul@example.com", "+12105550100")

    def test_what_the_widget_sent_wins(self):
        v = with_stored_contact(
            VisitorInfo(name="Anna Rossi", email="anna@example.com"),
            {"visitor_name": "Maria", "visitor_email": "old@example.com", "visitor_phone": "+12105550100"},
        )
        assert (v.name, v.email, v.phone) == ("Anna Rossi", "anna@example.com", "+12105550100")

    def test_the_full_name_completes_the_forms_first_name(self):
        """A form that sends "John" on every turn must not write "John" back
        over the "John Smith" the client gave to "your last name?"."""
        assert with_stored_contact(VisitorInfo(name="John"), {"visitor_name": "John Smith"}).name == "John Smith"
        assert with_stored_contact(VisitorInfo(name="Johnny"), {"visitor_name": "John Smith"}).name == "Johnny"
        assert with_stored_contact(VisitorInfo(name="John"), {"visitor_name": "Mary Smith"}).name == "John"

    def test_this_turns_value_replaces_only_what_the_widget_did_not_send(self):
        """A corrected email replaces the row's; the widget's own stays
        (CodeRabbit #262)."""
        stored = with_stored_contact(VisitorInfo(), {"visitor_email": "old@example.com"})
        assert with_turn_contact(stored, VisitorInfo(), {"email": "new@example.com"}).email == "new@example.com"
        form = VisitorInfo(email="form@example.com")
        assert with_turn_contact(form, form, {"email": "other@example.com"}).email == "form@example.com"
        same = with_turn_contact(stored, VisitorInfo(), {"email": "old@example.com", "phone": "  "})
        assert same is stored

    def test_nothing_usable_changes_nothing(self):
        v = VisitorInfo(name="Anna")
        assert with_stored_contact(v, {"visitor_email": "  ", "visitor_phone": None}) is v
        assert with_stored_contact(v, None) is v
        assert with_stored_contact(None, None) == VisitorInfo()

    def test_the_crm_gate_sees_the_contact_given_in_the_chat(self):
        """Name, email and phone typed into the chat are all the data the
        CRM needs from the client (owner, 17 Nov)."""
        from app.services.crm import check_crm_ready

        lead = {
            "origin_code": "SFO", "destination_code": "FCO",
            "departure_date": "2027-03-10", "passengers": 1, "trip_type": "one_way",
        }
        row = {"visitor_name": "Paul Moreau", "visitor_email": "paul@example.com", "visitor_phone": "+12105550100"}
        assert check_crm_ready(lead, with_stored_contact(VisitorInfo(), row)) is True
        assert check_crm_ready(lead, VisitorInfo()) is False


# ── The callback turn (Step 3.45) ───────────────────────────

_TRIP = {
    "origin_code": "BOS", "destination_code": "LIS",
    "departure_date": "2027-03-10", "passengers": 1, "trip_type": "one_way",
}


def _callback_turn(message, *, lead_signals=None, conv_extra=None, visitor=None, lead_extra=None, lead_fails=False):
    """Run one callback turn; returns (response, mocks). `lead_fails`: the
    lead read raises, as on a database hiccup."""
    from app.pipeline.orchestrator import _pipeline

    conv = {"id": _CID, "mode": "ai", "status": "active", "tunnel": "sales", "metadata": {},
            **(conv_extra or {})}
    lead = {"id": "L-1", "conversation_id": _CID, "notes": "", **(lead_extra or {})}
    if lead_signals is not None:
        lead["intent_signals"] = lead_signals
    mocks = {
        "update_conversation": AsyncMock(return_value={"id": _CID}),
        "update_lead": AsyncMock(),
        "queue_for_person": AsyncMock(),
        "task_from_callback": AsyncMock(),
        "withdraw_call_tasks": AsyncMock(return_value=0),
    }
    read_lead = (AsyncMock(side_effect=RuntimeError("database hiccup")) if lead_fails
                 else AsyncMock(return_value=dict(lead)))

    async def run():
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.conversation_service.get_or_create_conversation",
                                      new=AsyncMock(return_value=conv)))
            stack.enter_context(patch("app.services.conversation_service.add_message",
                                      new=AsyncMock(return_value={"id": "m1"})))
            stack.enter_context(patch("app.pipeline.orchestrator.db.get_recent_messages",
                                      new=AsyncMock(return_value=[{"role": "ai", "content": Q_CHANNEL}])))
            stack.enter_context(patch("app.services.moderation.moderate_message", new=AsyncMock()))
            stack.enter_context(patch("app.pipeline.orchestrator.db.update_conversation",
                                      new=mocks["update_conversation"]))
            stack.enter_context(patch("app.pipeline.orchestrator.db.update_lead", new=mocks["update_lead"]))
            stack.enter_context(patch("app.pipeline.orchestrator.lead_service.get_or_create_lead", new=read_lead))
            stack.enter_context(patch("app.services.routing.queue_for_person", new=mocks["queue_for_person"]))
            stack.enter_context(patch("app.services.tasks_service.task_from_callback",
                                      new=mocks["task_from_callback"]))
            stack.enter_context(patch("app.services.tasks_service.withdraw_call_tasks",
                                      new=mocks["withdraw_call_tasks"]))
            return await _pipeline(
                _CID, message, "sales", visitor or VisitorInfo(), None,
                _persist_state={"ai_persisted": False},
            )

    return run, mocks


def _lead_payloads(mocks):
    return [c.args[1] for c in mocks["update_lead"].await_args_list]


class TestTheCallbackTurn:
    @pytest.mark.asyncio
    async def test_a_texter_who_gives_the_number_is_told_texts_and_gets_no_call_task(self):
        """17 Nov: "Text", then the number — and the reply promised a call."""
        run, mocks = _callback_turn(
            "2105550100", lead_signals={"contact_channel": "text"},
            conv_extra={"visitor_email": "paul@example.com"},
        )
        resp = await run()

        assert "text you on +12105550100" in resp.message
        assert "call you" not in resp.message
        mocks["update_conversation"].assert_any_await(_CID, {"visitor_phone": "+12105550100"})
        payloads = _lead_payloads(mocks)
        assert payloads, "the note and the wish must reach the lead"
        assert all("visitor_phone" not in p for p in payloads), "leads has no visitor_phone column"
        signals = payloads[-1]["intent_signals"]
        assert signals["contact_channel"] == "text"
        assert not signals.get("callback_requested")
        assert payloads[-1]["notes"].startswith("CLIENT ASKED FOR TEXTS, NOT CALLS — +12105550100")
        mocks["task_from_callback"].assert_not_called()
        mocks["queue_for_person"].assert_awaited_once()
        assert mocks["queue_for_person"].await_args.kwargs["reason"] == "text request"

    @pytest.mark.asyncio
    async def test_text_said_with_the_number_counts_the_same(self):
        run, mocks = _callback_turn("2105550100  text please")
        resp = await run()
        assert "text you on +12105550100" in resp.message
        assert _lead_payloads(mocks)[-1]["intent_signals"]["contact_channel"] == "text"

    @pytest.mark.asyncio
    async def test_josef_who_asks_to_be_called_is_called(self):
        run, mocks = _callback_turn("please call me on 210-555-0100", lead_signals={"contact_channel": "text"})
        resp = await run()
        assert "call you on +12105550100" in resp.message
        payload = _lead_payloads(mocks)[-1]
        assert payload["intent_signals"]["callback_requested"] is True
        assert payload["notes"].startswith("CLIENT ASKED TO BE CALLED")
        assert mocks["queue_for_person"].await_args.kwargs["reason"] == "call-back request"

    @pytest.mark.asyncio
    async def test_a_bare_number_promises_no_channel(self):
        run, mocks = _callback_turn("2105550100")
        resp = await run()
        assert "reach you on +12105550100" in resp.message
        payload = _lead_payloads(mocks)[-1]
        assert payload["notes"].startswith("CLIENT GAVE A NUMBER (no preference stated)")
        assert payload["intent_signals"]["callback_requested"] is True

    @pytest.mark.asyncio
    async def test_a_name_and_email_from_the_chat_are_never_asked_again(self):
        """17 Nov: the callback reply asked "what's your name?" of clients
        who had given it two messages earlier."""
        run, mocks = _callback_turn(
            "2105550100", lead_extra=_TRIP,
            conv_extra={"visitor_name": "Paul Moreau", "visitor_email": "paul@example.com"},
        )
        resp = await run()
        assert "name" not in resp.message.lower()
        assert "email" not in resp.message.lower()

    @pytest.mark.asyncio
    async def test_an_earlier_no_texts_gets_no_text_and_no_call(self):
        """CodeRabbit #262: "no calls, no texts" then a bare number."""
        run, mocks = _callback_turn("2105550100", lead_signals={"contact_channel": "no calls, no texts"})
        resp = await run()
        assert "text you" not in resp.message and "call you" not in resp.message
        assert "reach you on +12105550100" in resp.message
        signals = _lead_payloads(mocks)[-1]["intent_signals"]
        assert signals["contact_channel"] == "no calls, no texts"
        assert not signals.get("callback_requested")
        mocks["task_from_callback"].assert_not_called()
        assert mocks["queue_for_person"].await_args.kwargs["reason"] == "number given"

    @pytest.mark.asyncio
    async def test_call_me_withdraws_an_older_no_calls(self):
        """CodeRabbit #262: "call me" after "text only, no calls"."""
        run, mocks = _callback_turn("please call me on 210-555-0100",
                                    lead_signals={"contact_channel": "email, text, no calls"})
        await run()
        signals = _lead_payloads(mocks)[-1]["intent_signals"]
        assert signals["contact_channel"] == "email"
        assert signals["callback_requested"] is True

    @pytest.mark.asyncio
    async def test_choosing_texts_ends_an_older_call_back(self):
        """CodeRabbit #262: the panel must not still say "Asks: a call back"."""
        run, mocks = _callback_turn(
            "2105550100  text please",
            lead_signals={"callback_requested": True},
            lead_extra={"notes": "CLIENT ASKED TO BE CALLED — +12105550100\nItinerary: BOS-LHR"},
        )
        await run()
        payload = _lead_payloads(mocks)[-1]
        assert payload["intent_signals"]["callback_requested"] is False
        assert payload["intent_signals"]["contact_channel"] == "text"
        assert payload["notes"] == "CLIENT ASKED FOR TEXTS, NOT CALLS — +12105550100\nItinerary: BOS-LHR"

    @pytest.mark.asyncio
    async def test_a_newer_number_replaces_the_one_the_chat_gave(self):
        run, mocks = _callback_turn("2105550199", conv_extra={"visitor_phone": "+12105550100"})
        await run()
        mocks["update_conversation"].assert_any_await(_CID, {"visitor_phone": "+12105550199"})

    @pytest.mark.asyncio
    async def test_the_same_number_again_is_no_write(self):
        run, mocks = _callback_turn("2105550100", conv_extra={"visitor_phone": "+12105550100"})
        await run()
        for call in mocks["update_conversation"].await_args_list:
            assert "visitor_phone" not in call.args[1]

    @pytest.mark.asyncio
    async def test_a_phone_already_on_file_is_not_overwritten(self):
        run, mocks = _callback_turn("2105550199", visitor=VisitorInfo(name="Anna", phone="+12105550100"))
        await run()
        for call in mocks["update_conversation"].await_args_list:
            assert "visitor_phone" not in call.args[1]


# ── The ordinary turn (Step 4) ──────────────────────────────

class _Stop(Exception):
    pass


def _ordinary_turn(message, history, *, conv_extra=None, visitor=None):
    """Run one turn up to the generator; returns (run, captured)."""
    from app.pipeline.orchestrator import _pipeline

    conv = {"id": _CID, "mode": "ai", "status": "active", "tunnel": "sales", "metadata": {},
            **(conv_extra or {})}
    captured: dict = {"entities": [], "visitor": None, "withdraw": AsyncMock(return_value=0)}
    update_entities = AsyncMock()

    def _gen(*args, **kwargs):
        captured["visitor"] = kwargs.get("visitor")
        raise _Stop("stop after the step under test")

    async def run():
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.conversation_service.get_or_create_conversation",
                                      new=AsyncMock(return_value=conv)))
            stack.enter_context(patch("app.services.conversation_service.add_message",
                                      new=AsyncMock(return_value={"id": "m1"})))
            stack.enter_context(patch("app.pipeline.orchestrator.db.get_recent_messages",
                                      new=AsyncMock(return_value=history)))
            stack.enter_context(patch("app.services.moderation.moderate_message", new=AsyncMock()))
            stack.enter_context(patch("app.pipeline.orchestrator.db.update_conversation", new=AsyncMock()))
            stack.enter_context(patch("app.pipeline.orchestrator.db.update_lead", new=AsyncMock()))
            stack.enter_context(patch("app.pipeline.orchestrator.lead_service.get_or_create_lead",
                                      new=AsyncMock(return_value={"id": "L-1", "conversation_id": _CID})))
            stack.enter_context(patch("app.pipeline.orchestrator.lead_service.update_lead_from_entities",
                                      new=update_entities))
            stack.enter_context(patch("app.services.tasks_service.withdraw_call_tasks", new=captured["withdraw"]))
            stack.enter_context(patch("app.pipeline.orchestrator.generate_response", new=MagicMock(side_effect=_gen)))
            with pytest.raises(_Stop):
                await _pipeline(
                    _CID, message, "sales", visitor or VisitorInfo(), None,
                    _persist_state={"ai_persisted": False},
                )
        captured["entities"] = [c.args[1] for c in update_entities.await_args_list]

    return run, captured


def _history(question, answer):
    return [{"role": "ai", "content": question}, {"role": "user", "content": answer}]


class TestTheAnswerToAlexsQuestion:
    @pytest.mark.asyncio
    async def test_the_name_answered_to_your_name_is_kept(self):
        run, got = _ordinary_turn(
            "Paul Moreau", _history(Q_NAME_3, "Paul Moreau"),
            conv_extra={"visitor_email": "paul@example.com"},
        )
        await run()
        assert got["entities"], "the name must reach the conversation"
        assert got["entities"][0]["name"] == "Paul Moreau"
        # …and the reply is written knowing it: not asked again.
        assert got["visitor"].name == "Paul Moreau"
        assert got["visitor"].email == "paul@example.com"

    @pytest.mark.asyncio
    async def test_the_last_name_joins_the_first(self):
        run, got = _ordinary_turn("Rossi", _history(Q_LAST_NAME, "Rossi"), conv_extra={"visitor_name": "Anna"})
        await run()
        assert got["entities"][0]["name"] == "Anna Rossi"

    @pytest.mark.asyncio
    async def test_a_known_name_is_never_replaced(self):
        run, got = _ordinary_turn("Maria", _history(Q_NAME_3, "Maria"), conv_extra={"visitor_name": "Anna Rossi"})
        await run()
        assert got["visitor"] is not None, "the turn must reach the reply"
        assert got["visitor"].name == "Anna Rossi"
        assert all(e.get("name") == "Anna Rossi" for e in got["entities"])

    @pytest.mark.asyncio
    async def test_free_text_never_names_the_client(self):
        """Not an answer to a name question: "Paul Moreau" here is no name."""
        run, got = _ordinary_turn("Paul Moreau", _history(Q_DATES, "Paul Moreau"))
        await run()
        assert got["visitor"] is not None, "the turn must reach the reply"
        assert not got["visitor"].name
        assert all(not e.get("name") for e in got["entities"])

    @pytest.mark.asyncio
    async def test_a_form_name_is_completed_not_overwritten(self):
        run, got = _ordinary_turn(
            "Next March", _history(Q_DATES, "Next March"),
            conv_extra={"visitor_name": "John Smith"}, visitor=VisitorInfo(name="John"),
        )
        await run()
        assert got["entities"], "the turn must reach the lead write"
        assert got["visitor"].name == "John Smith"
        assert all(e.get("name") in (None, "John Smith") for e in got["entities"])

    @pytest.mark.parametrize("answer, channel", [
        ("Text", "text"),
        ("Text please.  I can’t always take a call", "text"),
        ("Email is best", "email"),
    ])
    @pytest.mark.asyncio
    async def test_the_channel_answered_to_text_or_call_is_kept(self, answer, channel):
        run, got = _ordinary_turn(answer, _history(Q_CHANNEL_AFTER_NAME, answer), conv_extra={"visitor_name": "Ilse"})
        await run()
        assert got["entities"], "the wish must reach the lead"
        assert got["entities"][0]["contact_channel"] == channel

    @pytest.mark.asyncio
    async def test_a_call_answer_stores_no_wish(self):
        """A call is the default; only a text or email wish is a wish."""
        run, got = _ordinary_turn("a call is fine", _history(Q_CHANNEL, "a call is fine"),
                                  conv_extra={"visitor_name": "Anna Rossi"})
        await run()
        assert got["entities"], "the turn must reach the lead write"
        assert all(not e.get("contact_channel") for e in got["entities"])

    @pytest.mark.asyncio
    async def test_the_same_word_without_the_question_is_not_a_wish(self):
        run, got = _ordinary_turn("Text", _history(Q_DATES, "Text"), conv_extra={"visitor_name": "Anna Rossi"})
        await run()
        assert got["entities"], "the turn must reach the lead write"
        assert all(not e.get("contact_channel") for e in got["entities"])


class TestACorrectedContact:
    """CodeRabbit #262: a value the client corrects this turn is the one the
    rest of the turn (the CRM gate and its payload) uses — unless the widget
    sent its own."""

    @pytest.mark.asyncio
    async def test_a_corrected_email_replaces_the_stored_one(self):
        q = "Where should the options land — best email to reach you?"
        run, got = _ordinary_turn(
            "It's paul.new@example.com", _history(q, "It's paul.new@example.com"),
            conv_extra={"visitor_name": "Paul Moreau", "visitor_email": "paul.old@example.com"},
        )
        await run()
        assert got["entities"][0]["email"] == "paul.new@example.com"
        assert got["visitor"].email == "paul.new@example.com"

    @pytest.mark.asyncio
    async def test_the_widgets_email_keeps_priority(self):
        q = "Where should the options land — best email to reach you?"
        run, got = _ordinary_turn(
            "It's paul.new@example.com", _history(q, "It's paul.new@example.com"),
            visitor=VisitorInfo(name="Paul Moreau", email="paul.form@example.com"),
        )
        await run()
        assert got["visitor"].email == "paul.form@example.com"


# ── A call turned down later (CodeRabbit #262, round 2) ─────

_CALL_NOTES = "CLIENT ASKED TO BE CALLED — +12105550100\nItinerary: BOS-LHR"


class _Result:
    def __init__(self, data):
        self.data = data


class _TasksTable:
    """Enough of the Supabase client for one UPDATE on tasks: the filters the
    query chains are applied to the stored rows, as PostgREST would, and only
    the rows that match are written and returned."""

    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]
        self.filters: list[tuple] = []
        self.payload: dict = {}

    def table(self, name):
        assert name == "tasks"
        self.filters = []
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self.filters.append(("is", col, val))
        return self

    def execute(self):
        def matches(row):
            for op, col, val in self.filters:
                if op == "eq" and row.get(col) != val:
                    return False
                if op == "is" and val == "null" and row.get(col) is not None:
                    return False
            return True

        hit = [r for r in self.rows if matches(r)]
        for r in hit:
            r.update(self.payload)
        return _Result([dict(r) for r in hit])


class _LeadsTable:
    """Enough of the Supabase client for update_lead_from_entities: one lead
    row to read, and every update written down."""

    def __init__(self, row):
        self.row = row
        self.updates: list[dict] = []
        self._op = None

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def update(self, payload):
        self._op = "update"
        self.updates.append(payload)
        return self

    def insert(self, _payload):
        self._op = "insert"
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return _Result([dict(self.row)] if self._op == "select" else [])


async def _lead_written(entities: dict, row: dict) -> dict:
    """The lead update that carries the signals, from one Step-4 write."""
    from app.db import supabase as db
    from app.services import lead_service

    client = _LeadsTable(row)

    async def _run(fn, **_kwargs):
        return fn()

    with patch.object(db, "get_client", return_value=client), patch.object(db, "_run_sync", _run):
        await lead_service.update_lead_from_entities(_CID, entities)
    return next(u for u in client.updates if "intent_signals" in u)


class TestACallTurnedDownLater:
    """"Call me", then "text only" or "no calls": the call is no longer owed —
    not in the lead, not on the panel, not as a task someone would act on."""

    @pytest.mark.parametrize("channel", ["text", "no calls", "email, no calls", "email, text"])
    def test_texts_or_no_calls_turn_a_call_down(self, channel):
        assert turns_down_calls(channel) is True

    @pytest.mark.parametrize("channel", [None, "", "email", "no texts", "no email, no texts"])
    def test_other_wishes_do_not(self, channel):
        assert turns_down_calls(channel) is False

    def test_the_call_line_becomes_the_texts_line(self):
        assert notes_without_call(_CALL_NOTES, texts=True) == (
            "CLIENT ASKED FOR TEXTS, NOT CALLS — +12105550100\nItinerary: BOS-LHR"
        )

    def test_no_calls_alone_only_drops_the_call_line(self):
        assert notes_without_call(_CALL_NOTES, texts=False) == "Itinerary: BOS-LHR"

    def test_notes_without_a_call_line_are_left_alone(self):
        assert notes_without_call("CLIENT ASKED FOR TEXTS, NOT CALLS — +12105550100", texts=True) is None
        assert notes_without_call(None, texts=True) is None

    @pytest.mark.asyncio
    async def test_the_lead_drops_the_call_when_texts_are_chosen_later(self):
        payload = await _lead_written(
            {"contact_channel": "text"},
            {"id": "L-1", "intent_signals": {"callback_requested": True}, "notes": _CALL_NOTES},
        )
        assert payload["intent_signals"]["callback_requested"] is False
        assert payload["intent_signals"]["contact_channel"] == "text"
        assert payload["notes"].startswith("CLIENT ASKED FOR TEXTS, NOT CALLS — +12105550100")
        assert "CLIENT ASKED TO BE CALLED" not in payload["notes"]
        assert "Itinerary: BOS-LHR" in payload["notes"]
        assert "Reach by: text" in payload["notes"]

    @pytest.mark.asyncio
    async def test_no_calls_said_later_drops_it_too(self):
        payload = await _lead_written(
            {"contact_channel": "no calls"},
            {"id": "L-1", "intent_signals": {"callback_requested": True}, "notes": _CALL_NOTES},
        )
        assert payload["intent_signals"]["callback_requested"] is False
        assert "CLIENT ASKED" not in payload["notes"]
        assert "Reach by: no calls" in payload["notes"]

    @pytest.mark.asyncio
    async def test_an_email_wish_leaves_the_call_standing(self):
        payload = await _lead_written(
            {"contact_channel": "email"},
            {"id": "L-1", "intent_signals": {"callback_requested": True}, "notes": _CALL_NOTES},
        )
        assert payload["intent_signals"]["callback_requested"] is True
        assert payload["notes"].startswith("CLIENT ASKED TO BE CALLED — +12105550100")

    @pytest.mark.asyncio
    async def test_choosing_texts_withdraws_the_call_task_an_earlier_ask_made(self):
        run, mocks = _callback_turn("2105550100  text please", lead_signals={"callback_requested": True})
        await run()
        mocks["withdraw_call_tasks"].assert_called_once_with(_CID)
        mocks["task_from_callback"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_number_after_no_calls_withdraws_it_too(self):
        run, mocks = _callback_turn("2105550100", lead_signals={"contact_channel": "no calls"})
        await run()
        mocks["withdraw_call_tasks"].assert_called_once_with(_CID)
        mocks["task_from_callback"].assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message, signals", [
        ("please call me on 210-555-0100", {"contact_channel": "no calls"}),
        ("2105550100", None),
    ])
    async def test_a_call_ask_or_a_bare_number_withdraws_nothing(self, message, signals):
        run, mocks = _callback_turn(message, lead_signals=signals)
        await run()
        mocks["withdraw_call_tasks"].assert_not_called()
        mocks["task_from_callback"].assert_called_once()

    @pytest.mark.asyncio
    async def test_texts_chosen_in_an_ordinary_message_withdraw_the_task(self):
        run, got = _ordinary_turn("Text", _history(Q_CHANNEL, "Text"), conv_extra={"visitor_name": "Anna Rossi"})
        await run()
        got["withdraw"].assert_called_once_with(_CID)

    @pytest.mark.asyncio
    async def test_no_calls_in_an_ordinary_message_withdraws_it_too(self):
        run, got = _ordinary_turn("no calls please", _history(Q_DATES, "no calls please"),
                                  conv_extra={"visitor_name": "Anna Rossi"})
        await run()
        got["withdraw"].assert_called_once_with(_CID)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer, question", [("Email", Q_CHANNEL), ("Paul Moreau", Q_NAME)])
    async def test_other_answers_withdraw_nothing(self, answer, question):
        run, got = _ordinary_turn(answer, _history(question, answer))
        await run()
        got["withdraw"].assert_not_called()


class TestAnUnreadableLead:
    """CodeRabbit #262: when the lead cannot be read, the client's earlier
    "Text" or "no calls" is unknown — a bare number is then no call request."""

    @pytest.mark.asyncio
    async def test_a_bare_number_is_no_call_when_the_wish_cannot_be_read(self):
        run, mocks = _callback_turn("2105550100", lead_fails=True)
        resp = await run()
        assert "reach you on +12105550100" in resp.message
        assert "call you" not in resp.message
        mocks["task_from_callback"].assert_not_called()
        mocks["withdraw_call_tasks"].assert_not_called()
        assert mocks["queue_for_person"].await_args.kwargs["reason"] == "number given"

    @pytest.mark.asyncio
    async def test_call_me_still_counts(self):
        run, mocks = _callback_turn("please call me on 210-555-0100", lead_fails=True)
        resp = await run()
        assert "call you on +12105550100" in resp.message
        mocks["task_from_callback"].assert_called_once()
        assert mocks["queue_for_person"].await_args.kwargs["reason"] == "call-back request"


class TestWithdrawingTheCallTask:
    @staticmethod
    def _rows():
        return [
            {"id": "t-open", "source": "client_asked", "kind": "call"},
            {"id": "t-done", "source": "client_asked", "kind": "call", "done_at": "2026-11-17T05:00:00+00:00"},
            {"id": "t-gone", "source": "client_asked", "kind": "call", "dismissed_at": "2026-11-17T05:00:00+00:00"},
            {"id": "t-email", "source": "client_asked", "kind": "email"},
            {"id": "t-caught", "source": "caught", "kind": "call"},
        ]

    @pytest.mark.asyncio
    async def test_only_the_open_call_the_client_asked_for_is_withdrawn(self):
        from app.services import tasks_service

        update = AsyncMock(return_value={"id": "t-open"})
        event = AsyncMock()
        before = tasks_service.db.TASKS_HEALTH["withdrawn"]
        with patch.object(tasks_service.db, "tasks_of_conversations", new=AsyncMock(return_value=self._rows())), \
                patch.object(tasks_service.db, "withdraw_open_call_task", new=update), \
                patch.object(tasks_service.db, "add_task_event", new=event):
            assert await tasks_service.withdraw_call_tasks(_CID) == 1
        assert [c.args[0] for c in update.await_args_list] == ["t-open"]
        assert datetime.fromisoformat(update.await_args.args[1]).tzinfo is not None
        assert event.await_args.args[:2] == ("t-open", "withdrawn")
        assert tasks_service.db.TASKS_HEALTH["withdrawn"] == before + 1

    @pytest.mark.asyncio
    async def test_a_task_already_gone_counts_nothing(self):
        from app.services import tasks_service

        event = AsyncMock()
        with patch.object(tasks_service.db, "tasks_of_conversations", new=AsyncMock(return_value=self._rows())), \
                patch.object(tasks_service.db, "withdraw_open_call_task", new=AsyncMock(return_value=None)), \
                patch.object(tasks_service.db, "add_task_event", new=event):
            assert await tasks_service.withdraw_call_tasks(_CID) == 0
        event.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_write_itself_checks_the_task_is_still_an_open_call(self):
        """The UPDATE carries the conditions, not only the read before it: a
        finished, dismissed or other task is left as it is (CodeRabbit #262)."""
        from app.db import supabase as db

        stored = [
            {"id": "t-open", "source": "client_asked", "kind": "call", "done_at": None, "dismissed_at": None},
            {"id": "t-done", "source": "client_asked", "kind": "call",
             "done_at": "2026-11-17T05:00:00+00:00", "dismissed_at": None},
            {"id": "t-gone", "source": "client_asked", "kind": "call",
             "done_at": None, "dismissed_at": "2026-11-17T05:00:00+00:00"},
            {"id": "t-email", "source": "client_asked", "kind": "email", "done_at": None, "dismissed_at": None},
            {"id": "t-caught", "source": "caught", "kind": "call", "done_at": None, "dismissed_at": None},
        ]
        client = _TasksTable(stored)

        async def _run(fn, *_a, **_k):
            return fn()

        at = "2026-11-17T06:20:00+00:00"
        with patch.object(db, "get_client", return_value=client), patch.object(db, "_run_sync", _run):
            assert (await db.withdraw_open_call_task("t-open", at))["dismissed_at"] == at
            for task_id in ("t-done", "t-gone", "t-email", "t-caught"):
                assert await db.withdraw_open_call_task(task_id, at) is None
        rows = {r["id"]: r for r in client.rows}
        assert rows["t-done"]["dismissed_at"] is None
        assert rows["t-gone"]["dismissed_at"] == "2026-11-17T05:00:00+00:00"
        assert rows["t-email"]["dismissed_at"] is None
        assert rows["t-caught"]["dismissed_at"] is None

    @pytest.mark.asyncio
    async def test_a_task_finished_after_the_read_stays_done(self):
        """The read still shows the call open; an agent marks it done before
        the write: it is not withdrawn, counted or given a history line."""
        from app.db import supabase as db
        from app.services import tasks_service

        stale = [{"id": "t-open", "source": "client_asked", "kind": "call"}]
        client = _TasksTable([{"id": "t-open", "source": "client_asked", "kind": "call",
                               "done_at": "2026-11-17T06:19:59+00:00", "dismissed_at": None}])

        async def _run(fn, *_a, **_k):
            return fn()

        event = AsyncMock()
        before = db.TASKS_HEALTH["withdrawn"]
        with patch.object(db, "get_client", return_value=client), patch.object(db, "_run_sync", _run), \
                patch.object(tasks_service.db, "tasks_of_conversations", new=AsyncMock(return_value=stale)), \
                patch.object(tasks_service.db, "add_task_event", new=event):
            assert await tasks_service.withdraw_call_tasks(_CID) == 0
        assert client.rows[0]["dismissed_at"] is None
        event.assert_not_called()
        assert db.TASKS_HEALTH["withdrawn"] == before

    @pytest.mark.asyncio
    async def test_before_the_tasks_migration_nothing_happens(self):
        from app.services import tasks_service

        with patch.object(tasks_service.db, "tasks_of_conversations",
                          new=AsyncMock(side_effect=tasks_service.db.TasksNotSetUp("no tasks table"))):
            assert await tasks_service.withdraw_call_tasks(_CID) == 0

    @pytest.mark.asyncio
    async def test_a_failure_never_reaches_the_turn(self):
        from app.services import tasks_service

        before = tasks_service.db.TASKS_HEALTH["withdraw_failures"]
        with patch.object(tasks_service.db, "tasks_of_conversations",
                          new=AsyncMock(side_effect=RuntimeError("database hiccup"))):
            assert await tasks_service.withdraw_call_tasks(_CID) == 0
        assert tasks_service.db.TASKS_HEALTH["withdraw_failures"] == before + 1

    def test_the_history_says_why_it_went(self):
        from app.services.tasks_view import history_of

        lines = history_of(
            [{"kind": "withdrawn", "at": "2026-11-17T06:20:00+00:00", "actor_id": None, "details": {}}],
            {}, client="Anna",
        )
        assert lines[0]["text"] == "Withdrawn: Anna then chose texts or said no calls."
