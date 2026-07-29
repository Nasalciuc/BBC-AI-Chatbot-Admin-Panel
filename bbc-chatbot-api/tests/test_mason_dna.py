"""Tests for the Mason DNA layer — persona reading, occasion, volunteered signals."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from unittest.mock import patch

import pytest

from app.ai.prompts import build_conversational_prompt, build_site_context
from app.db import supabase as db
from app.models.chat import VisitorInfo
from app.pipeline.entity_extractor import (
    derive_persona,
    destination_from_path,
    extract_entities,
)
from app.pipeline.validator import validate_response
from app.services import lead_service
from app.services.crm import build_crm_payload


def _sales_prompt(**kwargs) -> tuple[str, str]:
    return build_conversational_prompt(
        tunnel="sales", visitor=VisitorInfo(), **kwargs
    )


class TestPromptBlocks:
    """The methodology has to actually reach the model."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "READ THE CLIENT",
            "TIME_IS_MONEY",
            "DREAM MIRRORING",
            "PERMISSION FRAME",
            "under a minute of your time",
            "And what's the occasion",
            "probe flexibility ONCE",
            "VALUE SEEDS",
            "OPEN DOOR",
            "CALL PRIMING",
            "CLOSING LINE",
            "CONFIRMATION PHRASING",
            "Two quick things so your consultant can reach you",
            "not the first thing available, the right thing",
            "GRACEFUL EXIT",
            "VOLUNTEERED SIGNALS",
        ],
    )
    def test_block_renders(self, phrase):
        static, _dynamic = _sales_prompt()
        assert phrase in static

    def test_booking_for_someone_else_rule(self):
        static, _ = _sales_prompt()
        assert "classify from the TRAVELER, not the booker" in static

    def test_call_time_offer_rides_the_priming(self):
        static, _ = _sales_prompt()
        assert "if a particular time works best for the call" in static

    def test_graceful_exit_covers_both_contact_states(self):
        static, _ = _sales_prompt()
        assert "Contact already captured:" in static
        assert "Contact NOT captured:" in static

    def test_support_tunnel_untouched(self):
        static, _ = build_conversational_prompt(tunnel="support", visitor=VisitorInfo())
        assert "PERSONA PLAYBOOKS" not in static


class TestPersonaPlaybooks:
    """Each playbook must arrive whole — recognition through anti-patterns."""

    @pytest.mark.parametrize(
        "persona,recognize,fear,anti_pattern",
        [
            ("━━ TIME_IS_MONEY ━━", "work trip", "a missed meeting", "flowery language"),
            ("━━ EXPERIENCE_SEEKER ━━", "bucket list", "disappointing product", "leading with discounts"),
            ("━━ NEEDS_BASED ━━", "traveling with the baby", "the family split up", "vague promises"),
            ("━━ VALUE_DRIVEN ━━", "just need to get there", "hidden fees", "dodging price topics"),
        ],
    )
    def test_playbook_dimensions(self, persona, recognize, fear, anti_pattern):
        static, _ = _sales_prompt()
        assert persona in static
        for fragment in (recognize, fear, anti_pattern):
            assert fragment in static


class TestValidatorRegression:
    """The flexibility seed quotes a number — validation must not eat it."""

    def test_value_driven_seed_survives(self):
        seed = (
            "Purely hypothetically — if shifting one day saved around $1,000 or more, "
            "worth a look, or is the date locked?"
        )
        assert "$1,000" in validate_response(seed)

    def test_bare_price_still_scrubbed(self):
        assert "$3,200" not in validate_response("The fare is $3,200.")


class TestDerivePersona:
    @pytest.mark.parametrize(
        "occasion,text,expected",
        [
            ("business", None, ("time_is_money", "occasion")),
            ("celebration", None, ("experience_seeker", "occasion")),
            ("medical", None, ("needs_based", "occasion")),
            ("family", "visiting my parents next month", ("needs_based", "occasion_family")),
            ("family", "we go every few months", ("value_driven", "occasion_family_frequent")),
            (None, None, (None, None)),
        ],
    )
    def test_truth_table(self, occasion, text, expected):
        assert derive_persona(occasion, text) == expected

    def test_comparison_origin_is_a_prior(self):
        assert derive_persona(None, None, "kayak") == ("value_driven", "utm_prior")
        assert derive_persona(None, None, None, {"kayak_click_id": "abc"}) == (
            "value_driven",
            "utm_prior",
        )

    def test_occasion_overrides_the_prior(self):
        assert derive_persona("celebration", None, "kayak") == (
            "experience_seeker",
            "occasion",
        )

    def test_unknown_occasion_falls_through(self):
        assert derive_persona("something else entirely", None, None) == (None, None)


class TestDestinationFromPath:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/business-class-to-london", "London"),
            ("/flights/new-york-to-dubai", "Dubai"),
            ("/deals/jfk-lhr", "London"),
            ("/flight", None),
            ("", None),
            (None, None),
        ],
    )
    def test_paths(self, path, expected):
        assert destination_from_path(path) == expected


