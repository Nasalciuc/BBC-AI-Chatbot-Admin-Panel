"""What the client reads from Alex — the 16-17 Nov chats.

DATES     The tool's example "'june 15'=2026-06-15" taught the model this
          year's date for months already past. "June 14" became 2026-06-14,
          the check refused it, and Alex asked a client who had given the date
          for it again: "I notice the system needs your departure date".
LENGTH    A reply over 350 characters was cut at the last dot it found — the
          dot of an email or a number counted — and the closing question went
          with the cut: one ended "Text to <the number>."
KAYAK     Kayak is where the clients come from (owner, 17 Nov), not a
          competitor. A competitor sentence became the words "other services",
          glued into the reply.
KB        A tapped button ("Yes, check fares") and "ok" / "Text" each cost a
          knowledge-base search, and every one came back empty.

Names, routes and numbers below are invented.
"""

import asyncio
import os
import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.ai import claude  # noqa: E402
from app.db import qdrant  # noqa: E402
from app.models.extraction import (  # noqa: E402
    roll_past_dates_forward,
    salvage_tool_entities,
    validate_tool_entities,
)
from app.pipeline.validator import MAX_LENGTH, validate_response  # noqa: E402

NOV_17 = date(2026, 11, 17)
ROUTE = {"origin": "BOS", "destination": "LHR"}


# ── DATES ────────────────────────────────────────────────────

