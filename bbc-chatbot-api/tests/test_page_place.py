"""The place a page is about is not the client's trip until the client says it.

#3823 (27 Nov 2026): the quote card opened on a destination page; the page's city went onto the lead as the
destination, the prompt told the bot it was the client's own search and to treat it as known, and the bot
said it back to the client as their trip. It was not. (The phrasings below are invented; none is copied from
the chat.)
"""

import logging
import os
import sys
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.models.trip_context import TripContext
from app.pipeline import page_place
from app.services import lead_service, proactive

_ROUTE_PAGE = {"source": "route_page", "destination_code": "BCN", "destination_name": "Barcelona", "cabin": "business"}
_PAGE_URL = "https://buybusinessclass.com/business-class-flights/barcelona"


def _meta(**over) -> dict:
    return {"proactive_trip": dict(_ROUTE_PAGE), "page_url": _PAGE_URL, **over}


# ── The seed and the prompt line ─────────────────────────────


class TestTheSeed:
    def test_a_route_page_seeds_the_cabin_and_not_the_place(self):
        assert proactive._entities_from_trip(TripContext(**_ROUTE_PAGE)) == {"cabin_class": "business"}

    def test_a_route_page_with_both_ends_seeds_neither(self):
        trip = TripContext(source="route_page", origin_code="JFK", destination_code="LHR", cabin="first")
        assert proactive._entities_from_trip(trip) == {"cabin_class": "first"}

    @pytest.mark.parametrize("source", ["kayak", "search"])
    def test_a_search_the_visitor_ran_still_seeds_its_route(self, source):
        trip = TripContext(source=source, origin_code="JFK", destination_code="LHR")
        assert proactive._entities_from_trip(trip) == {"origin": "JFK", "destination": "LHR"}


def _lead(**over) -> dict:
    page = {**_ROUTE_PAGE, "confirmed": False, "landed": ["cabin_class"]}
    lead = {"id": "L1", "cabin_class": "business", "trip_notes": {"page_trip": page}}
    lead.update(over)
    return lead


class TestThePromptLine:
    def test_the_page_s_place_is_asked_about_not_stated(self):
        line = proactive.page_trip_line(_lead())
        assert line.startswith("From the page the chat opened on, not the client's words: Barcelona")
        assert "ask once" in line and "never state it as their route" in line
        assert "Treat it as known" not in line and "the client's own search" not in line

    @pytest.mark.parametrize("code", ["LIS", "BCN"])
    def test_gone_once_the_lead_holds_a_destination_the_client_gave(self, code):
        # Another place, or the page's own once the client said it: the seed never writes it, so it is theirs.
        assert proactive.page_trip_line(_lead(destination_code=code)) is None

    def test_an_origin_alone_keeps_the_question_about_the_place(self):
        assert proactive.page_trip_line(_lead(origin_code="ORD")) is not None

    def test_a_search_the_visitor_ran_is_still_known(self):
        page = {"source": "kayak", "origin_code": "JFK", "destination_code": "LHR", "confirmed": False,
                "landed": ["origin_code", "destination_code"]}
        lead = {"id": "L1", "origin_code": "JFK", "destination_code": "LHR", "trip_notes": {"page_trip": page}}
        assert "Treat it as known" in proactive.page_trip_line(lead)

    def test_a_page_trip_older_than_the_record_still_reads_as_seeded(self):
        # CodeRabbit #291: a page trip with no "landed" record and the page's code on the lead was "held"
        # before this change; it still is. A city named only in words still gives way (the older rule).
        lead = _lead(destination_code="BCN")
        del lead["trip_notes"]["page_trip"]["landed"]
        assert proactive.page_trip_line(lead) is not None
        assert proactive.page_trip_note(lead) is not None
        assert proactive.page_trip_line(_lead(destination_code="LIS")) is None
        words_only = {"source": "route_page", "destination_name": "Paris", "confirmed": False}
        assert proactive.page_trip_line({"id": "L1", "destination_code": "NRT",
                                         "trip_notes": {"page_trip": words_only}}) is None

    def test_a_place_an_older_seed_wrote_is_still_the_page_s(self):
        """Leads seeded before this change hold the page's code, and recorded writing it: the line still asks,
        and the consultant is still told the route was never confirmed."""
        lead = _lead(destination_code="BCN")
        lead["trip_notes"]["page_trip"]["landed"] = ["destination_code", "cabin_class"]
        assert proactive.page_trip_line(lead).startswith("From the page the chat opened on")
        assert proactive.page_trip_note(lead) == (
            "Route and dates from the page the client came from, not confirmed in chat")
        lead["destination_code"] = "LIS"
        assert proactive.page_trip_line(lead) is None and proactive.page_trip_note(lead) is None


def test_the_landing_city_line_asks_instead_of_assuming():
    from app.ai.prompts import build_site_context

    text = build_site_context({"page_url": _PAGE_URL})
    assert "is about Barcelona: a hint, not their trip yet" in text
    assert "ask once, naturally, whether Barcelona is where they want to go" in text


