"""Tests for output validator — price, competitor, length, hallucination checks."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")

from app.pipeline.validator import (
    validate_response,
    EMPTY_FALLBACK,
    PRICE_REPLACEMENT,
    HALLUCINATION_REPLACEMENT,
)


class TestPriceDetection:
    """Exact prices should be replaced unless qualified."""

    def test_exact_price_replaced(self):
        result = validate_response("The flight costs $3,500 round trip.")
        assert "$3,500" not in result
        assert PRICE_REPLACEMENT in result

    def test_exact_price_with_cents(self):
        result = validate_response("That will be $2,800.00 per person.")
        assert "$2,800.00" not in result

    def test_mid_sentence_price_replaces_whole_sentence(self):
        result = validate_response(
            "Strong route. The fare is $3,500 today. When do you depart?"
        )
        assert "$3,500" not in result
        assert PRICE_REPLACEMENT in result
        assert "Strong route." in result
        assert "When do you depart?" in result
        # No dangling mid-sentence splice fragments
        assert "The fare is" not in result
        assert " today." not in result

    def test_qualified_around_price_survives(self):
        seed = (
            "Purely hypothetically — if shifting one day saved around $1,000 or more, "
            "worth a look, or is the date locked?"
        )
        assert "$1,000" in validate_response(seed)

    def test_clean_message_unchanged(self):
        msg = "Business class flights typically range from affordable to premium pricing."
        result = validate_response(msg)
        assert result == msg


class TestCompetitorNames:
    """Competitor names replace the containing sentence."""

    def test_kayak_replaced(self):
        result = validate_response("You can find it on Kayak too.")
        assert "kayak" not in result.lower()
        assert "other services" in result

    def test_expedia_replaced(self):
        result = validate_response("Expedia shows similar prices.")
        assert "expedia" not in result.lower()
        assert "other services" in result

    def test_skyscanner_replaced(self):
        result = validate_response("Try Skyscanner for comparison.")
        assert "skyscanner" not in result.lower()

    def test_multiple_competitors_same_sentence(self):
        result = validate_response("Check Kayak or Expedia for prices.")
        assert "kayak" not in result.lower()
        assert "expedia" not in result.lower()
        # One sentence → one replacement (not a mid-sentence double splice)
        assert result.count("other services") == 1

    def test_competitor_sentence_leaves_neighbors(self):
        result = validate_response(
            "Happy to help. You can find it on Kayak too. Where from?"
        )
        assert "kayak" not in result.lower()
        assert "Happy to help." in result
        assert "Where from?" in result


class TestLengthTruncation:
    """Responses over MAX_LENGTH chars should be truncated at sentence boundary."""

    def test_short_message_unchanged(self):
        msg = "This is a short message."
        assert validate_response(msg) == msg

    def test_long_message_truncated(self):
        # Build a message > MAX_LENGTH with sentence boundaries
        sentences = ["This is sentence number {}.".format(i) for i in range(30)]
        long_msg = " ".join(sentences)
        result = validate_response(long_msg)
        assert len(result) <= 510  # Allow small buffer for truncation logic
        assert result.endswith(".") or result.endswith("…")


class TestEmptyResponse:
    """Empty or whitespace responses should return fallback."""

    def test_empty_string(self):
        assert validate_response("") == EMPTY_FALLBACK

    def test_whitespace_only(self):
        assert validate_response("   \n  ") == EMPTY_FALLBACK

    def test_none_like(self):
        assert validate_response("") == EMPTY_FALLBACK


class TestHallucinationDetection:
    """Phrases that indicate hallucinated confirmations."""

    def test_booking_confirmation(self):
        result = validate_response("I can confirm your booking for tomorrow.")
        assert "I can confirm your booking" not in result
        assert HALLUCINATION_REPLACEMENT in result

    def test_reservation_number(self):
        result = validate_response("Your reservation number is ABC123.")
        assert "reservation number is" not in result

    def test_booking_confirmed(self):
        result = validate_response("Great news! Booking confirmed for your trip.")
        assert "booking confirmed" not in result.lower()
        assert "Great news!" in result
        assert HALLUCINATION_REPLACEMENT in result


class TestCombinedViolations:
    """Multiple violations in the same message."""

    def test_price_and_competitor(self):
        # Price runs first and replaces the whole sentence — competitor gone with it.
        msg = "The Kayak price is $3,500 for this route."
        result = validate_response(msg)
        assert "kayak" not in result.lower()
        assert "$3,500" not in result
        assert PRICE_REPLACEMENT in result

    def test_clean_passthrough(self):
        msg = "I'd be happy to help you find business class flights to London."
        assert validate_response(msg) == msg