class TestTheYearOfADate:
    def test_the_tool_teaches_no_fixed_year(self):
        rules = claude.TRAVEL_TOOL["description"]
        assert "2026-06-15" not in rules
        assert "NEXT occurrence" in rules
        assert "asked in November, 'june 15' is June 15 of next year" in rules

    def test_a_month_already_past_is_next_year(self):
        out = roll_past_dates_forward({"departure_date": "2026-06-14", "return_date": "2026-06-25"}, today=NOV_17)
        assert out == {"departure_date": "2027-06-14", "return_date": "2027-06-25"}

    def test_only_the_wrong_year_moves(self):
        out = roll_past_dates_forward({"departure_date": "2026-12-20", "return_date": "2026-01-05"}, today=NOV_17)
        assert out == {"departure_date": "2026-12-20", "return_date": "2027-01-05"}

    def test_a_trip_that_straddles_today_moves_as_one(self):
        """CodeRabbit #263: "June 14 to June 25" said on June 20 — the moved
        departure must not land after the return."""
        june_20 = date(2026, 6, 20)
        out = roll_past_dates_forward({"departure_date": "2026-06-14", "return_date": "2026-06-25"}, today=june_20)
        assert out == {"departure_date": "2027-06-14", "return_date": "2027-06-25"}
        # A return already given in the right year stays as it is.
        out = roll_past_dates_forward({"departure_date": "2026-06-14", "return_date": "2027-06-25"}, today=june_20)
        assert out == {"departure_date": "2027-06-14", "return_date": "2027-06-25"}

    def test_a_back_to_front_pair_is_not_moved_twice(self):
        """CodeRabbit #263: both dates past and the return before the
        departure — each moves once, and the pair stays back to front."""
        out = roll_past_dates_forward({"departure_date": "2026-06-14", "return_date": "2026-06-10"}, today=NOV_17)
        assert out == {"departure_date": "2027-06-14", "return_date": "2027-06-10"}

    @freeze_time("2026-11-17")
    def test_the_check_still_refuses_a_back_to_front_pair(self):
        kept, err = validate_tool_entities({**ROUTE, "departure_date": "2026-06-14", "return_date": "2026-06-10"})
        assert kept is None and "return_date is before departure_date" in err
        assert salvage_tool_entities({"departure_date": "2026-06-14", "return_date": "2026-06-10"}) == {
            "departure_date": "2027-06-14",
        }

    @freeze_time("2026-06-20")
    def test_the_check_keeps_the_whole_trip(self):
        kept, err = validate_tool_entities({**ROUTE, "departure_date": "2026-06-14", "return_date": "2026-06-25"})
        assert err is None
        assert (kept["departure_date"], kept["return_date"]) == ("2027-06-14", "2027-06-25")
        assert salvage_tool_entities({"departure_date": "2026-06-14", "return_date": "2026-06-25"}) == {
            "departure_date": "2027-06-14", "return_date": "2027-06-25",
        }

    @pytest.mark.parametrize("value", [
        "2026-11-16",      # yesterday: the check's own one-day grace
        "2026-11-17",      # today
        "2025-06-14",      # more than a year ago: not a slip of the year, refused as before
        "24/7",            # not a date at all: the check says so
    ])
    def test_anything_else_is_left_for_the_check(self, value):
        assert roll_past_dates_forward({"departure_date": value}, today=NOV_17) == {"departure_date": value}

    def test_a_29_february_with_no_next_one_stays(self):
        out = roll_past_dates_forward({"departure_date": "2028-02-29"}, today=date(2029, 3, 1))
        assert out == {"departure_date": "2028-02-29"}

    def test_nothing_else_changes(self):
        raw = {**ROUTE, "passengers": 2}
        assert roll_past_dates_forward(raw, today=NOV_17) == raw
        assert roll_past_dates_forward(None, today=NOV_17) == {}

    @freeze_time("2026-11-17")
    def test_the_check_keeps_it(self):
        kept, err = validate_tool_entities({**ROUTE, "departure_date": "2026-06-14"})
        assert err is None
        assert kept["departure_date"] == "2027-06-14"
        assert salvage_tool_entities({"departure_date": "2026-06-14"}) == {"departure_date": "2027-06-14"}

    @freeze_time("2026-11-17")
    def test_the_tool_answers_with_the_date_it_kept_and_nothing_is_asked_again(self):
        sent = {**ROUTE, "departure_date": "2026-06-14"}
        kept, _err = validate_tool_entities(sent)
        answer = claude._tool_answer(sent, kept)
        assert "departure_date 2027-06-14" in answer
        assert "Not saved" not in answer

    @freeze_time("2026-11-17")
    def test_no_reprompt_is_spent_on_it(self):
        sent = {**ROUTE, "departure_date": "2026-06-14"}
        first = SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Boston to London, noted."),
                SimpleNamespace(type="tool_use", id="toolu_01", name="save_travel_details", input=sent),
            ],
            stop_reason="tool_use", usage=_usage(), id="msg_1",
        )
        rest = SimpleNamespace(content=[SimpleNamespace(type="text", text="How many travelers?")],
                               stop_reason="end_turn", usage=_usage(), id="msg_2")
        client = MagicMock()
        client.messages.create.side_effect = [first, rest]
        with patch.object(claude, "_get_client", return_value=client):
            _text, _cost, entities = claude._call_model_with_tools(
                "claude-opus-4-8", ("static", "dynamic"), "Boston to London June 14",
                max_tokens=450, temperature=0.6,
            )
        assert entities["departure_date"] == "2027-06-14"
        assert client.messages.create.call_count == 2  # the reply and its finish — no re-prompt


def _usage():
    return SimpleNamespace(input_tokens=10, output_tokens=5,
                           cache_creation_input_tokens=0, cache_read_input_tokens=0)


# ── The tool's answer ────────────────────────────────────────

class TestTheToolsAnswer:
    RULES = (
        "one or two short sentences",
        "at most one question",
        "never mentioning saving, a system or a tool",
    )

    def test_saved(self):
        for rule in self.RULES:
            assert rule in claude._TOOL_SAVED

    def test_partly_saved(self):
        answer = claude._tool_answer({**ROUTE, "return_date": "2026-01-01"}, dict(ROUTE))
        for rule in self.RULES:
            assert rule in answer
        assert answer.startswith("Saved as: origin BOS, destination LHR. Not saved, not valid: return_date.")
        assert answer.endswith(
            "Confirm only the details as saved above; ask the client again for what was not saved."
        )


