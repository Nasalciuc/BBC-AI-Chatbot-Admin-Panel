"""A phone typed the local way, without its country code (chat #3296, 17 Nov 2026).

The client was in Australia and typed his mobile as "04…". The extractor did not take it, and
format_phone_international would have put "+1" before it. Numbers here are invented."""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.pipeline.callback_intent import detect_callback_request
from app.pipeline.entity_extractor import extract_entities
from app.security.metadata import allowed_widget_metadata
from app.services.crm import format_phone_international
from app.services.lead_service import _is_local_number
from app.services.phone_country import country_hint


@pytest.mark.parametrize("meta, iso", [
    ({"browser_lang": "en-AU"}, "AU"), ({"browser_lang": "pt-BR"}, "BR"), ({"tz": "Australia/Adelaide"}, "AU"),
    ({"tz": "America/Toronto"}, "CA"), ({"tz": "America/Chicago"}, "US"), ({"tz": "America/Argentina/Cordoba"}, "AR"),
    ({"browser_lang": "es-419", "tz": "America/Mexico_City"}, "MX"), ({"ip_country": "gb"}, "GB"),
    ({"browser_lang": "en", "tz": "Mars/Base"}, None), (None, None),
])
def test_where_the_client_probably_is(meta, iso):
    assert country_hint(meta) == iso


@pytest.mark.parametrize("raw, country, e164", [
    ("0412 345 678", "AU", "+61412345678"),
    ("07911 123456", "GB", "+447911123456"),
    ("9876543210", "IN", "+919876543210"),
    ("210-555-0100", "US", "+12105550100"),
    ("210-555-0100", None, "+12105550100"),          # the US default stays
    ("+61 412 345 678", "US", "+61412345678"),       # its own code wins
    ("0061 412 345 678", None, "+61412345678"),
    # The country's own length, not only 10 digits (CodeRabbit #270: Portugal's 9 became an Indian +91).
    ("912345678", "PT", "+351912345678"),
    ("612345678", "ES", "+34612345678"),
    ("91234567", "NO", "+4791234567"),
    ("11987654321", "BR", "+5511987654321"),
    # Its own code, typed without the +.
    ("351912345678", "PT", "+351912345678"),
    ("61412345678", "AU", "+61412345678"),
    ("919876543210", "IN", "+919876543210"),
    # A national number that starts like the code is still national: too short to carry it.
    ("9123456789", "IN", "+919123456789"),
    ("3912345678", "IT", "+393912345678"),
    # Italy dials its leading 0 from abroad too.
    ("02 1234 5678", "IT", "+390212345678"),
])
def test_a_number_is_placed_by_its_own_code_then_by_the_visits_country(raw, country, e164):
    assert format_phone_international(raw, country) == e164


def test_a_local_number_with_no_country_is_never_guessed():
    assert format_phone_international("0412 345 678") == ""
    assert format_phone_international("0412 345 678", "US") == ""  # +1 before a trunk 0 is no number


@pytest.mark.parametrize("text, phone", [
    ("my number 0412 345 678", "0412 345 678"), ("mobile: 07911 123456", "07911 123456"),
    ("call 210-555-0100", "210-555-0100"), ("leaving 05-10-2026", None), ("01.12.2026", None), ("ref 0012345678", None),
])
def test_the_extractor_takes_a_local_number_but_not_a_date_or_a_reference(text, phone):
    assert extract_entities(text).phone == phone


def test_a_local_number_is_one_with_a_trunk_zero():
    assert _is_local_number("0412 345 678")
    assert not _is_local_number("+61 412 345 678")
    assert not _is_local_number("0012345678")
    assert not _is_local_number("210 555 0100")


def test_a_callback_number_typed_the_local_way_is_placed_by_the_country():
    assert detect_callback_request("0412 345 678", "AU") == {"number": "+61412345678", "cue": "number"}


@pytest.mark.parametrize("text, phone, e164", [
    ("0412 345 678 2 passengers", "0412 345 678", "+61412345678"),
    ("+61 412 345 678 2 adults", "+61 412 345 678", "+61412345678"),
    ("+44 7700 900123 3", "+44 7700 900123", "+447700900123"),
])
def test_a_count_after_the_number_is_not_its_last_digit(text, phone, e164):
    """"0412 345 678 2 passengers": the 2 is the passengers (CodeRabbit #270), in the extractor and in
    the number a call-back or the WhatsApp button takes."""
    from app.pipeline.callback_intent import find_phone

    assert extract_entities(text).phone == phone
    assert find_phone(text, "AU") == e164


def test_the_widget_may_send_its_language_and_time_zone_and_nothing_else_like_them():
    assert allowed_widget_metadata({"browser_lang": "en-AU", "tz": "Australia/Adelaide"}) == \
        {"browser_lang": "en-AU", "tz": "Australia/Adelaide"}
    assert allowed_widget_metadata({"browser_lang": "<b>en</b>", "tz": "../etc/passwd"}) == {}


@pytest.mark.asyncio
async def test_a_number_typed_to_an_operator_is_placed_by_the_visits_country():
    from app.pipeline import operator_contact

    update = AsyncMock()
    with patch("app.db.supabase.get_conversation_simple",
               new=AsyncMock(return_value={"metadata": {"tz": "Australia/Adelaide"}})), \
         patch("app.services.lead_service.update_lead_from_entities", new=update):
        await operator_contact.record_contact("c-1", "you can reach me on 0412 345 678")
    found = update.await_args.args[1]
    assert found["phone"] == "0412 345 678" and found["_phone_country"] == "AU"
