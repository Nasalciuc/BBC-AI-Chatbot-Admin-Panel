"""Tests for intent detection — regex patterns only (no Claude calls)."""

import sys
import os
from unittest.mock import patch
import pytest

# Ensure project root is on sys.path so imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub out settings before any app import
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")

# Mock Claude to prevent API calls — always return None (forces fallback to GENERAL_QUESTION)
with patch("app.ai.claude.classify_intent", return_value=None):
    from app.pipeline.intent import detect_intent, Intent, INTENT_PATTERNS


# Pytest fixture to mock Claude for all tests
@pytest.fixture(autouse=True)
def mock_claude():
    """Mock Claude classify_intent to return None for all tests."""
    with patch("app.ai.claude.classify_intent", return_value=None):
        yield


# ── Regex pattern tests ───────────────────────────────────────

class TestIntentRegex:
    """Test that each regex pattern matches expected messages."""

    def test_new_booking(self):
        assert detect_intent("I want to book a flight") == Intent.NEW_BOOKING
        assert detect_intent("Looking for a flight to London") == Intent.NEW_BOOKING
        assert detect_intent("Help me fly business class") == Intent.NEW_BOOKING
        assert detect_intent("Need tickets to Tokyo") == Intent.NEW_BOOKING
        assert detect_intent("Planning a trip to Paris") == Intent.NEW_BOOKING

    def test_price_inquiry(self):
        assert detect_intent("How much does it cost?") == Intent.PRICE_INQUIRY
        assert detect_intent("What's the fare for NYC to LHR?") == Intent.PRICE_INQUIRY
        assert detect_intent("Is it expensive?") == Intent.PRICE_INQUIRY
        assert detect_intent("What are your rates?") == Intent.PRICE_INQUIRY

    def test_booking_change(self):
        assert detect_intent("Can I change my booking?") == Intent.BOOKING_CHANGE
        assert detect_intent("I need to cancel my flight") == Intent.BOOKING_CHANGE
        assert detect_intent("How do I modify my reservation?") == Intent.BOOKING_CHANGE
        assert detect_intent("I want a refund") == Intent.BOOKING_CHANGE

    def test_baggage_info(self):
        assert detect_intent("What about baggage allowance?") == Intent.BAGGAGE_INFO
        assert detect_intent("How many bags can I bring?") == Intent.BAGGAGE_INFO
        assert detect_intent("Carry-on size limit?") == Intent.BAGGAGE_INFO
        assert detect_intent("Checked luggage weight?") == Intent.BAGGAGE_INFO

    def test_greeting(self):
        assert detect_intent("Hi there") == Intent.GREETING
        assert detect_intent("Hello") == Intent.GREETING
        assert detect_intent("Hey!") == Intent.GREETING
        assert detect_intent("Good morning") == Intent.GREETING
        assert detect_intent("Good afternoon everyone") == Intent.GREETING

    def test_closing(self):
        assert detect_intent("Thank you so much!") == Intent.CLOSING
        assert detect_intent("Bye for now") == Intent.CLOSING
        assert detect_intent("Goodbye") == Intent.CLOSING
        assert detect_intent("That's all I needed") == Intent.CLOSING

    def test_talk_to_agent(self):
        assert detect_intent("Talk to a person") == Intent.TALK_TO_AGENT
        assert detect_intent("Can I speak to an agent?") == Intent.TALK_TO_AGENT
        assert detect_intent("Connect me with someone") == Intent.TALK_TO_AGENT
        assert detect_intent("I want to talk to a human") == Intent.TALK_TO_AGENT

    @pytest.mark.parametrize("text", [
        "yes, pass it on",
        "ok hand it over",
        "Sure, pass it along please",
        "please send my request to a consultant",
        "pass this to one of your consultants",
        "don't worry, pass it on",
        # A refusal in another clause refuses that clause only (CodeRabbit #255).
        "Don't send emails; pass my request to a consultant for this flight.",
        "No need to email me, just pass it on",
    ])
    def test_the_answer_to_the_real_person_offer_reaches_a_person(self, text):
        """Alex offers once to pass the request to a consultant (16 Nov); the
        client's "pass it on" must take the handoff path, live or queued."""
        assert detect_intent(text, use_llm=False) == Intent.TALK_TO_AGENT

    @pytest.mark.parametrize("text", [
        "can you send it over by email?",
        "I'll pass on that one",
        "can you pass this to my wife",
        "send me the options",
        # A refused transfer is a no (CodeRabbit #255).
        "please don't pass it on",
        "Please don’t pass it on yet",
        "do not hand it over",
        "never send my request to a consultant",
        "no need to pass it on",
        "I don't want you to pass it on",
        "don't send or pass it on",
    ])
    def test_ordinary_words_do_not_call_a_person(self, text):
        assert detect_intent(text, use_llm=False) != Intent.TALK_TO_AGENT

    def test_route_info(self):
        assert detect_intent("What airlines fly direct?") == Intent.ROUTE_INFO
        assert detect_intent("Is there a nonstop option?") == Intent.ROUTE_INFO
        assert detect_intent("How long is the duration?") == Intent.ROUTE_INFO

    def test_case_insensitivity(self):
        assert detect_intent("BOOK A FLIGHT") == Intent.NEW_BOOKING
        assert detect_intent("Cancel My Booking") == Intent.BOOKING_CHANGE
        assert detect_intent("HELLO") == Intent.GREETING


class TestIntentMetadata:
    """Test quick_reply_intent override from metadata."""

    def test_quick_reply_override(self):
        meta = {"quick_reply_intent": "new_booking"}
        assert detect_intent("random text", meta) == Intent.NEW_BOOKING

    def test_quick_reply_uppercase(self):
        meta = {"quick_reply_intent": "TALK_TO_AGENT"}
        assert detect_intent("random text", meta) == Intent.TALK_TO_AGENT

    def test_quick_reply_invalid_falls_through(self):
        meta = {"quick_reply_intent": "invalid_intent_xyz"}
        # Should fall through to regex / default
        result = detect_intent("Hello", meta)
        assert result == Intent.GREETING  # Regex catches it


class TestIntentPatternOrder:
    """Test that more specific patterns win over generic ones."""

    def test_agent_wins_over_booking(self):
        # "talk to" + "person" should match TALK_TO_AGENT before NEW_BOOKING ("trip")
        assert detect_intent("Talk to a person about my trip") == Intent.TALK_TO_AGENT

    def test_booking_wins_over_route(self):
        # "book" should match NEW_BOOKING before ROUTE_INFO
        assert detect_intent("I want to book a direct flight") == Intent.NEW_BOOKING