# ── LENGTH ───────────────────────────────────────────────────

# The 17-Nov shape: a confirmation, the follow-up, and the question at the end.
LONG_WITH_QUESTION = (
    "That's an important detail, Ilse — so this is an open-jaw: out to London, home from Dublin "
    "on June 25. Let me update that for you. Here's the corrected picture, Ilse: Boston to London "
    "outbound, returning home from Dublin on June 25, round trip, 2 travelers, business or premium "
    "economy — whichever comes in lower. Text to +1.210.555.0100. Is that the right number?"
)


class TestTheCut:
    def test_the_closing_question_survives(self):
        assert len(LONG_WITH_QUESTION) > MAX_LENGTH
        out = validate_response(LONG_WITH_QUESTION)
        assert len(out) <= MAX_LENGTH
        assert out.endswith("Is that the right number?")
        # What the question is about stays next to it, and so does the opening.
        assert "Text to +1.210.555.0100. Is that the right number?" in out
        assert out.startswith("That's an important detail, Ilse")
        # The filler in the middle went first.
        assert "Let me update that for you." not in out

    def test_a_short_reply_is_untouched(self):
        msg = "Boston to London on June 14.\n\nHow many travelers?"
        assert validate_response(msg) == msg

    def test_the_paragraph_break_is_kept(self):
        msg = ("Everything's in place for Boston to London on June 14, returning June 25.\n\n"
               + "Your consultant will reach out with the private options first thing. " * 4
               + "\n\nWhat's the best email for them?")
        out = validate_response(msg)
        assert len(out) <= MAX_LENGTH
        assert out.startswith("Everything's in place for Boston to London on June 14, returning June 25.\n\n")
        assert out.endswith("\n\nWhat's the best email for them?")

    def test_never_inside_an_email(self):
        """The dot in an email is no sentence end."""
        msg = ("Your consultant will send the options to ann.lee@mail.example.com "
               + "and follow up with the details " * 12 + "today.")
        out = validate_response(msg)
        assert len(out) <= MAX_LENGTH
        assert "ann.lee@mail.example.com" in out
        assert out.endswith("…") and not out.endswith(" …")

    def test_never_inside_a_number(self):
        msg = ("Your consultant will text you on +1.210.555.0100 " + "with the private options " * 14 + "soon.")
        out = validate_response(msg)
        assert "+1.210.555.0100" in out
        assert len(out) <= MAX_LENGTH

    def test_a_reply_with_no_question_keeps_whole_sentences(self):
        sentences = ["This is sentence number {}.".format(i) for i in range(30)]
        out = validate_response(" ".join(sentences))
        assert len(out) <= MAX_LENGTH
        assert out.endswith(".")
        assert out.startswith("This is sentence number 0.")

    def test_an_early_space_still_beats_a_cut_inside_a_word(self):
        """CodeRabbit #263: never half a web address, even when the only
        space is near the start."""
        out = validate_response("See https://example.com/" + "a" * 400 + ".")
        assert out == "See…"

    def test_a_bold_closing_question_is_still_the_question(self):
        """CodeRabbit #263: the markdown goes before the cut, so the closing
        "**When do you fly?**" is kept — and kept without the stars."""
        out = validate_response("Welcome. " + "We can compare cabin layouts. " * 14 + "**When do you fly?**")
        assert len(out) <= MAX_LENGTH
        assert out.startswith("Welcome.")
        assert out.endswith("When do you fly?")
        assert "*" not in out

    def test_a_price_sentence_with_cents_is_replaced_whole(self):
        out = validate_response("That will be $2,800.00 per person. When do you fly?")
        assert "00 per person" not in out
        assert out.endswith("When do you fly?")


# ── KAYAK and the competitors ────────────────────────────────

