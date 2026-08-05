"""Open-door must-have capture — one-turn window, never a route restatement.

Proven pollution: Chat: Must-have=I want Savannah Georgia to Auckland, New
Zealand in October or November 2026 one passenger — the old window stayed
open forever after summary_shown_at and accepted any 4–200 char reply.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import pytest

from app.pipeline.entity_extractor import extract_entities
from app.pipeline.orchestrator import decide_open_door_reply


def _decide(msg, *, pending=True, confirmed=False, trip=False):
    return decide_open_door_reply(
        msg,
        pending=pending,
        confirmed_this_turn=confirmed,
        has_trip_entities=trip,
    )


class TestDecideOpenDoorReply:
    def test_route_restatement_is_not_captured(self):
        """JFK→Rome with passengers is a booking correction, not a must-have."""
        msg = "JFK to Rome in May, 2 passengers"
        extracted = extract_entities(msg)
        has_trip = bool(
            extracted.origin_code
            or extracted.destination_code
            or extracted.departure_date
            or extracted.passengers
        )
        assert has_trip is True
        reply, clear = _decide(msg, trip=has_trip)
        assert reply is None
        assert clear is True

    def test_savannah_auckland_pollution_is_blocked(self):
        """The exact production failure mode."""
        msg = (
            "I want Savannah Georgia to Auckland, New Zealand "
            "in October or November 2026 one passenger"
        )
        extracted = extract_entities(msg)
        has_trip = bool(
            extracted.origin_code
            or extracted.destination_code
            or extracted.departure_date
            or extracted.passengers
        )
        # Even if the extractor is partial on city names, passengers alone
        # is enough to classify this as a booking correction.
        assert has_trip is True or extracted.passengers == 1
        reply, clear = _decide(msg, trip=True)
        assert reply is None
        assert clear is True

    def test_genuine_must_have_is_captured_once(self):
        reply, clear = _decide("window seats together please")
        assert reply == "window seats together please"
        assert clear is True

        # Second message — flag already cleared → nothing.
        reply2, clear2 = _decide("and aisle for my wife", pending=False)
        assert reply2 is None
        assert clear2 is False

    @pytest.mark.parametrize(
        "no",
        ["no", "nope", "nothing", "none", "that's all", "thats all",
         "all good", "no thanks", "nothing else", "No thanks.", "NOPE!"],
    )
    def test_graceful_no_stores_nothing(self, no):
        reply, clear = _decide(no)
        assert reply is None
        assert clear is True

    def test_confirmation_turn_does_not_capture(self):
        reply, clear = _decide("yes", confirmed=True)
        assert reply is None
        assert clear is True

    def test_not_pending_is_a_no_op(self):
        reply, clear = _decide("window seats together please", pending=False)
        assert reply is None
        assert clear is False

    def test_too_short_or_too_long_clears_without_storing(self):
        reply, clear = _decide("ok")  # len 2
        assert reply is None and clear is True
        reply, clear = _decide("x" * 201)
        assert reply is None and clear is True

    def test_trip_entity_detection_for_may_passengers(self):
        """Entities still process on a route restatement — verify the signal."""
        extracted = extract_entities("JFK to Rome in May, 2 passengers")
        assert extracted.origin_code == "JFK" or extracted.destination_code
        assert extracted.passengers == 2


class TestSummarySetsPendingFlag:
    """The write site that opens the one-turn window (orchestrator step 7.5)."""

    def test_open_door_pending_literal_present_at_summary_write(self):
        # Guard against the flag being renamed away from the summary write.
        import inspect
        from app.pipeline import orchestrator as orch

        src = inspect.getsource(orch)
        assert 'open_door_pending"] = True' in src or "open_door_pending'] = True" in src
        assert "decide_open_door_reply" in src
