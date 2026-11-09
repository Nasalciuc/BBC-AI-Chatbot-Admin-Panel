"""Follow-ups: the fixed phrases that turn an agent's promise into a task, and
the due time read from the same words. Sentences are the agents' own, from 30
days of production messages (8 Nov 2026), with names and numbers left out.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.services.follow_ups import (  # noqa: E402
    DEFAULT_RULES,
    catch_promise,
    due_from,
    normalize,
    rules_of,
    title_for,
    try_sentence,
)

RULES = rules_of(None)
PHRASES = RULES["phrases"]
# Friday 6 Nov 2026, 14:07 in New York (EST, UTC-5).
SENT = datetime(2026, 11, 6, 19, 7, tzinfo=timezone.utc)


def ny(day: int, hh: int, mm: int = 0) -> datetime:
    """A New York wall time in November 2026 (EST, UTC-5), as UTC."""
    return datetime(2026, 11, day, tzinfo=timezone.utc) + timedelta(hours=hh + 5, minutes=mm)


# ── What is a promise ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text, kind", [
    ("I'll email some options to you in 30-60mins", "email"),
    ("Ok, in this case I will be waiting for your feedback regarding the options that I will send to you shortly!", "email"),
    ("You can go to sleep. I will send you the price quotes via email.", "email"),
    ("Let me check some details and deals and get back to you via phone and share the quotes via email .", "call"),
    ("Give me half an hour and I will get back to you with the best deals.", "todo"),
    ("Okay, I will get back to you shortly with some flights quotes!", "email"),
    ("Allow me some time i will call you with the best option possible", "call"),
    ("Of course! I’ll send you three options within the hour.", "email"),
    ("We’ll call you Monday morning.", "call"),
    # The phrase decides when it can: an e-mail promise that mentions a call later is an e-mail.
    ("Sure . I will email those quotation with you and May I know what's the best time to call you", "email"),
])
def test_promises_are_caught_with_their_kind(text, kind):
    c = catch_promise(text, PHRASES)
    assert c is not None and c.kind == kind


@pytest.mark.parametrize("text", [
    "it okay if I will call you on your number (###)?",      # a question
    "Thank you, I’ll wait for the rules.",                    # no phrase
    "Are you flying tomorrow?",
    "Let me send you an email so you will have it",           # not in the list (an owner can add it)
    "The airline will send the e-ticket.",                    # not the agent
    "",
])
def test_not_a_promise(text):
    assert catch_promise(text, PHRASES) is None


def test_the_owner_s_list_is_the_only_list():
    assert catch_promise("Let me send you an email", ["let me send"]) is not None
    assert catch_promise("I'll send you the options", ["get back to you"]) is None
    assert catch_promise("I'll send you the options", []) is None


def test_normalize_reads_contractions_and_e_mail():
    assert normalize("I WILL  E-mail you") == "i'll email you"
    assert normalize("We’ll call") == "we'll call"


# ── Titles ─────────────────────────────────────────────────────────────────

def test_titles_use_the_agents_words():
    route = "New York → London"
    assert title_for("email", "I’ll send you three options within the hour.", "Michael", route) == \
        "Send Michael three options for New York → London"
    assert title_for("email", "I will send you the price quotes via email.", "Grace", None) == "Send Grace the price quotes"
    assert title_for("email", "No problem, I'll send you both and you let me know", "Ana", route) == "Send Ana what you promised"
    assert title_for("call", "I’ll call you in two hours to confirm the seats.", "Priya", None) == "Call Priya to confirm the seats"
    assert title_for("call", "I will call you with the best option", "Daniel", None) == "Call Daniel back"
    assert title_for("call", "Can you call me after 3?", "Kevin", None, client_asked=True) == "Call Kevin back"
    assert title_for("todo", "I will get back to you shortly", None, "Miami → Rome") == "Get back to the client about Miami → Rome"


# ── Due times ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, due, kind", [
    ("I'll send you three options within the hour.", SENT + timedelta(hours=1), "said"),
    ("I'll email some options to you in 30-60mins", SENT + timedelta(minutes=60), "said"),
    ("Give me half an hour and I will get back to you.", SENT + timedelta(minutes=30), "said"),
    ("I will have the best options in around 20-30 minutes", SENT + timedelta(minutes=30), "said"),
    ("I'll call you in two hours to confirm the seats.", SENT + timedelta(hours=2), "said"),
    # "After" a span is the span, never a clock: not 14:00 tomorrow, not 17:00.
    ("I'll call you after 2 hours.", SENT + timedelta(hours=2), "said"),
    ("I'll email you the options after 5 minutes.", SENT + timedelta(minutes=5), "said"),
    ("I'll call you back after an hour", SENT + timedelta(hours=1), "said"),
    ("I'll call you after 4 pm", ny(6, 16), "said"),
    ("I'll e-mail you the fare rules by 5", ny(6, 17), "said"),
    ("I'll call you at 5:30 pm", ny(6, 17, 30), "said"),
    ("I'll call you tomorrow at 9", ny(7, 9), "said"),
    ("We’ll call you Monday morning.", ny(9, 10), "said"),
    ("I'll e-mail you the fare rules today.", ny(6, 23), "end_of_day"),
    ("I'll email you shortly.", SENT + timedelta(hours=2), "default"),
    ("I'll get back to you.", SENT + timedelta(hours=2), "default"),
])
def test_due_from_the_words(text, due, kind):
    d = due_from(text, SENT, RULES)
    assert (d.at, d.kind) == (due, kind)


def test_a_clock_time_is_read_in_the_client_s_city_when_we_know_it():
    # "by 5" to a client flying from Chicago is 17:00 Chicago = 18:00 New York.
    d = due_from("I'll call you by 5", SENT, RULES, client_zone="America/Chicago")
    assert d.at == ny(6, 18)
    # Relative words need no zone.
    assert due_from("I'll call you within the hour", SENT, RULES, client_zone="America/Chicago").at == SENT + timedelta(hours=1)


def test_a_clock_time_already_past_is_tomorrow():
    late = ny(6, 18)  # 18:00: "by 5" has passed
    assert due_from("I'll call you by 5", late, RULES).at == ny(7, 17)


def test_the_default_due_follows_the_rules():
    for rule, due in [("1h", SENT + timedelta(hours=1)), ("2h", SENT + timedelta(hours=2)),
                      ("end_of_day", ny(6, 23)), ("next_morning", ny(7, 10))]:
        d = due_from("I'll email you shortly", SENT, rules_of({"default_due": rule}))
        assert (d.at, d.kind) == (due, "default"), rule
    # "End of the shift" after the working day ends is the next morning.
    assert due_from("I'll email you", ny(6, 23, 30), rules_of({"default_due": "end_of_day"})).at == ny(7, 10)
    # "Today" after the working day ends falls back to the default.
    assert due_from("I'll email you today", ny(6, 23, 30), RULES).kind == "default"


# ── Rules ──────────────────────────────────────────────────────────────────

def test_rules_keep_their_defaults_for_anything_unreadable():
    r = rules_of({"default_due": "never", "leave_shift": "x", "time_zone": "Mars/Base", "day_end": "25:00",
                  "phrases": ["I'll send", "I'll send", " ", 7, "x" * 80], "catch_promises": "yes"})
    assert r["default_due"] == DEFAULT_RULES["default_due"]
    assert r["leave_shift"] == "unowned" and r["time_zone"] == "America/New_York" and r["day_end"] == "23:00"
    assert r["phrases"] == ["I'll send"] and r["catch_promises"] is True


def test_try_a_sentence():
    out = try_sentence("I’ll e-mail you the fare rules by 5", RULES, SENT)
    assert out["makes_task"] and out["kind_label"] == "E-mail" and out["due_at"] == ny(6, 17).isoformat()
    assert try_sentence("Can I call you at 5?", RULES, SENT) == {"makes_task": False, "reason": "A question isn't a promise."}
    assert not try_sentence("Thanks!", RULES, SENT)["makes_task"]
    off = try_sentence("I'll call you", rules_of({"catch_promises": False}), SENT)
    assert off == {"makes_task": False, "reason": "Catching promises is off."}