class TestCompetitors:
    def test_kayak_is_where_the_clients_come_from(self):
        msg = "Since you came from the Kayak link, I have your route already. When do you fly?"
        assert validate_response(msg) == msg

    def test_a_competitor_sentence_is_dropped_not_replaced(self):
        out = validate_response("Happy to help. You could compare on Expedia, but our fares are private. Where from?")
        assert out == "Happy to help. Where from?"
        assert "other services" not in out

    def test_a_competitor_web_address_takes_its_sentence_whole(self):
        out = validate_response("Happy to help. You could check expedia.com, but our fares are private. Where from?")
        assert out == "Happy to help. Where from?"

    @pytest.mark.parametrize("before", [
        "Your flight leaves June 14 (local time.)",
        "You said “the 14th works.”",
    ])
    def test_a_sentence_ending_in_a_bracket_or_quote_is_kept(self, before):
        """CodeRabbit #263: the sentence before the competitor's ends in a
        bracket or a curly quote; only the competitor's sentence goes."""
        out = validate_response(f"{before} Expedia lists other fares. When do you return?")
        assert out == f"{before} When do you return?"

    def test_a_bold_competitor_sentence_keeps_the_question_after_it(self):
        """CodeRabbit #263, round 3: the competitor check runs before the
        Markdown is stripped; "option.**" ends the sentence, so the question
        after it stays and the reply is not the empty-reply fallback."""
        assert validate_response("**Expedia is an option.** Where from?") == "Where from?"

    def test_a_bold_sentence_before_the_competitor_is_kept(self):
        out = validate_response("**Great choice.** Expedia lists it too. Where from?")
        assert out == "Great choice. Where from?"


class TestAClosingQuestionWithAnEmoji:
    def test_the_question_and_its_emoji_are_kept(self):
        """CodeRabbit #263, round 3: an emoji after the closing question is
        part of it, not a sentence of its own that hides the question."""
        out = validate_response("We have options. " * 22 + "When do you fly? 😊")
        assert out.endswith("When do you fly? 😊")
        assert len(out) <= MAX_LENGTH

    def test_a_reply_that_fits_is_left_as_it_is(self):
        assert validate_response("We have options. When do you fly? 😊") == "We have options. When do you fly? 😊"


# ── KB ───────────────────────────────────────────────────────

class TestTheKnowledgeBaseSearch:
    @pytest.mark.parametrize("message", [
        "Yes, send me fares", "Get a fare quote",
        # The yes that names where the fares go (card_words.yes_label, 25 Nov), in the card's three languages.
        "Yes, email me the fares", "Yes, text me the fares", "Sí, envíenme las tarifas por correo",
        "Sí, envíenme las tarifas por SMS", "Oui, envoyez-moi les tarifs par e-mail", "Oui, envoyez-moi les tarifs par SMS",
        "Yes, check fares", "Check fares", "Change details", "I have a question", "Not now",
        "ok", "Okay!", "Text", "Yes please", "Thank you!", "no", "Email", "sounds good", "",
    ])
    def test_a_tap_or_an_acknowledgement_asks_nothing(self, message):
        assert qdrant.worth_searching(message) is False

    @pytest.mark.parametrize("message", [
        "What about baggage?", "wifi", "Is wifi included on Emirates?",
        "Can I change my dates later?", "yes, but what about lounge access?",
        "¿Cuánto cuesta en business?", "Сколько стоит бизнес-класс?",
    ])
    def test_a_question_is_searched(self, message):
        assert qdrant.worth_searching(message) is True

    def test_no_round_trip_for_a_tap(self):
        with patch.object(qdrant, "_search_sync", side_effect=AssertionError("searched")) as search:
            assert asyncio.run(qdrant.search_kb("Yes, send me fares")) == []
            assert asyncio.run(qdrant.search_kb("Yes, check fares")) == []
        search.assert_not_called()

    def test_a_question_still_reaches_qdrant(self):
        with patch.object(qdrant, "_search_sync", return_value=[{"id": 1}]) as search:
            assert asyncio.run(qdrant.search_kb("What about baggage?")) == [{"id": 1}]
        search.assert_called_once_with("What about baggage?", "sales", 3)
