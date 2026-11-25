"""The client's name, only from the client's own words (the owner, 25 Nov 2026): in the week before, 155 of 188
chats an agent had carried no name, and 72 of them reached the CRM without one. Nobody types a name in the panel
(an agent writing one from his own head could be taken for manipulation); the chat reads more of the ways the
client gives it, and the agent's open chat shows it at once. Every name, address and number here is invented."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.pipeline.contact_capture import (
    asks_for_name, name_beside_contact, name_from_answer, name_from_intro, name_in_reply,
)

ASKED_NAME = "Happy to help. Could I have your name and e-mail?"
ASKED_DATES = "Which dates work for you?"


# ── What a client's message names ───────────────────────────────


@pytest.mark.parametrize("message, name", [
    ("Hi, my name is Ana Popescu, two of us to Rome", "Ana Popescu"),
    ("my name is ana popescu please call me", "Ana Popescu"),
    ("My name is John A. Smith and I need 2 tickets", "John A. Smith"),
    ("My name’s Ana but my husband travels", "Ana"),
    ("Hello! My name is Paul. I want business class to Paris", "Paul"),
    ("My first name is Ana", "Ana"),
    ("me llamo maría josé y viajo a Madrid", "María José"),
    # The sentence that goes on after the name ends it (CodeRabbit #282, round 3); a name that ends in -ing stays.
    ("My name is Ana looking for flights", "Ana"),
    ("My name is Ana Popescu looking to fly", "Ana Popescu"),
    ("my name is john trying to book", "John"),
    ("My name is Ana calling about my booking", "Ana"),
    ("My name is Li Ming", "Li Ming"),
    ("My name is Will Smith", "Will Smith"),
    ("Je m’appelle Lucie Martin.", "Lucie Martin"),
    ("meu nome é Ana e moro em Lisboa", "Ana"),
    # Four words at most, and never a particle at the end.
    ("My name is María José García de la Fuente", "María José García"),
])
def test_my_name_is_names_the_client(message, name):
    assert name_from_intro(message) == name


@pytest.mark.parametrize("message", [
    "my name is on the booking",
    "my name is spelled with a K",
    "my name is not important",
    "my wife's name is Maria",
    "I'm looking for flights",          # the extractor's old mistake: "I'm …" names nobody
    "This is urgent",
    "my name is 007",
    "Paul Moreau",
    "",
])
def test_anything_else_names_nobody(message):
    assert name_from_intro(message) is None


@pytest.mark.parametrize("message, name", [
    ("Ana Popescu, ana@example.com", "Ana Popescu"),
    ("ana@example.com - Ana", "Ana"),
    ("Ana, +40 721 000 000", "Ana"),
    ("Paul Moreau and paul@example.com", "Paul Moreau"),
    ("Email: paul@example.com, name: Paul Moreau", "Paul Moreau"),
    ("Full name: Ana Popescu, +40 721 000 000", "Ana Popescu"),
])
def test_the_name_beside_the_contact(message, name):
    assert name_beside_contact(message) == name


@pytest.mark.parametrize("message", [
    "Ana, Boston, ana@example.com",     # two pieces of text: a name and a city read alike
    "Sure, it's paul.moreau@example.com",
    "you can call me at +1 (305) 555-0101 after 6pm",
    "paul.moreau@example.com or +44 7700 900123",
    "Write to ana@example.com",
    "Here: ana@example.com",
    "ana@example.com to Rome",
    "JFK to LHR on 2026-12-20",         # a date is no phone
    "Ana 2026-12-20",
    "Paul Moreau",                      # no contact: name_from_answer's case
])
def test_beside_no_contact_or_beside_more_than_a_name_nobody(message):
    assert name_beside_contact(message) is None


def test_a_sentence_after_the_name_question_is_no_name():
    """CodeRabbit #282, round 3: after the name question the reply was taken whole, "My name is Ana looking to fly"
    became "Ana Looking To Fly". A sentence is no name; "my name is …" still gives the name."""
    assert name_in_reply("My name is Ana looking to fly", ASKED_NAME) == "Ana"
    assert name_in_reply("my name is ana, looking for business class", ASKED_NAME) == "Ana"
    for reply in ["I'm Ana looking to fly", "It's JFK to LHR", "Ana from Boston", "Ana looking to fly"]:
        assert name_in_reply(reply, ASKED_NAME) is None, reply
        assert name_from_answer(reply) is None, reply
    # Rule 5: a name alone, and a full name with its joining word, are still names.
    assert name_in_reply("Ana Popescu", ASKED_NAME) == "Ana Popescu"
    assert name_from_answer("Ortega y Gasset") is not None


def test_the_answer_after_a_rule_about_the_name_is_no_name():
    assert name_in_reply("Boston", "Please make sure your name matches your passport. Where are you flying from?") is None


def test_beside_the_contact_only_when_the_name_was_asked():
    assert name_in_reply("Ana Popescu, ana@example.com", ASKED_NAME) == "Ana Popescu"
    assert name_in_reply("Ana Popescu", ASKED_NAME) == "Ana Popescu"
    # Rule 5: the same messages, the name not asked.
    assert name_in_reply("Ana Popescu, ana@example.com", ASKED_DATES) is None
    assert name_in_reply("Ana Popescu", ASKED_DATES) is None
    # "my name is …" needs no question.
    assert name_in_reply("my name is Ana Popescu", ASKED_DATES) == "Ana Popescu"


def test_an_initial_is_no_article():
    assert name_from_answer("John A. Smith") == "John A. Smith"
    assert name_from_answer("a smith") is None


# ── The question an agent or Skye asked (18-25 Nov: the shapes whose answer was a name and none kept) ─────


@pytest.mark.parametrize("question", [
    # Agents, without a question mark.
    "Also your name please to address you better.",
    "whats your name",
    "Could you please advise your name, email and phone number to create a lead",
    "please your name and contact details",
    "Can you please help me with your name and email to share some options with you?",
    "Name",
    "Hi, I'm Ana, happy to assist you, before we move forward, may I ask:\n1. Your name\n2. Email",
    "May I know your good name?",
    "My name is Ana, what about yours?",
    # Skye.
    "Last thing, and a name to put on the request?",
    "Still just need a name for the file, who should they ask for when they reach out?",
    "Who should they ask for when they reach out, what name is this under?",
    # A request with words after the name still asks for it.
    "Please share your name",
    "please type your name exactly as in your passport",
    "What is your full name as it appears on the passport?",
])
def test_the_name_question_in_the_shapes_it_was_asked(question):
    assert asks_for_name(question)


@pytest.mark.parametrize("question", [
    "Thanks, your name is noted. Which dates work for you?",
    "My name is Ana, and I'll be happy to assist you with your flight search.",
    "Hi, my name is Ana from BuyBusinessClass.",
    "I'll pass your name to the consultant.",
    "Your name is on file, thanks!",
    "What's the name of the airline you prefer?",
    "Name of the hotel?",
    "Please confirm the dates",
    # A rule about the name asks for nothing (CodeRabbit #282, round 3).
    "Please make sure your name matches your passport. Where are you flying from?",
    "Kindly note your name must match the ID.",
    "Please check your name spelling.",
    "Does your name match your passport, and where are you flying from?",
    "Please note that your name will appear on the ticket. Which dates?",
])
def test_a_statement_or_another_question_asks_no_name(question):
    """Rule 5: the agent's own name, a name already noted, and another thing's name."""
    assert not asks_for_name(question)


@pytest.mark.parametrize("answer, name", [
    ("Ok John", "John"),
    ("Sure, it's Ana", "Ana"),
    ("Hi, Mike", "Mike"),
    ("John, thanks", "John"),
    ("Thank you, Maria Lopez", "Maria Lopez"),
    ("Of course, Dr. Ana", "Dr. Ana"),
])
def test_the_courtesy_around_a_name_is_taken_off(answer, name):
    assert name_from_answer(answer) == name


@pytest.mark.parametrize("answer", ["ok great", "sure, john", "Thanks!", "yes please", "hello", "Yes"])
def test_after_the_courtesy_only_a_capitalised_name_counts(answer):
    """"ok great" would otherwise name the client "Great"."""
    assert name_from_answer(answer) is None


def test_nobody_writes_a_name_in_the_panel():
    """The owner, 25 Nov 2026: no route takes a name typed by an agent; the old PATCH stays management's only."""
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/conversations/{conversation_id}/name" not in paths


