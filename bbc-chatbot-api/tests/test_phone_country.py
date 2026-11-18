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


# ── A national number with no trunk 0, read with the visit's country (CodeRabbit #270, round 2) ──

@pytest.mark.parametrize("text, country, e164", [
    ("912345678", "PT", "+351912345678"),
    ("912 345 678", "PT", "+351912345678"),
    ("you can reach me at 612345678 thanks", "ES", "+34612345678"),
    ("41234567", "NO", "+4741234567"),
    ("account, phone 912345678", "PT", "+351912345678"),  # a phone word between them: a number again
])
def test_a_national_number_without_a_trunk_zero_is_placed_by_the_visits_country(text, country, e164):
    # Taken as typed, like every number the extractor finds; placed where it is saved, with the same country.
    phone = extract_entities(text, country=country).phone
    assert phone and format_phone_international(phone, country) == e164


@pytest.mark.parametrize("text, country", [
    ("912345678", None),                 # no country: never guessed
    ("912345678", "US"),                 # nine digits are no number there
    ("ref 912345678", "PT"), ("booking number is 912345678", "PT"), ("PNR: 912345678", "PT"),
    ("my id 612345678", "ES"),
    ("20261201", "NO"), ("01 12 2026", "DK"), ("12122024", "NO"), ("leaving 05.10.2026", "PT"),
    # An amount is no phone either.
    ("I saw 12.500.000 kr", "NO"), ("NOK 12500000", "NO"), ("€ 912 345 678", "PT"),
])
def test_a_date_a_reference_or_no_country_is_no_national_number(text, country):
    assert extract_entities(text, country=country).phone is None
    assert detect_callback_request(text, country) is None


def test_a_bare_national_number_is_a_number_to_reach():
    assert detect_callback_request("912345678", "PT") == {"number": "+351912345678", "cue": "number"}


@pytest.mark.parametrize("text", ["ref 2105550100", "order #210-555-0100", "booking 2105550100"])
def test_a_reference_is_never_a_number_to_call(text):
    assert extract_entities(text).phone is None
    assert detect_callback_request(text, "US") is None


def test_a_number_after_a_reference_and_a_phone_word_is_still_taken():
    assert extract_entities("a ticket question, call 2105550100").phone == "2105550100"


def test_a_number_its_country_reads_two_ways_is_kept_to_check_not_guessed():
    """"4930123456" in Germany: +49 30123456 typed without the +, or the national 0493 0123456."""
    from app.services.crm import phone_is_ambiguous

    assert format_phone_international("4930123456", "DE") == ""
    assert phone_is_ambiguous("4930123456", "DE")
    for typed in ("+4930123456", "004930123456", "030 123456"):
        assert not phone_is_ambiguous(typed, "DE")
    assert format_phone_international("+49 30 123456", "DE") == "+4930123456"
    assert not phone_is_ambiguous("912345678", "PT") and not phone_is_ambiguous("2105550100", "US")


def test_a_date_is_never_the_number_to_call_back():
    from app.pipeline.callback_intent import find_phone

    assert find_phone("call me on 01.12.2026 at 2105550100", "US") == "+12105550100"
    assert find_phone("call me on 20261201", "NO") is None
    out = detect_callback_request("call me on 01.12.2026", "AU")
    assert out["number"] is None and "raw" not in out


def test_a_call_back_number_that_cannot_be_placed_is_kept_as_typed():
    out = detect_callback_request("call me 4930123456", "DE")
    assert out == {"number": None, "cue": "words", "raw": "4930123456"}
    out = detect_callback_request("please call me on 0412 345 678", None)
    assert out["number"] is None and out["raw"] == "0412 345 678"


