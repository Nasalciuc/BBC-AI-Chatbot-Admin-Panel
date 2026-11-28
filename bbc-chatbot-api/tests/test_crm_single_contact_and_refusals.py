"""A lead with one contact, and a CRM that refuses the same data again and again (18 Nov 2026).

The CRM takes a lead only with an email AND a phone today: a phone-only lead got a 422 ("The
client.email field is required."), and the abandoned sweep asked again every five minutes, an ERROR
each time (#3324). Now: the refusal happens here, as a gate state the panel shows; a 422 is a refusal
of that data, remembered with a fingerprint of what the push was built from, and the sweep asks again
once that data changes, or a day later (28 Nov 2026). CRM_SINGLE_CONTACT (off) lets one contact through once the CRM's team
says their side takes it. Every contact and route here is invented."""

import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import cron
from app.services import crm
from app.services.crm import (
    CRMResult,
    _refused_fields,
    crm_push_gate,
    single_contact_ok,
    submit_abandoned_to_crm,
    validate_crm_payload,
)

_PHONE = "+12105550100"
_EMAIL = "client@example.com"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("CRM_SINGLE_CONTACT", raising=False)
    cron._pushed_today = set()
    cron._pushed_today_date = ""
    yield
    cron._pushed_today = set()
    cron._pushed_today_date = ""


@pytest.fixture
def single_on(monkeypatch):
    monkeypatch.setenv("CRM_SINGLE_CONTACT", "1")


# A departure always ahead of "today", whatever day the suite runs as (CI runs it 120 days on too):
# a fixed date turns into the CRM check's past_date and hides what these tests are about.
_DEPARTS = (date.today() + timedelta(days=200)).isoformat()
_DEPARTS_NEXT = (date.today() + timedelta(days=201)).isoformat()

def _payload(email=_EMAIL, phone=_PHONE):
    return {
        "client": {"name": "Ana", "email": email, "phone": phone},
        "flights": [{"from": "JFK", "to": "MAD", "date": _DEPARTS}],
        "passengers": {"adult": 1, "child": 0, "infant": 0},
        "cabin_class": "business",
    }


# ── the contract, with the flag off (today) and on ──────────────────────

def test_the_flag_is_off_unless_set():
    assert single_contact_ok() is False


@pytest.mark.parametrize("email, phone, expected", [
    (_EMAIL, _PHONE, None),
    ("", _PHONE, "email_missing"),        # the CRM's own 422, refused here instead
    (_EMAIL, "", "phone_format"),
    ("", "", "phone_format"),
])
def test_today_the_crm_needs_both_contacts(email, phone, expected):
    assert validate_crm_payload(_payload(email, phone)) == expected


@pytest.mark.parametrize("email, phone, expected", [
    (_EMAIL, _PHONE, None),
    ("", _PHONE, None),
    (_EMAIL, "", None),
    ("", "", "phone_format"),             # one contact at least, always
    (_EMAIL, "(210) 555-0100", "phone_format"),  # a phone that is there keeps the contract's shape
])
def test_with_single_contact_on_one_contact_is_enough(single_on, email, phone, expected):
    assert validate_crm_payload(_payload(email, phone)) == expected


def test_the_strict_path_follows_the_same_flag(monkeypatch):
    lead = {"score": 60}
    assert crm_push_gate(lead, "", _PHONE) == "email_invalid"
    monkeypatch.setenv("CRM_SINGLE_CONTACT", "true")
    assert crm_push_gate(lead, "", _PHONE) is None
    assert crm_push_gate(lead, _EMAIL, "") is None
    assert crm_push_gate(lead, "", "") == "no_contact"
    assert crm_push_gate(lead, "test@example.com", _PHONE) == "test_email", "hygiene still holds"


# ── a 422 is a refusal of this data, with the fields it names ───────────

def _resp(status, body=None, text=None):
    resp = MagicMock()
    resp.status_code = status
    if body is None:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = body
    resp.text = text if text is not None else str(body)
    return resp


@pytest.mark.parametrize("body, fields", [
    ({"message": "x", "errors": {"client.phone": ["bad"], "client.email": ["required"]}}, "client.email,client.phone"),
    ({"message": "The given data was invalid."}, "unknown"),
    (None, "unknown"),
    ({"errors": ["a list, not a map"]}, "unknown"),
])
def test_the_fields_a_422_names(body, fields):
    assert _refused_fields(_resp(422, body, text="refused")) == fields