# ── The agent's open chat shows it at once ────────────────────────────


class _Recorder:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self
        return call

    def execute(self):
        res = MagicMock()
        res.data = self.rows
        return res


def _write(entities, *, lead_rows=None):
    lead = _Recorder(lead_rows if lead_rows is not None else [{"id": "L-1", "score": 0}])
    conv = _Recorder([{"visitor_name": None, "visitor_email": None, "visitor_phone": None}])
    client = MagicMock()
    client.table.side_effect = lambda name: conv if name == "conversations" else lead
    pushed = AsyncMock()
    from app.services import lead_service

    with patch("app.db.supabase.get_client", return_value=client), \
            patch("app.realtime.manager.manager.push", new=pushed):
        ok = asyncio.run(lead_service.update_lead_from_entities("c-1", entities))
    return ok, pushed


def test_a_name_saved_on_the_chat_tells_the_agents_open_chat_at_once():
    ok, pushed = _write({"name": "Ana Popescu"})
    assert ok is True
    pushed.assert_awaited_once_with("op:c-1", {"event": "contact", "fields": ["visitor_name"]})
    # The event names the field, never the value.
    assert "Popescu" not in repr(pushed.await_args)


def test_an_email_and_a_phone_too_and_a_trip_alone_tells_nothing():
    ok, pushed = _write({"email": "ana@example.com", "phone": "+40 721 000 001"})
    assert ok is True
    assert pushed.await_args.args[1] == {"event": "contact", "fields": ["visitor_email", "visitor_phone"]}
    # Rule 5: a route changes no contact on the chat.
    ok, pushed = _write({"origin": "JFK", "destination": "LHR"})
    assert ok is True
    pushed.assert_not_awaited()