@pytest.mark.asyncio
async def test_a_national_number_typed_to_an_operator_is_placed_by_the_visits_country():
    from app.pipeline import operator_contact

    update = AsyncMock()
    with patch("app.db.supabase.get_conversation_simple",
               new=AsyncMock(return_value={"metadata": {"tz": "Europe/Lisbon"}})), \
         patch("app.services.lead_service.update_lead_from_entities", new=update):
        await operator_contact.record_contact("c-1", "912345678")
    found = update.await_args.args[1]
    assert found["phone"] == "912345678" and found["_phone_country"] == "PT"
    assert format_phone_international(found["phone"], found["_phone_country"]) == "+351912345678"


@pytest.mark.asyncio
async def test_a_national_number_its_country_reads_two_ways_is_kept_to_check():
    """"49301234" typed to an operator from Germany: +49 301234, or the national 0493 01234. Kept as typed
    for the agent to check, never guessed (CodeRabbit #270, round 3)."""
    from app.pipeline import operator_contact

    update = AsyncMock()
    with patch("app.db.supabase.get_conversation_simple",
               new=AsyncMock(return_value={"metadata": {"tz": "Europe/Berlin"}})), \
         patch("app.services.lead_service.update_lead_from_entities", new=update):
        await operator_contact.record_contact("c-1", "49301234")
    found = update.await_args.args[1]
    assert found["phone"] == "49301234" and found["_phone_country"] == "DE"
    kept, presence = await _lead_update(found, presence_ok=True)
    assert kept is True and presence.await_args.args[1] == {"phone_to_check": "49301234"}


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["20261201", "booking 912345678", "ref: 912345678", "leaving 05.10.2026"])
async def test_a_date_or_a_reference_typed_to_an_operator_saves_no_phone(message):
    from app.pipeline import operator_contact

    update = AsyncMock()
    with patch("app.db.supabase.get_conversation_simple",
               new=AsyncMock(return_value={"metadata": {"tz": "Europe/Lisbon"}})), \
         patch("app.services.lead_service.update_lead_from_entities", new=update):
        await operator_contact.record_contact("c-1", message)
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_operator_message_without_digits_reads_no_country():
    from app.pipeline import operator_contact

    read = AsyncMock(return_value={"metadata": {"tz": "Europe/Lisbon"}})
    update = AsyncMock()
    with patch("app.db.supabase.get_conversation_simple", new=read), \
         patch("app.services.lead_service.update_lead_from_entities", new=update):
        await operator_contact.record_contact("c-1", "thanks, see you tomorrow")
    read.assert_not_awaited()
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_call_back_number_that_cannot_be_placed_is_kept_and_not_asked_again():
    """"call me on 0412 345 678" with no country: kept as the number to check, confirmed, and the turn
    does not ask for a number the client just gave (CodeRabbit #270)."""
    from tests.test_chat_contacts import _CID, _callback_turn, _lead_payloads

    run, mocks = _callback_turn("please call me on 0412 345 678",
                                conv_extra={"visitor_email": "cliente@example.com"})
    with patch("app.pipeline.orchestrator.db.patch_conversation_presence",
               new=AsyncMock(return_value=True)) as presence:
        resp = await run()
    presence.assert_any_await(_CID, {"phone_to_check": "0412 345 678"})
    assert "0412 345 678" in resp.message
    assert "best number" not in resp.message.lower()
    assert all("visitor_phone" not in c.args[1] for c in mocks["update_conversation"].await_args_list)
    assert _lead_payloads(mocks)[-1]["notes"].startswith("CLIENT ASKED TO BE CALLED — 0412 345 678")


async def _lead_update(entities: dict, presence_ok: bool):
    from app.db import supabase as db
    from app.services import lead_service
    from tests.test_chat_contacts import _CID, _LeadsTable

    client = _LeadsTable({"id": "L-1", "conversation_id": _CID, "intent_signals": {}, "notes": ""})

    async def _run(fn, **_kwargs):
        return fn()

    presence = AsyncMock(return_value=presence_ok)
    with patch.object(db, "get_client", return_value=client), patch.object(db, "_run_sync", _run), \
         patch.object(db, "patch_conversation_presence", new=presence):
        kept = await lead_service.update_lead_from_entities(_CID, entities)
    return kept, presence


