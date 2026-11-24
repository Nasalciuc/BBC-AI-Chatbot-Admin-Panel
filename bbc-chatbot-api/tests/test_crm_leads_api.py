"""The agent's "Create lead in CRM" button (CRM's CHAT-LEADS-API v1.1). Every name, number and address here is
invented."""

import os
import sys
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoieCJ9.sig")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import crm_lead as route  # noqa: E402
from app.services import crm_leads_api as api  # noqa: E402

TODAY = date(2026, 11, 24)
SOON = (TODAY + timedelta(days=20)).isoformat()
LATER = (TODAY + timedelta(days=30)).isoformat()
AGENT, OTHER = "agent-me", "agent-other"


def _conv(**kw):
    base = {
        "id": "c-1",
        "status": "active",
        "mode": "human",
        "tunnel": "sales",
        "assigned_agent_id": AGENT,
        "visitor_name": "Mara Test",
        "visitor_email": "mara@example.com",
        "visitor_phone": "+1 415 555 0123",
        "metadata": {
            "site": "bct",
            "utm_source": "kayak",
            "utm_medium": "",
            "msclkid": "ms-1",
            "page_url": "https://example.com/flights/london",
            "client_ip": "203.0.113.7",
            "user_agent": "Mozilla/5.0",
        },
        "lead": {
            "id": "L1",
            "origin_code": "jfk",
            "destination_code": "LHR",
            "departure_date": SOON,
            "return_date": LATER,
            "trip_type": "round_trip",
            "cabin_class": "Business",
            "passengers": 3,
            "children_count": 1,
            "infant_count": 0,
            "created_in_crm": False,
        },
        "messages": [
            {"role": "user", "content": "Hi, two adults and a child to London", "created_at": "2026-11-24T15:02:00+00:00"},
            {"role": "ai", "content": "Sure, what dates?", "created_at": "2026-11-24T15:02:05+00:00"},
            {"role": "system", "content": "Agent joined", "created_at": "2026-11-24T15:03:00+00:00"},
            {"role": "agent", "content": "Hi Mara, I'll get you options.", "created_at": "2026-11-24T15:03:10+00:00"},
        ],
    }
    base.update(kw)
    return base


# ── the payload ─────────────────────────────────────────────────


def test_the_payload_carries_what_the_chat_knows():
    p = api.build_payload(_conv(), agent_email="agent@example.com", agent_id="57", note="  Wants a window seat  ",
                          today=TODAY)
    assert p["conversation_id"] == "c-1"
    assert p["site"] == "BCT"
    assert p["chat_url"].endswith("/chats?highlight=c-1")
    assert p["agent_id"] == 57 and p["agent_email"] == "agent@example.com"
    assert p["note"] == "Wants a window seat"
    assert p["client"] == {
        "name": "Mara Test", "email": "mara@example.com", "phone": "+14155550123",
        "ip": "203.0.113.7", "user_agent": "Mozilla/5.0",
    }
    assert p["marketing"] == {"utm_source": "kayak", "msclkid": "ms-1", "landing_page": "https://example.com/flights/london"}
    assert p["trip"] == {
        "cabin_class": "business", "adults": 2, "children": 1, "infants": 0,
        "segments": [{"from": "JFK", "to": "LHR", "date": SOON}, {"from": "LHR", "to": "JFK", "date": LATER}],
    }
    assert [m["from"] for m in p["transcript"]] == ["client", "bot", "agent"], "system lines stay out"
    assert p["transcript"][0]["at"] == "2026-11-24T15:02:00+00:00"


def test_no_source_is_inferred_from_a_click_id():
    """v1.1: the CRM applies its own rules to a click id; we send it raw and add no utm_source."""
    conv = _conv(metadata={"gclid": "g-1"})
    assert api.build_payload(conv, agent_email="a@example.com", today=TODAY)["marketing"] == {"gclid": "g-1"}


def test_an_empty_marketing_block_is_left_out():
    p = api.build_payload(_conv(metadata={"utm_source": "  "}), agent_email="a@example.com", today=TODAY)
    assert "marketing" not in p


@pytest.mark.parametrize("lead_patch", [
    {"origin_code": "AAA"},  # a placeholder route
    {"departure_date": (TODAY - timedelta(days=1)).isoformat()},  # a date that has passed
    {"departure_date": "2026-02-30"},  # not a date
    {"destination_code": None},
])
def test_a_trip_we_dont_really_know_sends_no_segment(lead_patch):
    lead = {**_conv()["lead"], **lead_patch}
    assert "segments" not in api.trip_of(lead, TODAY)