class TestVolunteeredExtraction:
    def test_occasion_keywords(self):
        assert extract_entities("It's our anniversary").occasion == "celebration"
        assert extract_entities("business trip to Frankfurt").occasion == "business"
        assert extract_entities("visiting my parents").occasion == "family"
        assert extract_entities("surgery abroad, medical trip").occasion == "medical"

    def test_no_occasion_from_a_plain_route(self):
        assert extract_entities("JFK to LHR on June 12").occasion is None

    def test_airline_love_and_avoid(self):
        e = extract_entities("I prefer Emirates and never again Spirit")
        assert e.airline_preference == "Emirates"
        assert e.airline_avoid == "Spirit"

    def test_airline_named_without_a_verb_is_not_a_signal(self):
        e = extract_entities("Is Emirates flying that route?")
        assert e.airline_preference is None
        assert e.airline_avoid is None

    def test_nonstop_only(self):
        assert extract_entities("non-stop only please").nonstop_only is True
        assert extract_entities("a connection is fine").nonstop_only is None

    def test_price_seen_and_budget(self):
        assert extract_entities("I saw $3,200 on another site").price_seen == "$3,200"
        assert extract_entities("my budget is around 5k").budget_hint == "$5,000"

    def test_best_call_time(self):
        assert extract_entities("call me after 2 pm").best_call_time == "after 2 pm"
        assert extract_entities("sounds good").best_call_time is None

    def test_booking_for_someone_else(self):
        assert extract_entities("I'm booking for my boss").booking_for == "boss"
        assert extract_entities("flying for my parents").booking_for == "parents"

    def test_booking_for_traveler_drives_persona(self):
        e = extract_entities("booking for my parents, visiting my parents in Rome")
        persona, _source = derive_persona(e.occasion, "booking for my parents")
        assert e.booking_for == "parents"
        assert persona == "needs_based"

    def test_date_flexibility(self):
        assert extract_entities("we have a day or two of flexibility").date_flexible is True
        assert extract_entities("dates are fixed").date_flexible is False
        assert extract_entities("JFK to LHR").date_flexible is None


class TestSiteContext:
    def test_renders_for_comparison_traffic(self):
        block = build_site_context(
            {
                "referrer": "https://www.kayak.com/flights",
                "utm_source": "kayak",
                "utm_medium": "cpc",
                "page_url": "https://buybusinessclass.com/business-class-to-london?gclid=zzz",
            }
        )
        assert "Came from: www.kayak.com via kayak/cpc" in block
        assert "Opened chat on page: /business-class-to-london" in block
        assert "comparing public fares" in block
        assert "interest in London" in block

    def test_absent_without_metadata(self):
        assert build_site_context(None) is None
        assert build_site_context({}) is None
        assert build_site_context({"site": "bbc"}) is None

    def test_never_leaks_click_ids_or_ga_id(self):
        metadata = {
            "utm_source": "kayak",
            "page_url": "https://buybusinessclass.com/deals?kclid=SECRET1",
            "kayak_click_id": "SECRET2",
            "google_analytics_client_id": "SECRET3",
        }
        static, dynamic = _sales_prompt(entities={"_metadata": metadata})
        prompt = static + dynamic
        assert "[SITE CONTEXT]" in dynamic
        for secret in ("SECRET1", "SECRET2", "SECRET3"):
            assert secret not in prompt

    def test_returning_visitor_line(self):
        block = build_site_context({"utm_source": "google", "returning_visitor": True})
        assert "Welcome back!" in block


class TestChatContextLine:
    def test_line_shape(self):
        line = lead_service.build_chat_context_line(
            {
                "occasion": "anniversary",
                "persona": "experience_seeker",
                "date_flexible": True,
                "must_haves": "window seats together",
                "airline_avoid": "Spirit",
                "nonstop_only": True,
                "price_seen": "$3,200",
                "best_call_time": "after 2 PM",
            }
        )
        assert line.startswith("Chat: ")
        for fragment in (
            "Occasion=anniversary",
            "Type=experience_seeker",
            "Must-have=window seats together",
            "Avoid=Spirit",
            "Seen: ~$3,200",
            "Call after 2 PM",
            "Dates=flexible",
            "Non-stop only",
        ):
            assert fragment in line

    def test_empty_signals_produce_nothing(self):
        assert lead_service.build_chat_context_line({}) is None
        assert lead_service.build_chat_context_line(None) is None

    def test_dream_outcome_derived_from_persona(self):
        signals = lead_service.signals_from_entities(
            {"persona": "needs_based", "occasion": "family"}
        )
        assert signals["dream_outcome"] == "comfort"

    def test_must_haves_are_pii_masked(self):
        signals = lead_service.signals_from_entities(
            {"must_haves": "call me on 415-555-0134 or me@example.com"}
        )
        assert "415-555-0134" not in signals["must_haves"]
        assert "me@example.com" not in signals["must_haves"]
        assert "[contact]" in signals["must_haves"]

    def test_notes_append_keeps_human_notes(self):
        existing = "Agent: client called, wants morning flights."
        updated = lead_service._append_note(existing, "Chat: Occasion=business")
        assert existing in updated
        assert "Chat: Occasion=business" in updated

    def test_notes_replace_only_our_previous_line(self):
        existing = "Agent: note.\nChat: Occasion=business"
        updated = lead_service._append_note(
            existing, "Chat: Occasion=business | Type=time_is_money"
        )
        assert updated.count("Chat: ") == 1
        assert "Agent: note." in updated

    def test_notes_unchanged_when_line_is_identical(self):
        existing = "Chat: Occasion=business"
        assert lead_service._append_note(existing, "Chat: Occasion=business") is None


