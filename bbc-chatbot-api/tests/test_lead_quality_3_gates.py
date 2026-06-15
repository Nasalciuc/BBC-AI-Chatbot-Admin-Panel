"""Tests for the 3-gate lead quality system.
Pins: intent context, extraction reconciliation, CRM gate, confirmation flow."""
from app.pipeline.intent import detect_intent, Intent
from app.pipeline.entity_extractor import extract_entities
from app.models.lead import get_missing_fields


# ── COMMIT 1: Intent ──

def test_digit_mid_collection_is_general():
    assert detect_intent("1", user_msg_count=5) != Intent.NEW_BOOKING


def test_digit_first_message_is_new_booking():
    assert detect_intent("1", user_msg_count=0) == Intent.NEW_BOOKING


def test_yes_mid_collection_is_not_new_booking():
    assert detect_intent("yes", user_msg_count=3) != Intent.NEW_BOOKING


def test_two_mid_collection_is_general():
    assert detect_intent("2", user_msg_count=4) != Intent.NEW_BOOKING


# ── COMMIT 2: Extraction ──

def test_pax_leading_digit_dot():
    e = extract_entities("2. Both over 65")
    assert e.passengers == 2


def test_pax_from_ai_text():
    e = extract_entities("Perfect — 2 adults, both over 65")
    assert e.passengers == 2


def test_no_false_positive_weeks():
    e = extract_entities("within 2 weeks of travel")
    assert e.passengers is None


def test_no_false_positive_options():
    e = extract_entities("2-3 business class options available")
    assert e.passengers is None


# ── COMMIT 3: CRM Gate ──

def test_crm_gate_blocks_without_passengers():
    lead = {
        "origin_code": "LAS",
        "destination_code": "ECP",
        "departure_date": "2026-07-05",
        "passengers": None,
        "cabin_class": "business",
    }
    conv = {
        "visitor_name": "Test",
        "visitor_email": "a@b.c",
        "visitor_phone": "+1234",
    }
    missing = get_missing_fields(lead, conv, for_crm=True)
    assert any("passenger" in m.lower() for m in missing)


def test_crm_gate_blocks_without_departure():
    lead = {
        "origin_code": "LAS",
        "destination_code": "ECP",
        "departure_date": None,
        "passengers": 2,
        "cabin_class": "business",
    }
    conv = {
        "visitor_name": "Test",
        "visitor_email": "a@b.c",
        "visitor_phone": "+1234",
    }
    missing = get_missing_fields(lead, conv, for_crm=True)
    assert any("departure" in m.lower() for m in missing)


def test_crm_gate_passes_complete():
    lead = {
        "origin_code": "LAS",
        "destination_code": "ECP",
        "departure_date": "2026-07-05",
        "passengers": 2,
        "cabin_class": "business",
        "trip_type": "one_way",
    }
    conv = {
        "visitor_name": "Test",
        "visitor_email": "a@b.c",
        "visitor_phone": "+1234",
    }
    missing = get_missing_fields(lead, conv, for_crm=True)
    assert missing == []


# ── COMMIT 4: Summary template ──

def test_build_summary_complete():
    from app.ai.templates import build_summary

    s = build_summary({
        "origin_code": "LAS",
        "destination_code": "ECP",
        "departure_date": "2026-07-05",
        "passengers": 1,
        "cabin_class": "business",
        "trip_type": "one_way",
    })
    assert s is not None
    assert "LAS" in s and "ECP" in s and "1 passenger" in s


def test_build_summary_incomplete():
    from app.ai.templates import build_summary

    s = build_summary({
        "origin_code": "LAS",
        "destination_code": "ECP",
        "departure_date": "2026-07-05",
        "passengers": None,
        "cabin_class": "business",
    })
    assert s is None