def test_a_one_way_trip_has_one_segment_even_with_a_return_date():
    lead = {**_conv()["lead"], "trip_type": "one_way"}
    assert len(api.trip_of(lead, TODAY)["segments"]) == 1


def test_unknown_passengers_send_no_count():
    lead = {**_conv()["lead"], "passengers": None}
    trip = api.trip_of(lead, TODAY)
    assert "adults" not in trip and "children" not in trip


def test_economy_is_sent_as_economy():
    assert api.trip_of({**_conv()["lead"], "cabin_class": "economy"}, TODAY)["cabin_class"] == "economy"


def test_a_local_number_never_leaves_and_an_international_one_does():
    """A number without its country would be read as US/Canada by the CRM."""
    local = _conv(visitor_phone="07700 900123", visitor_email=None, metadata={"phone_to_check": "07700 900123"})
    assert "phone" not in api.client_of(local)
    assert not api.has_contact(local)
    assert api.client_of(_conv(visitor_phone="+44 7700 900123"))["phone"] == "+447700900123"


@pytest.mark.parametrize("name", ["Visitor", "customer", "", "A"])
def test_a_placeholder_name_is_left_for_the_crm(name):
    assert "name" not in api.client_of(_conv(visitor_name=name))


def test_a_malformed_email_is_not_a_contact():
    conv = _conv(visitor_email="mara@", visitor_phone=None)
    assert not api.has_contact(conv)
    assert api.has_contact(_conv(visitor_phone=None))


# ── the switches ────────────────────────────────────────────────


def _flags(values):
    return patch("app.flags.flag", side_effect=lambda name, default: values.get(name, default))


def test_the_button_is_off_by_default():
    with _flags({}):
        assert not api.enabled_for({"id": AGENT, "email": "agent@example.com"})


def test_the_pilot_list_turns_it_on_for_its_agents_only():
    with _flags({"CRM_LEADS_API_PILOT": " Agent@Example.com, someone-id "}):
        assert api.enabled_for({"id": AGENT, "email": "agent@example.com"})
        assert api.enabled_for({"id": "someone-id", "email": "x@example.com"})
        assert not api.enabled_for({"id": OTHER, "email": "other@example.com"})
        assert not api.everyone_on()


def test_the_switch_turns_it_on_for_everyone():
    with _flags({"CRM_LEADS_API": True}):
        assert api.enabled_for({"id": OTHER, "email": "other@example.com"})
        assert api.everyone_on()


# ── the call ────────────────────────────────────────────────────