# ── The places a page names ─────────────────────────────────


class TestPagePlaces:
    def test_the_route_page_s_place_and_the_address_s_city(self):
        places = page_place.page_places(_meta())
        assert set(places) == {"BCN"} and "barcelona" in places["BCN"]

    def test_the_address_alone_names_its_city(self):
        assert set(page_place.page_places({"page_url": _PAGE_URL})) == {"BCN"}

    def test_a_search_the_visitor_ran_is_never_the_page_s(self):
        meta = _meta(proactive_trip={"source": "kayak", "origin_code": "JFK", "destination_code": "BCN"})
        assert page_place.page_places(meta) == {}

    def test_a_title_named_in_words_reads_as_its_code(self):
        meta = {"proactive_trip": {"source": "title", "origin_name": "Chicago", "destination_name": "Paris"}}
        assert set(page_place.page_places(meta)) == {"ORD", "CDG"}

    def test_the_query_string_names_no_page(self):
        meta = {"page_url": "https://buybusinessclass.com/?utm_term=business+class+to+paris"}
        assert page_place.page_places(meta) == {}

    @pytest.mark.parametrize("meta", [None, {}, "x", {"proactive_trip": "x"}, {"page_url": 5}])
    def test_nothing_readable_names_nothing(self, meta):
        assert page_place.page_places(meta) == {}

    def test_an_unreadable_trip_is_logged_and_the_address_still_read(self, caplog):
        with patch("app.models.trip_context.TripContext.__init__", side_effect=ValueError("bad")), \
             caplog.at_level(logging.WARNING):
            places = page_place.page_places(_meta())
        assert set(places) == {"BCN"}
        assert "stored trip unreadable (ValueError)" in caplog.text


# ── What the tool's reading may give the lead ────────────────


def _held(tool: dict, message: str, history=None, lead=None, meta=None) -> set:
    return page_place.held_back(tool, _meta() if meta is None else meta, history or [], message, lead or {})


class TestHeldBack:
    def test_the_page_s_place_is_held_until_the_client_says_it(self):
        assert _held({"origin": "ORD", "destination": "BCN"}, "I'd fly from Chicago") == {"destination"}

    @pytest.mark.parametrize("said", ["Barcelona please", "barcelona, spain", "to BCN in May"])
    def test_said_in_this_message(self, said):
        assert _held({"destination": "BCN"}, said) == set()

    def test_said_in_an_earlier_message(self):
        history = [{"role": "user", "content": "thinking of Barcelona"}, {"role": "ai", "content": "Lovely!"}]
        assert _held({"destination": "BCN"}, "from Chicago", history) == set()

    def test_words_of_ours_are_not_the_client_s(self):
        history = [{"role": "ai", "content": "Business class to Barcelona? Where would you fly from?"},
                   {"role": "user", "content": "from Chicago"}]
        assert _held({"destination": "BCN"}, "from Chicago", history) == {"destination"}

    @pytest.mark.parametrize("yes", ["yes", "Yes, from Chicago", "yeah exactly", "sure", "that's right", "Right.",
                                     "right, from Chicago"])
    def test_a_yes_to_our_question_that_named_it(self, yes):
        history = [{"role": "ai", "content": "Is Barcelona where you'd like to go?"}]
        assert _held({"destination": "BCN"}, yes, history) == set()

    @pytest.mark.parametrize("reply", ["ok", "okay, from Chicago", "no, Lisbon", "maybe", "right now I'm in Chicago"])
    def test_no_yes_no_place(self, reply):
        history = [{"role": "ai", "content": "Is Barcelona where you'd like to go?"}]
        assert _held({"destination": "BCN"}, reply, history) == {"destination"}

    def test_a_yes_on_an_earlier_turn_still_counts(self):
        # CodeRabbit #291: "yes" to the place, then the origin on the next turn, when the tool reads both.
        history = [{"role": "ai", "content": "Is Barcelona where you'd like to go?"},
                   {"role": "user", "content": "yes"},
                   {"role": "ai", "content": "Great. Where would you fly from?"},
                   {"role": "user", "content": "Chicago"}]
        assert _held({"origin": "ORD", "destination": "BCN"}, "Chicago", history) == set()

    def test_an_earlier_yes_to_something_else_does_not(self):
        history = [{"role": "ai", "content": "Shall I put a quote together?"},
                   {"role": "user", "content": "yes please"},
                   {"role": "ai", "content": "Where would you fly from?"},
                   {"role": "user", "content": "Chicago"}]
        assert _held({"origin": "ORD", "destination": "BCN"}, "Chicago", history) == {"destination"}

    def test_a_yes_answers_our_question_not_the_statement_beside_it(self):
        # CodeRabbit #291: the place in a statement, the question about something else.
        history = [{"role": "ai", "content": "Barcelona fares are available. Shall I send you a quote?"}]
        assert _held({"destination": "BCN"}, "Right.", history) == {"destination"}
        # Two questions, one of them about the place: the yes confirms it.
        history = [{"role": "ai", "content": "Business class to Barcelona? Where would you fly from?"}]
        assert _held({"destination": "BCN"}, "yes, from Chicago", history) == set()

    def test_a_yes_to_a_question_that_named_no_place(self):
        history = [{"role": "agent", "content": "Shall I send you a quote?"}]
        assert _held({"destination": "BCN"}, "yes", history) == {"destination"}

    def test_the_page_s_origin_too(self):
        meta = {"proactive_trip": {"source": "route_page", "origin_code": "JFK", "destination_code": "LHR"}}
        assert _held({"origin": "JFK", "destination": "LHR"}, "next spring", meta=meta) == {"origin", "destination"}

    def test_another_place_the_lead_already_holds_or_no_page_place_goes_through(self):
        assert _held({"destination": "LIS"}, "Lisbon, Portugal") == set()
        assert _held({"destination": "BCN"}, "from Chicago", lead={"destination_code": "BCN"}) == set()
        assert page_place.held_back({"destination": "BCN"}, {}, [], "from Chicago", {}) == set()

    def test_a_search_the_visitor_ran_goes_through(self):
        meta = _meta(proactive_trip={"source": "kayak", "origin_code": "JFK", "destination_code": "BCN"})
        assert _held({"destination": "BCN"}, "from Chicago", meta=meta) == set()


