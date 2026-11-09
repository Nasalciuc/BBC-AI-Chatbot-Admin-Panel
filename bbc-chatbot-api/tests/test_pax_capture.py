"""PR-0 — passengers and cabin reach the lead on today's engine.

The 3 Nov reading: in 27 of 50 conversations the number of travelers was said
in the chat and never reached the lead. Two holes: the tool had no field for
it, and the regex did not know "2 seniors", "my husband and I", "3 tickets".
Both fixes sit behind a flag whose default is today's behaviour, and every
test below pins that default too (rule 5: the message that does NOT take the
new branch).
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.ai.claude import TRAVEL_TOOL, travel_tool
from app.models.chat import VisitorInfo
from app.models.extraction import validate_tool_entities
from app.pipeline.entity_extractor import extract_entities
from app.pipeline.generator import GeneratedResponse
from app.pipeline.orchestrator import _pipeline, _tool_pax_breakdown

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "pax_phrasings.json").read_text(encoding="utf-8")
)["phrasings"]
_KEYS = ("passengers", "children", "infants")


@pytest.fixture(autouse=True)
def _flags_clean(monkeypatch):
    flags._reset_for_tests()
    monkeypatch.delenv("PAX_TOOL_FIELDS", raising=False)
    monkeypatch.delenv("PAX_REGEX_V2", raising=False)
    yield
    flags._reset_for_tests()


def _counts(text):
    e = extract_entities(text)
    return {"passengers": e.passengers, "children": e.children_count, "infants": e.infant_count}


# ── regex ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("row", CORPUS, ids=[r["text"] for r in CORPUS])
def test_flag_off_extracts_exactly_what_master_did(row):
    assert _counts(row["text"]) == row["legacy"]


@pytest.mark.parametrize("row", CORPUS, ids=[r["text"] for r in CORPUS])
def test_flag_on_extracts_the_target(row, monkeypatch):
    monkeypatch.setenv("PAX_REGEX_V2", "true")
    assert _counts(row["text"]) == row["v2"]


def test_the_corpus_covers_the_lost_phrasings():
    lost = {r["text"] for r in CORPUS if r["legacy"]["passengers"] is None and r["v2"]["passengers"]}
    for phrase in ("2 seniors", "my husband and I", "3 tickets", "we are a family of four", "myself"):
        assert phrase in lost


@pytest.mark.parametrize("text", ["one way", "2-3 options", "seat 14A", "flight 2 of 3",
                                  "I'll book it myself", "2 maybe 3"])
def test_non_passenger_numbers_stay_untouched(text, monkeypatch):
    monkeypatch.setenv("PAX_REGEX_V2", "true")
    assert extract_entities(text).passengers is None


def test_a_total_with_an_age_is_not_double_counted(monkeypatch):
    monkeypatch.setenv("PAX_REGEX_V2", "true")
    assert extract_entities("3 of us, my son is 9 years old").passengers == 3


# ── the tool schema ──────────────────────────────────────────────────────

def test_flag_off_sends_todays_schema():
    assert travel_tool() is TRAVEL_TOOL
    assert set(TRAVEL_TOOL["input_schema"]["properties"]) == {
        "origin", "destination", "departure_date", "return_date", "trip_type",
    }


def test_flag_on_adds_travelers_and_cabin(monkeypatch):
    monkeypatch.setenv("PAX_TOOL_FIELDS", "true")
    tool = travel_tool()
    props = tool["input_schema"]["properties"]
    assert {"passengers", "children", "infants", "cabin_class"} <= set(props)
    assert props["cabin_class"]["enum"] == ["business", "first", "premium_economy"]
    assert "PASSENGERS" in tool["description"] and "never guess" in tool["description"]
    # The shared dict is never mutated: the next flag-off call is today's schema.
    assert "passengers" not in TRAVEL_TOOL["input_schema"]["properties"]


def test_validation_accepts_the_new_fields():
    clean, err = validate_tool_entities(
        {"passengers": 3, "children": 1, "infants": 1, "cabin_class": "premium_economy"}
    )
    assert err is None
    assert clean == {"passengers": 3, "children": 1, "infants": 1, "cabin_class": "premium_economy"}
    _, err = validate_tool_entities({"children": 12})
    assert err


# ── the merge (STEP 6.1) ─────────────────────────────────────────────────

def test_breakdown_maps_onto_the_lead_keys():
    updates, bad = _tool_pax_breakdown({"passengers": 3, "children": 1, "infants": 1}, {})
    assert (updates, bad) == ({"_children_count": 1, "_infant_count": 1}, False)


def test_fewer_travelers_than_minors_is_ignored():
    updates, bad = _tool_pax_breakdown({"passengers": 1, "children": 2}, {})
    assert (updates, bad) == ({}, True)


def test_regex_count_this_turn_wins():
    updates, _ = _tool_pax_breakdown({"passengers": 4, "children": 2}, {"_children_count": 1})
    assert updates == {}


def test_no_minors_no_updates():
    assert _tool_pax_breakdown({"passengers": 2}, {}) == ({}, False)


def test_adults_only_regex_total_takes_the_tools_total():
    # "my husband and I and three children": regex 2 (the adults), tool 5 / 3.
    updates, bad = _tool_pax_breakdown({"passengers": 5, "children": 3}, {"passengers": 2})
    assert (updates, bad) == ({"_children_count": 3, "passengers": 5}, False)


def test_saved_total_below_minors_without_a_tool_total_keeps_nothing():
    updates, bad = _tool_pax_breakdown({"children": 3}, {"passengers": 2})
    assert (updates, bad) == ({}, True)


def test_the_leads_stored_total_must_hold_the_minors_too():
    # CodeRabbit #226: a lead saved with 2 travelers, the tool says 3 children
    # and no total this turn: 2 travelers with 3 children was saved.
    assert _tool_pax_breakdown({"children": 3}, {}, stored_total=2) == ({}, True)
    assert _tool_pax_breakdown({"children": 1}, {}, stored_total=2) == ({"_children_count": 1}, False)
    # A total said this turn still comes first.
    assert _tool_pax_breakdown({"passengers": 5, "children": 3}, {}, stored_total=2) == ({"_children_count": 3}, False)


def test_a_regex_total_that_holds_the_minors_is_kept():
    updates, bad = _tool_pax_breakdown({"passengers": 4, "children": 2}, {"passengers": 4})
    assert (updates, bad) == ({"_children_count": 2}, False)


def test_an_explicit_zero_reaches_the_lead():
    # A correction to "no children" must clear a stale count, not be skipped.
    updates, bad = _tool_pax_breakdown({"passengers": 2, "children": 0}, {"_children_count": None})
    assert (updates, bad) == ({"_children_count": 0}, False)


_CID = "aaaaaaaa-bbbb-cccc-dddd-000000000000"
_CONV = {"id": _CID, "mode": "ai", "status": "active", "tunnel": "sales", "metadata": {}}
_LEAD = {"id": "lead-pax", "conversation_id": _CID, "origin_code": "JFK",
         "destination_code": "LHR", "trip_type": "round_trip", "notes": ""}


class _StopAfterMerge(Exception):
    """Raised by the patched validate_response: the pipeline reached STEP 7."""


async def _merged_entities(message, tool_entities):
    """Run the pipeline up to STEP 6.1 and return what it hands the lead.

    Only the deliberate stop is swallowed: a failure before STEP 7 fails the
    test instead of passing it with an empty result."""
    from contextlib import ExitStack

    update = AsyncMock()
    with ExitStack() as stack:
        for target, value in (
            ("app.services.conversation_service.get_or_create_conversation", AsyncMock(return_value=dict(_CONV))),
            ("app.services.conversation_service.add_message", AsyncMock(return_value={"id": "m1"})),
            ("app.pipeline.orchestrator.db.get_recent_messages", AsyncMock(return_value=[])),
            ("app.services.moderation.moderate_message", AsyncMock()),
            ("app.pipeline.orchestrator.db.update_conversation", AsyncMock()),
            ("app.pipeline.orchestrator.db.update_lead", AsyncMock()),
            ("app.pipeline.orchestrator.lead_service.get_or_create_lead", AsyncMock(return_value=dict(_LEAD))),
            ("app.pipeline.orchestrator.lead_service.update_lead_from_entities", update),
        ):
            stack.enter_context(patch(target, new=value))
        # Offline: no classifier call; stop at STEP 7, right after the merge.
        stack.enter_context(patch("app.pipeline.intent.claude_classify", return_value=(None, 0.0)))
        stack.enter_context(patch(
            "app.pipeline.orchestrator.validate_response",
            side_effect=_StopAfterMerge("stop after the merge"),
        ))
        stack.enter_context(patch(
            "app.pipeline.orchestrator.generate_response",
            return_value=GeneratedResponse(text="Got it.", model_used="sonnet", tool_entities=tool_entities),
        ))
        with pytest.raises(_StopAfterMerge):
            await _pipeline(_CID, message, "sales", VisitorInfo(name="Pax"), None,
                            _persist_state={"ai_persisted": False})
    calls = [c.args[1] for c in update.await_args_list]
    return calls[-1] if calls else {}


@pytest.mark.asyncio
async def test_tool_passengers_and_cabin_reach_the_lead(monkeypatch):
    monkeypatch.setenv("PAX_TOOL_FIELDS", "true")
    ents = await _merged_entities(
        "We would like first class please",
        {"passengers": 3, "children": 1, "infants": 1, "cabin_class": "first"},
    )
    assert ents.get("passengers") == 3
    assert ents.get("cabin_class") == "first"
    assert ents.get("_children_count") == 1 and ents.get("_infant_count") == 1


@pytest.mark.asyncio
async def test_flag_off_ignores_the_breakdown(monkeypatch):
    ents = await _merged_entities(
        "We would like first class please",
        {"passengers": 3, "children": 1, "infants": 1},
    )
    assert ents.get("passengers") == 3, "STEP 6.1 ran: passengers was always accepted by the merge"
    assert not ents.get("_children_count")
    assert not ents.get("_infant_count")


@pytest.mark.asyncio
async def test_inconsistent_triple_keeps_nothing(monkeypatch):
    monkeypatch.setenv("PAX_TOOL_FIELDS", "true")
    ents = await _merged_entities("hello there", {"passengers": 1, "children": 2})
    assert ents.get("passengers") in (None, 0)
    assert not ents.get("_children_count")


@pytest.mark.asyncio
async def test_adults_only_regex_and_a_consistent_tool_triple(monkeypatch):
    monkeypatch.setenv("PAX_TOOL_FIELDS", "true")
    monkeypatch.setenv("PAX_REGEX_V2", "true")
    ents = await _merged_entities("my husband and I", {"passengers": 5, "children": 3})
    assert ents.get("passengers") == 5 and ents.get("_children_count") == 3


@pytest.mark.asyncio
async def test_a_tool_zero_clears_the_lead_count():
    from app.services import lead_service

    client = MagicMock()
    sel = client.table.return_value.select.return_value.eq.return_value.limit.return_value
    sel.execute.return_value = MagicMock(data=[{"id": "lead-pax", "children_count": 2, "infant_count": 1}])

    async def _run(fn, **kw):
        return fn()

    with patch("app.db.supabase.get_client", return_value=client), \
         patch("app.db.supabase._run_sync", new=_run):
        await lead_service.update_lead_from_entities(_CID, {"_children_count": 0, "_infant_count": None})
    payloads = [c.args[0] for c in client.table.return_value.update.call_args_list]
    lead = next(p for p in payloads if "children_count" in p)
    assert lead["children_count"] == 0
    assert "infant_count" not in lead
