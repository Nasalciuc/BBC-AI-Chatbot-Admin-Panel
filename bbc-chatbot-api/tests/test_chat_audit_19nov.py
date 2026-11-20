"""What the 42 chats of 18-19 Nov 2026 showed, held by tests (the trips here are made up).

- a client who asked for the fares by email is asked the email next, route or not (#3331, #3332, #3344);
- "What's the best name for your consultant to address you by?" is a name question (#3361 was asked again);
- "Tks", "thx", "ty" are thanks (#3352 heard the consultant line twice);
- outside team hours nobody is "with a client", nobody calls "shortly" (#3359, #3361, #3323);
- a client who writes after the closing is kept, and a resend adds nothing (#3323, #3361);
- the prompt never names a keyword or a name read from an email (#3359, #3341);
- a return the day after departure is asked about once (#3356);
- the cards name the cities they used to give as codes (BER, DPS, DNZ, SGN…).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai.prompts import build_conversational_prompt, build_site_context  # noqa: E402
from app.ai.templates import get_template  # noqa: E402
from app.models.chat import VisitorInfo  # noqa: E402
from app.pipeline.contact_capture import asks_for_name  # noqa: E402
from app.pipeline.intent import Intent, detect_intent, is_only_goodbye  # noqa: E402
from app.services import closing, proactive  # noqa: E402


def _prompt(lead, history, meta=None, visitor=None, entities=None):
    _static, dynamic = build_conversational_prompt(
        "sales", visitor or VisitorInfo(), lead=lead, history=history,
        entities={"_metadata": meta or {}, **(entities or {})},
    )
    return dynamic


# ── The email, when the client asked for the fares by email ──

_CARD = [{"role": "ai", "content": "Rather not fill in the whole form? I can pass Athens, Business to an agent "
                                   "right here, and they'll email you the fares."},
         {"role": "user", "content": "Email me the fares"}]
_DEST_ONLY = {"destination_code": "ATH", "intent_signals": {"contact_channel": "email"}, "score": 10}


def test_a_client_who_asked_for_email_is_asked_it_next_route_or_not():
    dynamic = _prompt(_DEST_ONLY, _CARD, {"proactive_trigger": "quote", "proactive_variant": "contact"})
    assert "Ask next: the email — they asked for the fares by email" in dynamic
    assert "before the route, the dates or the travelers" in dynamic


def test_the_wish_said_in_this_message_counts_before_the_lead_has_it():
    lead = {"destination_code": "ATH", "score": 10}
    dynamic = _prompt(lead, _CARD, {"proactive_trigger": "quote"}, entities={"contact_channel": "email"})
    assert "Ask next: the email — they asked for the fares by email" in dynamic


@pytest.mark.parametrize("lead, history, visitor", [
    (_DEST_ONLY, _CARD, VisitorInfo(email="client@example.com")),                     # on file
    (_DEST_ONLY, _CARD + [{"role": "ai", "content": "Where should they send them — your email?"},
                          {"role": "user", "content": "from denver"}], None),          # asked already
    ({**_DEST_ONLY, "intent_signals": {"contact_channel": "no email"}}, _CARD, None),  # turned down
])
def test_not_pushed_when_on_file_asked_or_turned_down(lead, history, visitor):
    assert "Ask next: the email" not in _prompt(lead, history, {"proactive_trigger": "quote"}, visitor)


# ── The name ──

@pytest.mark.parametrize("question", [
    "What's the best name for your consultant to address you by?",
    "What's the best name to put on the request?",
    "Nice to meet you. What's the best name to have on file for this request?",
    "Which name should I use to address you?",
    "How should I address you?",
])
def test_the_best_name_is_a_name_question(question):
    assert asks_for_name(question)


@pytest.mark.parametrize("question", [
    "Which address should we send the tickets to?",
    "What is the address you want the tickets sent to?",  # CodeRabbit #271
    "What's the name of the airline you prefer?",
    "Name noted. Where are you flying from?",
])
def test_not_a_name_question(question):
    assert not asks_for_name(question)


def test_a_name_never_read_from_an_email():
    dynamic = _prompt({"score": 5}, [], visitor=VisitorInfo(email="first.last@example.com"))
    assert "Name: not given — call the client by a name only once they type it" in dynamic
    assert "Name: not given" not in _prompt({"score": 5}, [], visitor=VisitorInfo(name="Pat"))


# ── Thanks a phone types ──

@pytest.mark.parametrize("text", ["Tks", "thx", "ty", "Thnx!", "tysm", "ok tks"])
def test_short_thanks_are_thanks(text):
    assert is_only_goodbye(text)
    assert detect_intent(text, use_llm=False) == Intent.CLOSING


@pytest.mark.parametrize("text", ["tks, what about Paris?", "ty — can you check Feb 1?"])
def test_short_thanks_with_more_are_answered(text):
    assert not is_only_goodbye(text)


# ── Team hours ──

def test_off_hours_no_agent_line_promises_nothing_it_cannot_keep():
    text = get_template("no_agent_available_off_hours", "sales")
    assert "offline right now" in text and "9 AM US Eastern" in text
    assert "with a client" not in text and "shortly" not in text and "right away" not in text


def test_off_hours_call_back_names_when():
    text = get_template("callback_confirmed_off_hours", "sales", number="+1 202 555 0143")
    assert text == ("Of course — a consultant will call you on +1 202 555 0143 first thing when the team is back "
                    "(from 9 AM US Eastern).")


def test_the_support_tunnel_keeps_its_line():
    assert get_template("no_agent_available_off_hours", "support") is None


@pytest.mark.parametrize("online, words", [(True, "soon"), (False, "first thing when the team is back")])
def test_when_we_reach_out(online, words):
    with patch.object(closing, "is_within_team_hours", return_value=online):
        assert closing.when_we_reach_out() == words


# ── A client who writes after the closing ──

def _ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


_CLOSING = "Thank you — your consultant will reach out first thing."
_ACK_OFF = "Thank you! Your consultant will reach out first thing when the team is back."


def _closed(yes_ago=20):
    """The "Yes" the closing answered, `yes_ago` seconds old: built when the test runs (CodeRabbit #271)."""
    return [{"id": "u1", "role": "user", "content": "Yes", "created_at": _ago(yes_ago)},
            {"id": "a1", "role": "ai", "content": _CLOSING, "created_at": _ago(5)}]


class _Store:
    """A conversation's rows: get_recent_messages reads them, add_message adds to them, and each waits once, as
    a round trip to the database would. A save of the role `fail` fails (add_message → None)."""

    def __init__(self, rows, fail=None):
        self.rows, self.added, self.fail = list(rows), [], fail

    async def recent(self, cid, limit=5):
        await asyncio.sleep(0)
        return [dict(r) for r in self.rows[-limit:]]

    async def add(self, cid, role, content, model_used=None, cost=0.0, **_):
        await asyncio.sleep(0)
        if role == self.fail:
            return None
        row = {"id": f"m{len(self.rows)}", "role": role, "content": content, "created_at": _ago(0)}
        self.rows.append(row)
        self.added.append((role, content))
        return row


async def _after_closing(messages, store, online=False):
    from app.api import chat as chat_api

    with patch.object(chat_api.db, "get_recent_messages", new=store.recent), \
         patch.object(chat_api.conversation_service, "add_message", new=store.add), \
         patch("app.services.closing.is_within_team_hours", return_value=online):
        return await asyncio.gather(*(chat_api._post_sale_reply("c-1", m) for m in messages))


@pytest.mark.asyncio
async def test_what_the_client_writes_after_the_closing_is_kept():
    store = _Store(_closed())
    [out] = await _after_closing(["Can you also check March 2?"], store)
    assert store.added == [("user", "Can you also check March 2?"), ("ai", _ACK_OFF)]
    assert out.message == _ACK_OFF and out.type == "post_sale"
    assert out.message_id == store.rows[-1]["id"]  # the stored ack: the widget never draws it twice


@pytest.mark.asyncio
async def test_in_team_hours_the_ack_says_soon():
    [out] = await _after_closing(["thanks again"], _Store(_closed()), online=True)
    assert out.message == "Thank you! Your consultant will reach out soon."


@pytest.mark.asyncio
async def test_a_resend_of_the_answered_message_adds_nothing():
    """The widget's retry of the "Yes" the closing answered (#3361): nothing stored, the closing said again."""
    store = _Store(_closed())
    [out] = await _after_closing(["Yes"], store)
    assert store.added == [] and out.message == _CLOSING and out.message_id == "a1"


@pytest.mark.asyncio
async def test_a_resend_before_its_answer_is_stored_gets_the_ack_and_adds_nothing():
    """The send it repeats is saved and its answer not yet: not the closing before it, and no second row
    (CodeRabbit #271)."""
    store = _Store(_closed() + [{"id": "u2", "role": "user", "content": "And March 2?", "created_at": _ago(3)}])
    [out] = await _after_closing(["And March 2?"], store)
    assert store.added == [] and out.message == _ACK_OFF and out.message_id is None


@pytest.mark.asyncio
async def test_the_same_words_much_later_are_new_words():
    store = _Store(_closed(yes_ago=600))
    await _after_closing(["Yes"], store)
    assert store.added[0] == ("user", "Yes")


@pytest.mark.asyncio
async def test_two_sends_at_once_are_stored_once():
    """The same words sent twice at once (a double tap, a retry on top of its send): one row, one ack, the same
    answer to both (CodeRabbit #271)."""
    store = _Store(_closed())
    a, b = await _after_closing(["And March 2?", "And March 2?"], store)
    assert store.added == [("user", "And March 2?"), ("ai", _ACK_OFF)]
    assert a.message == b.message == _ACK_OFF


@pytest.mark.asyncio
async def test_a_message_it_could_not_store_gets_no_ack():
    """The widget takes a 200 for the save receipt: a save that failed is a 503, and no ack is stored
    (CodeRabbit #271)."""
    from fastapi import HTTPException

    from app.api import chat as chat_api

    store = _Store(_closed(), fail="user")
    before = chat_api.POST_SALE_HEALTH["unsaved"]
    with pytest.raises(HTTPException) as err:
        await _after_closing(["And March 2?"], store)
    assert err.value.status_code == 503 and store.added == []
    assert chat_api.POST_SALE_HEALTH["unsaved"] == before + 1


# ── What the prompt never says ──

def test_the_keyword_is_private():
    block = build_site_context({"utm_source": "google", "utm_medium": "cpc", "utm_term": "business class athens"})
    assert "Search keyword hint: business class athens (private — never quote it or say there was one)" in block
    assert "never say there was a keyword, an ad or a search behind the visit" in block


# ── A return the day after departure ──

_NEXT_DAY = {"origin_code": "DEN", "destination_code": "ATH", "departure_date": "2027-02-10",
             "return_date": "2027-02-11", "passengers": 1, "score": 30}


def test_a_return_the_day_after_departure_is_asked_about():
    assert "Check the dates: the return is the day after departure" in _prompt(_NEXT_DAY, [])


@pytest.mark.parametrize("lead, history, meta", [
    ({**_NEXT_DAY, "return_date": "2027-02-20"}, [], {}),                                  # a real stay
    (_NEXT_DAY, [{"role": "ai", "content": "Is it one-way, or when do you fly back?"}], {}),  # asked
    (_NEXT_DAY, [], {"confirmed_at": "2026-11-19T10:00:00+00:00"}),                       # confirmed
])
def test_otherwise_the_dates_are_left_alone(lead, history, meta):
    assert "Check the dates" not in _prompt(lead, history, meta)


@pytest.mark.parametrize("question, asked", [
    ("Is it one-way, or when do you fly back?", True),
    ("When would you like to return?", True),
    ("¿Es solo ida, o cuándo vuelve?", True),
    ("Is Feb 11 your return, or the day you arrive?", True),
    ("When is your return flight?", True),                        # CodeRabbit #272: asked directly
    ("What's your return date?", True),
    ("What date works for the return?", True),
    ("Which cabin would you prefer for the return flight?", False),  # round 3: asks the cabin, not the day
    ("Departing Feb 10, returning Feb 11, 1 traveler — is that right?", False),  # the summary, said back
    ("Would you prefer a refundable ticket for the return?", False),  # names it, asks something else
])
def test_the_return_asked_about_not_only_said_back(question, asked):
    """#3356: the dates were said back four times and never asked about: a summary that names the return
    is no question about it."""
    dynamic = _prompt(_NEXT_DAY, [{"role": "ai", "content": question}])
    assert ("Check the dates" not in dynamic) is asked


def test_the_email_first_then_the_dates():
    """One question per reply (CodeRabbit #271): the email when they asked for the fares by email, the dates
    on the reply after it."""
    lead = {**_NEXT_DAY, "intent_signals": {"contact_channel": "email"}}
    first = _prompt(lead, _CARD)
    assert "Ask next: the email" in first and "Check the dates" not in first
    later = _prompt(lead, _CARD, visitor=VisitorInfo(email="client@example.com"))
    assert "Check the dates" in later and "Ask next: the email" not in later


# ── Cities, not codes ──

@pytest.mark.parametrize("code, city", [
    ("BER", "Berlin"), ("DPS", "Bali"), ("DNZ", "Denizli"), ("SGN", "Ho Chi Minh City"), ("SAL", "San Salvador"),
    ("AMD", "Ahmedabad"), ("WAS", "Washington"), ("DVO", "Davao"), ("NSI", "Yaoundé"), ("TRN", "Turin"),
    ("OPO", "Porto"), ("YHZ", "Halifax"), ("ABV", "Abuja"), ("WSI", "Western Sydney"),
])
def test_the_card_names_the_city(code, city):
    assert proactive._city(code) == city


# ── The replies the pipeline sends, by the clock ──

@pytest.mark.parametrize("online, words", [(True, "Every consultant is with a client right now"),
                                           (False, "Our consultants are offline right now")])
def test_the_no_agent_line_follows_the_clock(online, words):
    from app.pipeline.orchestrator import _no_agent_line

    with patch("app.services.closing.is_within_team_hours", return_value=online):
        assert _no_agent_line("sales", VisitorInfo()).startswith(words)


@pytest.mark.asyncio
@pytest.mark.parametrize("online, start, join", [
    (True, "Of course — I'm putting a consultant on this to call you on +12105550100.", " While they get to you — "),
    (False, "Of course — a consultant will call you on +12105550100 first thing when the team is back "
            "(from 9 AM US Eastern).", " Meanwhile, "),
])
async def test_a_call_back_at_night_is_promised_for_when_the_team_is_back(online, start, join):
    """Near midnight US Eastern (#3359): "I'm putting a consultant on this to call you … While they get to
    you", and the client waited for the call."""
    from tests.test_chat_contacts import _callback_turn

    run, _ = _callback_turn("please call me on 210-555-0100", conv_extra={"visitor_email": "client@example.com"})
    with patch("app.services.closing.is_within_team_hours", return_value=online):
        resp = await run()
    assert resp.message.startswith(start)
    assert join in resp.message