@pytest.mark.asyncio
async def test_a_number_to_check_that_was_not_saved_is_never_reported_as_kept():
    """The WhatsApp button says "your consultant will write to you" only when the number was kept: a failed
    phone_to_check write must reach the caller and the lead-write counter (CodeRabbit #270)."""
    from app.services import lead_service

    before = lead_service.LEAD_WRITE_HEALTH["errors_since_boot"]
    kept, presence = await _lead_update({"phone": "0412 345 678", "_phone_country": None}, presence_ok=False)
    assert kept is False
    presence.assert_awaited_once()
    assert presence.await_args.args[1] == {"phone_to_check": "0412 345 678"}
    assert lead_service.LEAD_WRITE_HEALTH["errors_since_boot"] == before + 1


@pytest.mark.asyncio
async def test_a_number_to_check_that_was_saved_is_kept():
    kept, presence = await _lead_update({"phone": "4930123456", "_phone_country": "DE"}, presence_ok=True)
    assert kept is True
    assert presence.await_args.args[1] == {"phone_to_check": "4930123456"}


@pytest.mark.asyncio
@pytest.mark.parametrize("visitor_country, conv_extra", [("AU", {}), (None, {"visitor_phone_country": "AU"})])
async def test_the_country_chosen_with_the_number_places_it(visitor_country, conv_extra):
    """The booking form sends the country with the number: a local "0412 345 678" is placed by it, before
    any hint from the browser (CodeRabbit #270, round 3)."""
    from app.models.chat import VisitorInfo
    from tests.test_chat_contacts import _CID, _callback_turn

    run, mocks = _callback_turn("0412 345 678", visitor=VisitorInfo(country_code=visitor_country),
                                conv_extra={"visitor_email": "cliente@example.com", **conv_extra})
    resp = await run()
    mocks["update_conversation"].assert_any_await(_CID, {"visitor_phone": "+61412345678"})
    assert "+61412345678" in resp.message


def test_a_country_that_is_not_one_is_ignored():
    from app.services.phone_country import known_country

    assert known_country("au") == "AU" and known_country(" GB ") == "GB"
    assert known_country("+61") is None and known_country("XX") is None and known_country(None) is None


@pytest.mark.asyncio
async def test_a_new_conversation_stores_the_local_number_placed_by_its_country():
    from app.db import supabase as db
    from app.models.chat import VisitorInfo

    written = {}

    class _Insert:
        def __init__(self, payload):
            written.update(payload)

        def execute(self):
            return type("R", (), {"data": [dict(written, id="c-new")]})()

    class _Client:
        def table(self, _name):
            return type("T", (), {"insert": lambda _self, payload: _Insert(payload)})()

    async def _run(fn, **_kwargs):
        return fn()

    with patch.object(db, "get_client", return_value=_Client()), patch.object(db, "_run_sync", _run):
        row = await db.get_or_create_conversation(None, "sales", VisitorInfo(phone="0412 345 678", country_code="AU"))
    assert row and written["visitor_phone"] == "+61412345678" and written["visitor_phone_country"] == "AU"
    written.clear()
    with patch.object(db, "get_client", return_value=_Client()), patch.object(db, "_run_sync", _run):
        await db.get_or_create_conversation(None, "sales", VisitorInfo(phone="0412 345 678"))
    assert "visitor_phone" not in written, "a number that can't be placed is not written as an empty string"


def test_the_crm_payload_places_a_local_number_by_the_visitors_country():
    from types import SimpleNamespace

    from app.services.crm import build_crm_payload

    lead = {"origin_code": "SYD", "destination_code": "LHR", "departure_date": "2027-02-10", "passengers": 1}
    visitor = SimpleNamespace(name="Test Client", email="cliente@example.com", phone="0412 345 678", country_code="AU")
    payload = build_crm_payload(lead, visitor, conv_metadata={}, suid=None)
    assert "+61412345678" in str(payload)
