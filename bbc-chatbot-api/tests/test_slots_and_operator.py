"""PR-7 — slot order, one question per turn, trip_type source, operator card."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.pipeline import slots
from app.pipeline.operator_card import build_details_payload


@pytest.fixture(autouse=True)
def _clean():
    flags._reset_for_tests()
    slots._reset_for_tests()
    yield
    slots._reset_for_tests()


def test_order_route_first():
    assert slots.next_slot({}) == "route"


def test_order_after_route_is_departure():
    lead = {"origin_code": "JFK", "destination_code": "LHR"}
    assert slots.next_slot(lead) == "departure_date"


def test_trip_type_default_does_not_satisfy():
    # round_trip is the NOT NULL default; without a client source it's still missing
    lead = {"origin_code": "JFK", "destination_code": "LHR", "departure_date": "2026-06-01",
            "trip_type": "round_trip", "passengers": 2}
    assert "trip_type" in slots.ordered_missing(lead)
    assert slots.next_slot(lead) == "trip_type"


def test_client_stated_trip_type_satisfies():
    lead = {"origin_code": "JFK", "destination_code": "LHR", "departure_date": "2026-06-01",
            "trip_type": "one_way", "trip_notes": {"trip_type_source": "client"}, "passengers": 2}
    assert slots.next_slot(lead) is None


def test_round_trip_client_needs_return():
    lead = {"origin_code": "JFK", "destination_code": "LHR", "departure_date": "2026-06-01",
            "trip_type": "round_trip", "trip_notes": {"trip_type_source": "client"}, "passengers": 2}
    assert slots.next_slot(lead) == "return_date"


def test_ensure_one_question_appends():
    out = slots.ensure_one_question("Got it, JFK to London.", "What date would you like to depart?")
    assert out.count("?") == 1 and out.endswith("depart?")
    assert slots.SLOTS_HEALTH["question_appended"] == 1


def test_ensure_one_question_trims_second():
    out = slots.ensure_one_question("Round trip? When would you like to fly back?", "x")
    assert out.count("?") == 1
    assert slots.SLOTS_HEALTH["second_question_trimmed"] == 1


def test_ensure_one_question_leaves_single_alone():
    txt = "When would you like to fly back?"
    assert slots.ensure_one_question(txt, "x") == txt


def test_operator_card_payload():
    lead = {"origin_code": "JFK", "destination_code": "LHR", "trip_notes": {"mobility": "wheelchair"}}
    p = build_details_payload(lead)
    assert p["type"] == "lead_details"
    assert p["route"] == "JFK → LHR"
    assert "departure_date" in p["missing"]
    assert p["suggested_question"]
    assert p["notes"]["mobility"] == "wheelchair"


def test_get_missing_fields_gate(monkeypatch):
    from app.models.lead import get_missing_fields
    lead = {"origin_code": "JFK", "destination_code": "LHR", "departure_date": "2026-06-01",
            "trip_type": "round_trip", "passengers": 2}
    # flag off: today's behaviour — trip_type default counts, so no return/one-way asked
    assert not any("return" in m for m in get_missing_fields(lead))
    monkeypatch.setenv("SLOT_POLICY_ENABLED", "true")
    assert any("return" in m for m in get_missing_fields(lead))