@pytest.fixture
def crm(monkeypatch):
    """A fake CRM: answers are queued per test; every request is kept."""
    answers: list = []
    seen: list = []

    def handler(request: httpx.Request):
        seen.append(request)
        nxt = answers.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(api.settings, "crm_chat_api_url", "https://crm.example.test/chat-api/v1/leads")
    monkeypatch.setattr(api.settings, "crm_chat_api_key", "test-key-1")
    monkeypatch.setattr(api, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return answers, seen


def _ok(status=201, **kw):
    body = {"success": True, "created": status == 201, "flight_request_id": 98765,
            "url": "https://crm.example.test/requests/98765", "warnings": [], **kw}
    return httpx.Response(status, json=body)


@pytest.mark.asyncio
async def test_a_created_lead_comes_back_with_its_number(crm):
    answers, seen = crm
    answers.append(_ok(warnings=[{"code": "TRIP_IGNORED", "message": "Bad airport"}]))
    before = api.CRM_LEADS_API_HEALTH["created"]
    call = await api.post_lead({"conversation_id": "c-1"})
    assert call.ok and call.status == 201 and call.data["flight_request_id"] == 98765
    assert api.CRM_LEADS_API_HEALTH["created"] == before + 1
    assert api.CRM_LEADS_API_HEALTH["warnings"]["TRIP_IGNORED"] >= 1
    assert seen[0].headers["Authorization"] == "Bearer test-key-1"


@pytest.mark.asyncio
async def test_a_second_call_is_the_same_lead(crm):
    answers, _ = crm
    answers.append(_ok(status=200))
    before = api.CRM_LEADS_API_HEALTH["existing"]
    call = await api.post_lead({"conversation_id": "c-1"})
    assert call.ok and call.status == 200
    assert api.CRM_LEADS_API_HEALTH["existing"] == before + 1
    assert api.record_of(call, agent_user_id=AGENT, by=AGENT, site="BBC")["created"] is False


@pytest.mark.asyncio
async def test_a_500_is_retried_once(crm):
    answers, seen = crm
    answers.extend([httpx.Response(500, json={"success": False, "error_code": "SERVER_ERROR"}), _ok()])
    call = await api.post_lead({"conversation_id": "c-1"})
    assert call.ok and len(seen) == 2


@pytest.mark.asyncio
async def test_two_timeouts_give_up_with_a_message(crm):
    answers, seen = crm
    answers.extend([httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")])
    call = await api.post_lead({"conversation_id": "c-1"})
    assert not call.ok and call.error_code == "TIMEOUT" and len(seen) == 2
    assert api.CRM_LEADS_API_HEALTH["errors"]["TIMEOUT"] >= 1


@pytest.mark.asyncio
async def test_a_refusal_is_not_retried_and_keeps_the_crms_words(crm):
    answers, seen = crm
    answers.append(httpx.Response(422, json={"success": False, "error_code": "AGENT_NOT_FOUND",
                                             "message": "No CRM user matches this agent."}))
    call = await api.post_lead({"conversation_id": "c-1"})
    assert not call.ok and len(seen) == 1
    assert call.error_code == "AGENT_NOT_FOUND" and call.message == "No CRM user matches this agent."


@pytest.mark.asyncio
async def test_a_wrong_key_is_an_admins_problem_not_the_agents(crm):
    answers, _ = crm
    answers.append(httpx.Response(401, json={"success": False, "error_code": "AUTH_INVALID", "message": "Bad key"}))
    call = await api.post_lead({"conversation_id": "c-1"})
    assert call.message == "The CRM connection isn't set up right. Tell an admin."


@pytest.mark.asyncio
async def test_rate_limited_says_when_to_try_again(crm):
    answers, _ = crm
    answers.append(httpx.Response(429, headers={"Retry-After": "12"}, json={"success": False, "error_code": "RATE_LIMITED"}))
    call = await api.post_lead({"conversation_id": "c-1"})
    assert call.retry_after == 12
    assert route._http_status(call) == 429 and "12 seconds" in route._http_message(call)


@pytest.mark.asyncio
async def test_not_configured_makes_no_call(monkeypatch):
    monkeypatch.setattr(api.settings, "crm_chat_api_url", "")
    before = api.CRM_LEADS_API_HEALTH["not_configured"]
    call = await api.post_lead({"conversation_id": "c-1"})
    assert not call.ok and call.error_code == "NOT_CONFIGURED"
    assert api.CRM_LEADS_API_HEALTH["not_configured"] == before + 1
    assert route._http_status(call) == 503


# ── the route ───────────────────────────────────────────────────


ME = {"id": AGENT, "role": "sales", "email": "agent@example.com", "crm_sub": "57"}
OWNER = {"id": "boss", "role": "owner", "email": "boss@example.com"}


def _route_patches(conv, call=None, marked=None):
    return {
        "flags": _flags({"CRM_LEADS_API": True}),
        "conv": patch.object(route.db, "get_conversation", new=AsyncMock(return_value=conv)),
        "user": patch.object(route.db, "get_user_by_id", new=AsyncMock(return_value={"id": AGENT, "email": "agent@example.com"})),
        "post": patch.object(route.api, "post_lead", new=AsyncMock(return_value=call or api.CrmLeadCall(
            ok=True, status=201, data={"success": True, "created": True, "flight_request_id": 98765,
                                       "url": "https://crm.example.test/requests/98765", "warnings": []}))),
        "keep": patch.object(route.db, "patch_conversation_presence", new=AsyncMock(return_value=True)),
        "mark": patch.object(route.db, "mark_lead_created_in_crm", new=AsyncMock(return_value=marked if marked is not None else {"id": "L1"})),
        "universe": patch.object(route.db, "invalidate_leads_universe"),
    }


async def _create(user, conv, **kw):
    ps = _route_patches(conv, **{k: v for k, v in kw.items() if k in ("call", "marked")})
    with ps["flags"], ps["conv"], ps["user"], ps["post"] as post, ps["keep"] as keep, ps["mark"] as mark, ps["universe"]:
        try:
            out = await route.create_crm_lead("c-1", body=route.CrmLeadRequest(note=kw.get("note")),
                                              confirm_locked=False, user=user)
            err = None
        except HTTPException as e:
            out, err = None, e
    return out, err, post, keep, mark


@pytest.mark.asyncio
async def test_the_agent_who_has_the_chat_gets_the_lead():
    out, err, post, keep, mark = await _create(ME, _conv(), note="Flexible by two days")
    assert err is None and out["data"]["lead"]["flight_request_id"] == 98765
    payload = post.await_args.args[0]
    assert payload["agent_email"] == "agent@example.com" and payload["agent_id"] == 57
    assert payload["note"] == "Flexible by two days"
    assert keep.await_args.args[1]["crm_lead"]["url"] == "https://crm.example.test/requests/98765"
    mark.assert_awaited_once()
    assert mark.await_args.kwargs["crm_lead_id"] == "98765" and mark.await_args.kwargs["source"] == "push"


@pytest.mark.asyncio
async def test_a_manager_presses_it_for_the_agent_who_has_the_chat():
    out, err, post, _, _ = await _create(OWNER, _conv())
    assert err is None
    payload = post.await_args.args[0]
    assert payload["agent_email"] == "agent@example.com", "the lead goes to the agent, never to the manager"
    assert "agent_id" not in payload, "the manager's CRM id is not the agent's"


@pytest.mark.parametrize("conv,why", [
    (_conv(assigned_agent_id=OTHER), "another agent's"),
    (_conv(assigned_agent_id=None, mode="ai"), "Take the chat first"),
])
@pytest.mark.asyncio
async def test_never_on_a_chat_that_isnt_the_agents(conv, why):
    out, err, post, _, _ = await _create(ME, conv)
    assert err is not None and err.status_code == 403 and why in err.detail
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_contact_no_call():
    out, err, post, _, _ = await _create(ME, _conv(visitor_email=None, visitor_phone=None))
    assert err.status_code == 422 and "ask Mara in the chat" in err.detail
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lead_already_made_is_answered_without_a_call():
    rec = {"flight_request_id": 98765, "url": "https://crm.example.test/requests/98765", "warnings": []}
    out, err, post, _, _ = await _create(ME, _conv(metadata={"crm_lead": rec}))
    assert out["data"]["lead"] == rec
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_crms_refusal_reaches_the_agent_in_its_words():
    call = api.CrmLeadCall(ok=False, status=403, error_code="AGENT_NO_SITE_ACCESS",
                           message="You are not on a team for BCT.")
    out, err, _, keep, mark = await _create(ME, _conv(), call=call)
    assert err.status_code == 422 and err.detail == "You are not on a team for BCT."
    keep.assert_not_awaited()
    mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lead_the_old_path_already_sent_keeps_its_receipt():
    conv = _conv()
    conv["lead"] = {**conv["lead"], "created_in_crm": True, "crm_lead_id": "a" * 24}
    out, err, _, keep, mark = await _create(ME, conv)
    assert err is None
    keep.assert_awaited_once()
    mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lead_row_that_cannot_be_marked_is_counted():
    before = api.CRM_LEADS_API_HEALTH["mark_failed"]
    with patch.object(route.db, "mark_lead_created_in_crm", new=AsyncMock(return_value=None)):
        ps = _route_patches(_conv())
        with ps["flags"], ps["conv"], ps["user"], ps["post"], ps["keep"], ps["universe"]:
            out = await route.create_crm_lead("c-1", body=None, confirm_locked=False, user=ME)
    assert out["data"]["lead"]["flight_request_id"] == 98765, "the lead exists: the agent still gets the link"
    assert api.CRM_LEADS_API_HEALTH["mark_failed"] == before + 1


@pytest.mark.asyncio
async def test_off_for_this_user_refuses_and_the_status_says_off():
    with _flags({}):
        with pytest.raises(HTTPException) as e:
            await route.create_crm_lead("c-1", body=None, confirm_locked=False, user=ME)
        status = await route.crm_lead_status("c-1", user=ME)
    assert e.value.status_code == 403
    assert status["data"] == {"enabled": False, "lead": None, "can_create": False, "reason": None}


@pytest.mark.parametrize("conv,can,reason", [
    (_conv(), True, None),
    (_conv(visitor_email=None, visitor_phone=None), False, "No phone or e-mail yet: ask Mara in the chat."),
    (_conv(assigned_agent_id=OTHER), False, None),
])
@pytest.mark.asyncio
async def test_the_status_tells_the_panel_what_to_show(conv, can, reason):
    with _flags({"CRM_LEADS_API": True}), \
            patch.object(route.db, "get_conversation_simple", new=AsyncMock(return_value=conv)):
        status = await route.crm_lead_status("c-1", user=ME)
    assert status["data"]["enabled"] is True
    assert status["data"]["can_create"] is can and status["data"]["reason"] == reason


@pytest.mark.asyncio
async def test_a_chat_that_cannot_be_read_is_a_503_not_an_empty_answer():
    with _flags({"CRM_LEADS_API": True}), \
            patch.object(route.db, "get_conversation_simple", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as e:
            await route.crm_lead_status("c-1", user=ME)
    assert e.value.status_code == 503
