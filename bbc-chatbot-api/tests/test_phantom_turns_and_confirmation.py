"""Six defects from one live conversation — Dunni, IAD→SIN, 18 Aug 17:15-17:22.

He asked for one seat. The lead reached a consultant saying seven people were
flying, because:

  17:16  the same apology went out three times in seconds, from a conversation
         no agent had ever been assigned to (D1)
  17:18  the bot spoke with no client message before it, invented "you plus
         some friends", and asked how many were travelling (D2)
  17:18  he answered that question — 7 — and the summary believed it
  17:20  "Going by myself. Ticket for me only." was not heard as one (D3)
  17:20  "Yes corewct" was not heard as yes (D4)
  17:20  the re-ask offered "e.g. 'returning Nov 17'" and he replied
         "I never said anything about November" (D5)
  17:22  "Dec23rd -1/3/27" came back as one date, twice (D6)
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoieCJ9.sig")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.pipeline.entity_extractor import extract_entities
from app.pipeline.orchestrator import (
    PIPELINE_HEALTH,
    _is_phantom_turn,
    is_confirmation,
)
from app.services import handoff


# ══════════════════════════════════════════════════════════════
# D2 — the bot speaks only in reply
# ══════════════════════════════════════════════════════════════

class TestPhantomTurn:
    def test_our_own_message_last_means_nothing_to_answer(self):
        history = [
            {"role": "user", "content": "22 or 23"},
            {"role": "ai", "content": "Traveling solo, or will someone join?"},
        ]
        assert _is_phantom_turn(history) is True

    def test_a_client_message_last_is_answered(self):
        history = [
            {"role": "ai", "content": "Traveling solo, or will someone join?"},
            {"role": "user", "content": "just me"},
        ]
        assert _is_phantom_turn(history) is False

    def test_an_agent_speaking_last_also_stops_us(self):
        """A human is mid-sentence with this client. We do not talk over them."""
        history = [{"role": "user", "content": "hi"}, {"role": "agent", "content": "on it"}]
        assert _is_phantom_turn(history) is True

    def test_a_fresh_conversation_is_not_a_phantom(self):
        assert _is_phantom_turn([]) is False
        assert _is_phantom_turn([{"role": "system", "content": "joined"}]) is False

    def test_system_noise_between_us_and_the_client_changes_nothing(self):
        """The three duplicate apologies from D1 sat between the turns."""
        history = [
            {"role": "user", "content": "Washington DC to Singapore"},
            {"role": "ai", "content": "Got it."},
            {"role": "system", "content": "Sorry for the wait — I'm here"},
        ]
        assert _is_phantom_turn(history) is True

    def test_the_guard_is_wired_only_to_the_re_entrant_path(self):
        """The normal path saves its user message a few lines earlier, so it can
        never be a phantom — and if that save ever fails we still owe the
        client an answer. Only FIX-C's fire-and-forget re-run is gated."""
        import inspect

        from app.pipeline import orchestrator

        src = inspect.getsource(orchestrator)
        assert "if skip_user_save and _is_phantom_turn(history):" in src
        assert "phantom_turn_blocked" in src

    def test_blocking_is_counted_for_health(self):
        assert "phantom_turns_blocked" in PIPELINE_HEALTH


# ══════════════════════════════════════════════════════════════
# D1 — one apology, and only when a human was really involved
# ══════════════════════════════════════════════════════════════

class _FakeConversations:
    """A table where the conditional UPDATE can succeed exactly once —
    the same guarantee Postgres gives us for a single statement."""

    def __init__(self):
        self.flag_set = False
        self.winners = 0

    def table(self, _name):
        return self

    def update(self, _payload):
        return self

    def eq(self, *_a):
        return self

    def is_(self, *_a):
        return self

    def execute(self):
        if self.flag_set:
            return MagicMock(data=[])
        self.flag_set = True
        self.winners += 1
        return MagicMock(data=[{"id": "c1"}])