# ── In the pipeline ──────────────────────────────────────────


class _StopAfterMerge(Exception):
    """Raised by the patched validate_response: the pipeline reached STEP 7."""


async def _pipeline_writes(message: str, tool_entities: dict, history: list) -> list[dict]:
    """The entities the pipeline hands the lead writer, on a chat that opened on the page (_meta())."""
    from app import flags
    from app.models.chat import VisitorInfo
    from app.pipeline.generator import GeneratedResponse
    from app.pipeline.orchestrator import _pipeline

    cid = "aaaaaaaa-bbbb-cccc-dddd-000000003823"
    conv = {"id": cid, "mode": "ai", "status": "active", "tunnel": "sales", "metadata": _meta()}
    lead = {"id": "L1", "conversation_id": cid, "cabin_class": "business",
            "trip_notes": {"page_trip": {**_ROUTE_PAGE, "confirmed": False, "landed": ["cabin_class"]}}}
    update = AsyncMock(return_value=True)
    flags._reset_for_tests()
    with ExitStack() as stack:
        for target, value in (
            ("app.services.conversation_service.get_or_create_conversation", AsyncMock(return_value=conv)),
            ("app.services.conversation_service.add_message", AsyncMock(return_value={"id": "m1"})),
            ("app.pipeline.orchestrator.db.get_recent_messages", AsyncMock(return_value=history)),
            ("app.services.moderation.moderate_message", AsyncMock()),
            ("app.pipeline.orchestrator.db.update_conversation", AsyncMock()),
            ("app.pipeline.orchestrator.db.update_lead", AsyncMock()),
            ("app.pipeline.orchestrator.lead_service.get_or_create_lead", AsyncMock(return_value=lead)),
            ("app.pipeline.orchestrator.lead_service.update_lead_from_entities", update),
        ):
            stack.enter_context(patch(target, new=value))
        stack.enter_context(patch("app.pipeline.intent.claude_classify", return_value=(None, 0.0)))
        stack.enter_context(patch("app.pipeline.orchestrator.validate_response",
                                  side_effect=_StopAfterMerge("stop after the merge")))
        stack.enter_context(patch(
            "app.pipeline.orchestrator.generate_response",
            return_value=GeneratedResponse(text="Noted!", model_used="sonnet", tool_entities=tool_entities),
        ))
        with pytest.raises(_StopAfterMerge):
            await _pipeline(cid, message, "sales", VisitorInfo(name="Client"), None,
                            _persist_state={"ai_persisted": False})
    flags._reset_for_tests()
    return [dict(c.args[1]) for c in update.await_args_list]


_OPENING = [{"role": "ai", "content": "Looking at business class to Barcelona? I can price it for you."}]


@pytest.mark.asyncio
async def test_the_merge_keeps_the_page_s_place_off_the_lead_and_takes_the_rest():
    before = lead_service.LEAD_WRITE_HEALTH["page_place_held"]
    writes = await _pipeline_writes("I'm in Chicago", {"origin": "ORD", "destination": "BCN"},
                                    _OPENING + [{"role": "user", "content": "I'm in Chicago"}])
    assert writes and writes[-1].get("origin") == "ORD"
    assert all(w.get("destination") != "BCN" for w in writes)
    assert lead_service.LEAD_WRITE_HEALTH["page_place_held"] == before + 1


@pytest.mark.asyncio
async def test_the_merge_takes_the_page_s_place_once_the_client_says_yes_to_it():
    writes = await _pipeline_writes("yes, from Chicago", {"origin": "ORD", "destination": "BCN"},
                                    _OPENING + [{"role": "user", "content": "yes, from Chicago"}])
    assert writes[-1].get("destination") == "BCN" and writes[-1].get("origin") == "ORD"