# ── Fake Supabase, enough for the merge path ──────────────────────


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self.updates: list[dict] = []
        self.inserts: list[dict] = []


class _FakeQuery:
    def __init__(self, table):
        self._table = table
        self._op = None

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def update(self, payload):
        self._op = "update"
        self._table.updates.append(payload)
        return self

    def insert(self, payload):
        self._op = "insert"
        self._table.inserts.append(payload)
        return self

    def execute(self):
        if self._op == "select":
            return _Result(list(self._table.rows))
        if self._op == "insert":
            return _Result([{"id": "lead-1"}])
        return _Result([])


class _FakeClient:
    def __init__(self, lead_row):
        self.tables = {
            "leads": _FakeTable([lead_row] if lead_row else []),
            "conversations": _FakeTable([]),
        }

    def table(self, name):
        return _FakeQuery(self.tables[name])


async def _run_update(entities: dict, lead_row: dict) -> list[dict]:
    client = _FakeClient(lead_row)

    async def _run(fn):
        return fn()

    with patch.object(db, "get_client", return_value=client), patch.object(
        db, "_run_sync", _run
    ):
        await lead_service.update_lead_from_entities("conv-1", entities)
    return client.tables["leads"].updates


def _signal_update(updates: list[dict]) -> dict | None:
    return next((u for u in updates if "intent_signals" in u), None)


class TestIntentSignalsMerge:
    @pytest.mark.asyncio
    async def test_signals_merge_without_losing_other_keys(self):
        lead_row = {
            "id": "lead-1",
            "score": 40,
            "intent_signals": {"lead_source": "widget"},
            "notes": "Agent: called once.",
        }
        updates = await _run_update({"occasion": "celebration", "persona": "experience_seeker"}, lead_row)
        payload = _signal_update(updates)
        assert payload["intent_signals"]["lead_source"] == "widget"
        assert payload["intent_signals"]["occasion"] == "celebration"
        assert payload["intent_signals"]["dream_outcome"] == "experience"
        assert "Agent: called once." in payload["notes"]
        assert "Chat: Occasion=celebration" in payload["notes"]

    @pytest.mark.asyncio
    async def test_no_churn_when_nothing_changed(self):
        lead_row = {
            "id": "lead-1",
            "score": 40,
            "intent_signals": {
                "occasion": "business",
                "persona": "time_is_money",
                "dream_outcome": "rested",
            },
            "notes": "Chat: Occasion=business | Type=time_is_money",
        }
        updates = await _run_update({"occasion": "business", "persona": "time_is_money"}, lead_row)
        assert _signal_update(updates) is None

    @pytest.mark.asyncio
    async def test_turn_without_signals_writes_nothing(self):
        lead_row = {"id": "lead-1", "score": 40, "intent_signals": {}, "notes": None}
        updates = await _run_update({"_raw_message": "hello"}, lead_row)
        assert _signal_update(updates) is None


class TestCrmPayload:
    class _Visitor:
        name = "Jane"
        email = "jane@example.com"
        phone = "+13125550142"

    def _lead(self, signals=None):
        return {
            "origin_code": "JFK",
            "destination_code": "LHR",
            "departure_date": "2030-06-15",
            "passengers": 2,
            "cabin_class": "business",
            "intent_signals": signals,
        }

    def test_chat_context_rides_the_payload(self):
        payload = build_crm_payload(
            self._lead({"occasion": "anniversary", "persona": "experience_seeker"}),
            self._Visitor(),
        )
        assert payload["chat_context"].startswith("Chat: ")
        assert "Occasion=anniversary" in payload["chat_context"]

    def test_absent_without_signals(self):
        payload = build_crm_payload(self._lead(), self._Visitor())
        assert "chat_context" not in payload

    def test_returning_client_flagged_when_an_agent_is_on_file(self):
        payload = build_crm_payload(
            self._lead({"occasion": "business"}),
            self._Visitor(),
            conv_metadata={"engaged_agent_id": "agent-7"},
        )
        assert "Returning client — prior agent on file" in payload["chat_context"]