class _LeadWriteFails(_Recorder):
    """The lead is read, then its write is refused."""

    def __init__(self, rows):
        super().__init__(rows)
        self.writing = False

    def update(self, *args, **kwargs):
        self.writing = True
        return self

    def execute(self):
        if self.writing:
            raise RuntimeError("refused")
        return super().execute()


def test_a_contact_saved_on_the_chat_is_told_even_when_the_leads_write_fails():
    """CodeRabbit #282, round 3: the chat's write landed, the agent's open chat hears of it; the lead's failure goes
    on as before."""
    lead = _LeadWriteFails([{"id": "L-1", "score": 0}])
    conv = _Recorder([{"visitor_name": None, "visitor_email": None, "visitor_phone": None}])
    client = MagicMock()
    client.table.side_effect = lambda name: conv if name == "conversations" else lead
    pushed = AsyncMock()
    from app.services import lead_service

    with patch("app.db.supabase.get_client", return_value=client), \
            patch("app.realtime.manager.manager.push", new=pushed):
        ok = asyncio.run(lead_service.update_lead_from_entities("c-1", {"name": "Ana Popescu", "passengers": 2}))
    assert ok is False
    pushed.assert_awaited_once_with("op:c-1", {"event": "contact", "fields": ["visitor_name"]})


def test_a_push_that_fails_never_fails_the_save(caplog):
    lead = _Recorder([{"id": "L-1", "score": 0}])
    conv = _Recorder([{"visitor_name": None, "visitor_email": None, "visitor_phone": None}])
    client = MagicMock()
    client.table.side_effect = lambda name: conv if name == "conversations" else lead
    from app.services import lead_service

    with patch("app.db.supabase.get_client", return_value=client), \
            patch("app.realtime.manager.manager.push", new=AsyncMock(side_effect=RuntimeError("down"))):
        assert asyncio.run(lead_service.update_lead_from_entities("c-1", {"name": "Ana Popescu"})) is True
    assert "the panel not told (RuntimeError)" in caplog.text