@pytest.mark.asyncio
async def test_a_422_comes_back_as_a_gate_refusal_not_a_failure():
    conv = {"id": "c-1", "visitor_name": "Ana", "visitor_email": _EMAIL, "visitor_phone": _PHONE,
            "metadata": {}, "visitor_id": "v-1", "created_at": "2026-11-18T10:00:00+00:00"}
    lead = {"id": "L1", "origin_code": "JFK", "destination_code": "MAD", "departure_date": _DEPARTS,
            "passengers": 1, "cabin_class": "business", "trip_type": "one_way"}
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(422, {"errors": {"client.phone": ["The client.phone format is invalid."]}}))
    with patch.object(crm, "_get_crm_client", return_value=client), \
         patch.object(crm.settings, "crm_api_url", "https://crm.test"):
        res = await submit_abandoned_to_crm(conv, lead)
    assert res.success is False and res.error == "gate:crm_422:client.phone"


@pytest.mark.asyncio
async def test_a_phone_only_lead_is_refused_here_without_calling_the_crm():
    conv = {"id": "c-2", "visitor_name": "Ana", "visitor_email": "", "visitor_phone": _PHONE,
            "metadata": {}, "visitor_id": "v-2", "created_at": "2026-11-18T10:00:00+00:00"}
    lead = {"id": "L2", "origin_code": "JFK", "destination_code": "MAD", "departure_date": _DEPARTS,
            "passengers": 1, "cabin_class": "business", "trip_type": "one_way"}
    client = MagicMock()
    client.post = AsyncMock()
    with patch.object(crm, "_get_crm_client", return_value=client), \
         patch.object(crm.settings, "crm_api_url", "https://crm.test"):
        res = await submit_abandoned_to_crm(conv, lead)
    assert res.error == "gate:email_missing"
    client.post.assert_not_awaited()


# ── the sweep: asks again only once the data changed ────────────────────

def _conv(email=_EMAIL, phone=_PHONE, metadata=None, cid="c-9"):
    return {
        "id": cid, "status": "active", "mode": "ai", "tunnel": "sales",
        "visitor_name": "Ana", "visitor_email": email, "visitor_phone": phone,
        "visitor_id": "v-9", "metadata": metadata or {}, "message_count": 5,
        "created_at": "2026-11-18T10:00:00+00:00", "last_user_message_at": "2026-11-18T10:05:00+00:00",
    }


_LEAD = {"id": "L9", "created_in_crm": False, "origin_code": "JFK", "destination_code": "MAD",
         "departure_date": _DEPARTS, "passengers": 1, "cabin_class": "business", "trip_type": "one_way"}


async def _sweep(conv, result, lead=None):
    with patch.object(cron.db, "get_abandoned_conversations", new=AsyncMock(return_value=[conv])), \
         patch.object(cron, "get_or_create_lead", new=AsyncMock(return_value=dict(lead or _LEAD))), \
         patch.object(cron, "submit_abandoned_to_crm", new=AsyncMock(return_value=result)) as submit, \
         patch.object(cron.db, "mark_lead_created_in_crm", new=AsyncMock(return_value={"id": "L9"})), \
         patch.object(cron.db, "update_conversation", new=AsyncMock()), \
         patch.object(cron.db, "update_lead_crm_push_state", new=AsyncMock()) as state, \
         patch.object(cron.db, "patch_conversation_presence", new=AsyncMock(return_value=True)) as meta:
        out = await cron.run_abandoned_crm()
    return out, submit, state, meta


_REFUSED = CRMResult(success=False, error="gate:crm_422:client.phone")


@pytest.mark.asyncio
async def test_a_422_is_remembered_with_what_the_push_was_built_from():
    conv = _conv()
    out, submit, state, meta = await _sweep(conv, _REFUSED)
    submit.assert_awaited_once()
    fp = cron._crm_fingerprint(conv, _LEAD)
    meta.assert_awaited_once_with("c-9", {"crm_refused": {"fields": "client.phone", "fp": fp, "at": ANY}})
    state.assert_awaited_once_with("L9", gate_reason="crm_422:client.phone")  # the panel's work-list
    assert out["results"][0]["status"] == "crm_failed"


def _ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


@pytest.mark.asyncio
async def test_the_same_data_is_not_sent_again_the_same_day():
    fp = cron._crm_fingerprint(_conv(), _LEAD)
    conv = _conv(metadata={"crm_refused": {"fields": "client.phone", "fp": fp, "at": _ago(hours=23)}})
    out, submit, state, meta = await _sweep(conv, _REFUSED)
    submit.assert_not_awaited()
    meta.assert_not_awaited()
    assert out["results"] == [{"id": "c-9", "status": "skipped", "reason": "crm_refused_unchanged"}]


# ── a day on, the same data is asked again (28 Nov 2026) ────────────────
# On 27 Nov the BCT endpoint began taking a lead with one contact; the BCT leads it had refused since 17 Nov
# stayed out until their marks were cleared by hand. The CRM's rules change: once a day, it is asked again.