class TestFallbackNoticeClaim:
    @pytest.mark.asyncio
    async def test_three_simultaneous_sweeps_produce_one_winner(self):
        """Three deadline sweeps fired within the same second on 18 Aug and
        the client got the apology three times. The decision belongs in the
        database, where it can only have one answer."""
        fake = _FakeConversations()
        with patch.object(handoff.db, "get_client", return_value=fake), \
             patch.object(handoff.db, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())):
            results = await asyncio.gather(
                *[handoff._claim_fallback_notice("c1", {}) for _ in range(3)]
            )
        assert sum(1 for r in results if r) == 1, "exactly one caller may speak"
        assert fake.winners == 1

    @pytest.mark.asyncio
    async def test_a_broken_claim_stays_quiet_rather_than_risking_a_repeat(self):
        with patch.object(handoff.db, "get_client", side_effect=RuntimeError("db down")):
            assert await handoff._claim_fallback_notice("c1", {}) is False

    @pytest.mark.asyncio
    async def test_the_claim_keeps_the_metadata_it_was_given(self):
        """It merges onto what the caller just wrote, so it cannot erase a
        flag some other writer set in between."""
        captured = {}

        class _Capture(_FakeConversations):
            def update(self, payload):
                captured.update(payload)
                return self

        with patch.object(handoff.db, "get_client", return_value=_Capture()), \
             patch.object(handoff.db, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())):
            await handoff._claim_fallback_notice("c1", {"summary_shown_at": "x"})

        assert captured["metadata"]["summary_shown_at"] == "x"
        assert captured["metadata"]["fallback_notice_at"]

    def test_the_apology_is_silent_when_no_agent_was_ever_assigned(self):
        """"Sorry for the wait — I'm here" says a human arrived. Dunni's
        conversation had no agent at all, so it was simply untrue."""
        import inspect

        src = inspect.getsource(handoff.fall_back_to_ai)
        assert "or not _agent" in src, "no agent ever assigned must force silence"
        assert "_claim_fallback_notice" in src


# ══════════════════════════════════════════════════════════════
# D4 — a client who opens with yes has said yes
# ══════════════════════════════════════════════════════════════

class TestConfirmation:
    @pytest.mark.parametrize("msg", ["yes corewct", "yes correct", "yes", "yeah thats right", "ok"])
    def test_these_are_confirmations(self, msg):
        assert is_confirmation(msg) is True

    @pytest.mark.parametrize("msg", ["urs", "no", "yes but make it one way", "yes but 2 passengers"])
    def test_these_are_not(self, msg):
        assert is_confirmation(msg) is False

    def test_urs_stays_a_re_ask(self):
        """Distance 2 from "yes" — the existing guard, deliberately kept."""
        assert is_confirmation("urs") is False

    def test_a_long_sentence_opening_with_si_is_not_agreement(self):
        """Spanish "si" starts plenty of sentences that are not a yes."""
        assert is_confirmation("si quieres cambiar la fecha de regreso") is False


# ══════════════════════════════════════════════════════════════
# D3 — "by myself" is a number
# ══════════════════════════════════════════════════════════════

class TestSoloPassenger:
    def test_the_sentence_dunni_repeated_three_times(self):
        text = "Going by myself. Ticket for me only. They bought their own ticket already"
        assert extract_entities(text).passengers == 1

    @pytest.mark.parametrize(
        "text", ["by myself", "just me", "only me", "just for me", "flying alone", "travelling alone"]
    )
    def test_every_way_of_saying_one(self, text):
        assert extract_entities(text).passengers == 1

    @pytest.mark.parametrize("text", ["just me and 3 friends", "just me and 2 others", "me and my wife"])
    def test_a_companion_in_the_same_breath_is_not_solo(self, text):
        assert extract_entities(text).passengers != 1

    def test_myself_in_an_unrelated_sentence_changes_nothing(self):
        assert extract_entities("I'll check myself and get back to you").passengers is None

    def test_an_explicit_number_still_wins(self):
        assert extract_entities("4 passengers").passengers == 4


# ══════════════════════════════════════════════════════════════
# D5 — never quote a date the client did not give us
# ══════════════════════════════════════════════════════════════

def test_the_second_reask_names_fields_not_invented_dates():
    from app.ai.templates import get_template

    text = get_template("summary_reask_2", "sales")
    assert text
    assert "Nov" not in text, "he answered: I never said anything about November"
    for month in ("Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Oct", "Dec"):
        assert month not in text, f"{month} is not his date either"
    for field in ("dates", "travellers", "cabin"):
        assert field in text


# ══════════════════════════════════════════════════════════════
# D6 — one range, two formats
# ══════════════════════════════════════════════════════════════

class TestMixedDateRange:
    @pytest.mark.parametrize(
        "text",
        [
            "Dec23rd -1/3/27",      # what Dunni actually typed
            "Dec23rd - 1/3/27",
            "12/23/26-1/3/27",      # no spaces — was silently wrong before
            "12/23/26 - 1/3/27",
            "Dec 23 - Jan 3",
            "Dec 23 to Jan 3",
        ],
    )
    def test_both_ends_of_the_range_survive(self, text):
        e = extract_entities(text)
        assert e.departure_date == "2026-12-23", text
        assert e.return_date == "2027-01-03", text

    def test_a_single_date_is_still_a_single_date(self):
        e = extract_entities("Dec 23")
        assert e.departure_date == "2026-12-23"
        assert e.return_date is None

    def test_the_same_month_range_still_works(self):
        e = extract_entities("March 15-22")
        assert e.departure_date and e.return_date
        assert e.departure_date != e.return_date

    def test_two_digit_years_are_this_century(self):
        e = extract_entities("1/3/27 - 1/10/27")
        assert e.departure_date == "2027-01-03"
        assert e.return_date == "2027-01-10"