@pytest.mark.asyncio
async def test_the_same_data_is_asked_again_a_day_later(caplog):
    fp = cron._crm_fingerprint(_conv(), _LEAD)
    conv = _conv(metadata={"crm_refused": {"fields": "client.phone", "fp": fp, "at": _ago(hours=24, minutes=5)}})
    before = crm.CRM_PUSH_HEALTH["refusal_asked_again"]
    with caplog.at_level(logging.INFO, logger="app.api.cron"):
        out, submit, _, _ = await _sweep(conv, CRMResult(success=True, request_id="R-3"))
    submit.assert_awaited_once()
    assert out["results"][0]["status"] == "success"
    assert crm.CRM_PUSH_HEALTH["refusal_asked_again"] == before + 1
    assert any("refused this data a day ago or more (client.phone): asked again" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_a_day_old_refusal_refused_again_waits_another_day():
    fp = cron._crm_fingerprint(_conv(), _LEAD)
    conv = _conv(metadata={"crm_refused": {"fields": "client.phone", "fp": fp, "at": _ago(days=2)}})
    out, submit, state, meta = await _sweep(conv, _REFUSED, lead={**_LEAD, "crm_push_gate_reason": "crm_422:client.phone"})
    submit.assert_awaited_once()
    meta.assert_awaited_once_with("c-9", {"crm_refused": {"fields": "client.phone", "fp": fp, "at": ANY}})
    new_mark = meta.await_args.args[1]["crm_refused"]
    assert cron._refusal_still_fresh(new_mark, datetime.now(timezone.utc)), "the new time holds it for a day"
    assert out["results"][0]["status"] == "crm_failed"


_NOW = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("at, fresh", [
    ((_NOW - timedelta(minutes=1)).isoformat(), True),
    ((_NOW - timedelta(hours=23, minutes=59)).isoformat(), True),
    ("2026-11-28T11:00:00Z", True),                       # written with a Z
    ("2026-11-28T11:00:00", True),                        # no zone: ours are UTC
    ((_NOW - timedelta(hours=24)).isoformat(), False),
    ((_NOW - timedelta(days=10)).isoformat(), False),
    ((_NOW + timedelta(minutes=5)).isoformat(), False),   # ahead of now: asked once more, a new time follows
    (None, False),
    ("", False),
    ("not a time", False),
    (1764331200, False),                                  # not ours
])
def test_when_a_refusal_is_still_fresh(at, fresh):
    mark = {"fields": "client.phone", "fp": "x"}
    if at is not None:
        mark["at"] = at
    assert cron._refusal_still_fresh(mark, _NOW) is fresh


@pytest.mark.asyncio
async def test_changed_data_is_sent_again():
    old_fp = cron._crm_fingerprint(_conv(phone="+12105550199"), _LEAD)
    conv = _conv(metadata={"crm_refused": {"fields": "client.phone", "fp": old_fp}})
    out, submit, _, _ = await _sweep(conv, CRMResult(success=True, request_id="R-1"))
    submit.assert_awaited_once()
    assert out["results"][0]["status"] == "success"


def test_the_fingerprint_is_the_contact_and_the_trip_only():
    base = cron._crm_fingerprint(_conv(), _LEAD)
    assert cron._crm_fingerprint(_conv(metadata={"heartbeat_at": "now"}), _LEAD) == base
    assert cron._crm_fingerprint({**_conv(), "message_count": 40}, _LEAD) == base
    assert cron._crm_fingerprint(_conv(email="other@example.com"), _LEAD) != base
    assert cron._crm_fingerprint(_conv(), {**_LEAD, "departure_date": _DEPARTS_NEXT}) != base
    # The same two travellers, one of them now a child: the CRM is asked something else (CodeRabbit #270).
    assert cron._crm_fingerprint(_conv(), {**_LEAD, "children_count": 1}) != base
    assert cron._crm_fingerprint(_conv(), {**_LEAD, "_infant_count": 1}) != base
    assert cron._crm_fingerprint(_conv(), {**_LEAD, "children_count": 0}) == base, "none said is none"


@pytest.mark.asyncio
async def test_the_same_refusal_again_is_debug_a_new_one_a_warning(caplog):
    gated = CRMResult(success=False, error="gate:email_missing")
    with caplog.at_level(logging.DEBUG, logger="app.api.cron"):
        await _sweep(_conv(email=""), gated)
        await _sweep(_conv(email=""), gated, lead={**_LEAD, "crm_push_gate_reason": "email_missing"})
    levels = [r.levelno for r in caplog.records if "CRM fail" in r.getMessage()]
    assert levels == [logging.WARNING, logging.DEBUG]


@pytest.mark.asyncio
async def test_an_email_alone_reaches_the_push_only_with_the_flag(monkeypatch):
    ok = CRMResult(success=True, request_id="R-2")
    out, submit, _, _ = await _sweep(_conv(phone=""), ok)
    submit.assert_not_awaited()
    assert out["results"][0]["reason"] == "phone_invalid"
    monkeypatch.setenv("CRM_SINGLE_CONTACT", "1")
    out, submit, _, _ = await _sweep(_conv(phone=""), ok)
    submit.assert_awaited_once()
