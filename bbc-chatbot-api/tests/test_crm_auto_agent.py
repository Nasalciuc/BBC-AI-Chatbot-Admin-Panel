"""A chat an agent has had goes to the CRM with that agent, whichever sender sends it (owner, 25 Nov 2026, chat #3700:
the old Send to CRM sent the lead to /requests/chatbot, which names no agent). Every name here is invented."""

import base64
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _placeholder_jwt() -> str:
    """JWT-shaped, built at run time: create_client checks the shape and nothing checks the signature, and no
    key-shaped literal sits in the source for the secret scan (.gitleaks.toml) to flag."""
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part({'role': 'test'})}.sig"


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", _placeholder_jwt())
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.services import crm  # noqa: E402
from app.services import crm_leads_api as api  # noqa: E402

AGENT = "agent-me"
SOON = (date.today() + timedelta(days=30)).isoformat()


def _conv(**kw):
    base = {
        "id": "c-1", "status": "active", "mode": "human", "tunnel": "sales",
        "assigned_agent_id": AGENT,
        "visitor_name": "Mara Test", "visitor_email": "mara@example.com", "visitor_phone": "+1 415 555 0123",
        "metadata": {"site": "bbc", "utm_source": "kayak"},
        "lead": {"id": "L1", "origin_code": "LAX", "destination_code": "MXP", "departure_date": SOON,
                 "trip_type": "one_way", "cabin_class": "business", "passengers": 2, "created_in_crm": False},
        "messages": [
            {"role": "user", "content": "LAX to Milan for two", "created_at": "2026-11-25T11:06:00+00:00"},
            {"role": "agent", "content": "What dates?", "created_at": "2026-11-25T11:06:10+00:00"},
        ],
    }
    base.update(kw)
    return base


def _flags(values):
    return patch("app.flags.flag", side_effect=lambda name, default: values.get(name, default))


def _ok_call(status=201):
    return api.CrmLeadCall(ok=True, status=status, data={
        "success": True, "created": status == 201, "flight_request_id": 98765,
        "url": "https://crm.example.test/requests/98765", "assigned_agent_id": 57,
        "assigned_to_requested_agent": True, "warnings": []})


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(api.settings, "crm_chat_api_url", "https://crm.example.test/chat-api/v1/leads")
    monkeypatch.setattr(api.settings, "crm_chat_api_key", "test-key-1")


async def _send(conv, *, call=None, presser=None, user=None, read_error=None, flags=None):
    """send_for_agent with the database and the CRM call faked; returns (result, post, keep, read)."""
    read = AsyncMock(side_effect=read_error) if read_error else AsyncMock(return_value=conv)
    agent_row = {"id": AGENT, "email": "agent@example.com"} if user is None else user
    with _flags(flags or {}), \
            patch.object(api, "post_lead", new=AsyncMock(return_value=call or _ok_call())) as post, \
            patch("app.db.supabase.get_conversation", new=read), \
            patch("app.db.supabase.get_user_by_id", new=AsyncMock(return_value=agent_row)), \
            patch("app.db.supabase.patch_conversation_presence", new=AsyncMock(return_value=True)) as keep:
        out = await api.send_for_agent("c-1", presser=presser)
    return out, post, keep, read


# ── when the automatic path runs at all ─────────────────────────


@pytest.mark.asyncio
async def test_switched_off_it_reads_nothing_and_the_old_way_sends(configured):
    out, post, _, read = await _send(_conv(), flags={"CRM_LEADS_API_AUTO": False})
    assert (out.route, out.reason) == ("old", "off")
    read.assert_not_awaited()
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_the_crms_address_and_key_nothing_changes(monkeypatch):
    monkeypatch.setattr(api.settings, "crm_chat_api_url", "")
    before = api.CRM_LEADS_API_HEALTH["auto_not_configured"]
    out, post, _, read = await _send(_conv())
    assert (out.route, out.reason) == ("old", "not_configured")
    assert api.CRM_LEADS_API_HEALTH["auto_not_configured"] == before + 1
    read.assert_not_awaited()
    post.assert_not_awaited()


def test_on_by_default_once_the_crm_is_set_up(configured):
    with _flags({}):
        assert api.auto_on()
    with _flags({"CRM_LEADS_API_AUTO": False}):
        assert not api.auto_on()


# ── a chat an agent has had ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_agents_chat_goes_to_the_agent(configured):
    before = api.CRM_LEADS_API_HEALTH["auto_sent"]
    out, post, keep, _ = await _send(_conv())
    assert (out.route, out.ok, out.request_id) == ("new", True, "98765")
    payload = post.await_args.args[0]
    assert payload["agent_email"] == "agent@example.com"
    assert "agent_id" not in payload, "no CRM id without the agent's own press"
    assert payload["conversation_id"] == "c-1" and payload["site"] == "BBC"
    kept = keep.await_args.args[1]["crm_lead"]
    assert kept["flight_request_id"] == 98765 and kept["agent_user_id"] == AGENT and kept["by"] == "auto"
    assert api.CRM_LEADS_API_HEALTH["auto_sent"] == before + 1


@pytest.mark.asyncio
async def test_the_agents_own_press_sends_their_crm_id_too(configured):
    me = {"id": AGENT, "role": "sales", "crm_sub": "57"}
    out, post, keep, _ = await _send(_conv(), presser=me)
    assert out.ok
    payload = post.await_args.args[0]
    assert payload["agent_id"] == 57 and payload["agent_email"] == "agent@example.com"
    assert keep.await_args.args[1]["crm_lead"]["by"] == AGENT


@pytest.mark.asyncio
async def test_a_managers_press_still_names_the_agent_never_the_manager(configured):
    boss = {"id": "boss", "role": "owner", "crm_sub": "1"}
    out, post, _, _ = await _send(_conv(), presser=boss)
    assert out.ok
    payload = post.await_args.args[0]
    assert payload["agent_email"] == "agent@example.com" and "agent_id" not in payload


@pytest.mark.asyncio
async def test_the_agent_who_closed_or_talked_counts_as_the_chats_agent(configured):
    conv = _conv(assigned_agent_id=None, metadata={"site": "bbc", "closed_by_agent_id": AGENT})
    out, post, _, _ = await _send(conv)
    assert out.ok and post.await_args.args[0]["agent_email"] == "agent@example.com"


@pytest.mark.asyncio
async def test_a_lead_already_made_here_is_answered_without_a_call(configured):
    rec = {"flight_request_id": 98765, "url": "https://crm.example.test/requests/98765"}
    out, post, _, _ = await _send(_conv(metadata={"site": "bbc", "crm_lead": rec}))
    assert (out.route, out.ok, out.request_id, out.reason) == ("new", True, "98765", "already")
    post.assert_not_awaited()


# ── the old way, as before ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_chat_no_agent_had_stays_on_the_old_way(configured):
    out, post, _, _ = await _send(_conv(assigned_agent_id=None, mode="ai"))
    assert (out.route, out.reason) == ("old", "no_agent")
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lead_with_no_chat_stays_on_the_old_way(configured):
    with _flags({}), patch.object(api, "post_lead", new=AsyncMock()) as post, \
            patch("app.db.supabase.get_conversation", new=AsyncMock(return_value=None)):
        out = await api.send_for_agent("lead-id-not-a-chat")
    assert (out.route, out.reason) == ("old", "no_conversation")
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_local_number_only_stays_on_the_old_way(configured):
    out, post, _, _ = await _send(_conv(visitor_email=None, visitor_phone="068859501"))
    assert (out.route, out.reason) == ("old", "no_contact")
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_agent_with_no_email_and_no_crm_id_goes_the_old_way(configured):
    out, post, _, _ = await _send(_conv(), user={"id": AGENT, "email": ""})
    assert (out.route, out.reason) == ("old", "agent_unread")
    post.assert_not_awaited()


@pytest.mark.parametrize("code,status", [("AGENT_NOT_FOUND", 422), ("AGENT_NO_SITE_ACCESS", 403),
                                         ("AUTH_INVALID", 401), ("HTTP_404", 404)])
@pytest.mark.asyncio
async def test_a_refusal_for_good_sends_the_old_way_so_no_lead_is_lost(configured, code, status):
    before = api.CRM_LEADS_API_HEALTH["auto_fallback"]
    out, _, keep, _ = await _send(_conv(), call=api.CrmLeadCall(ok=False, status=status, error_code=code))
    assert (out.route, out.reason, out.error) == ("old", "refused", code)
    keep.assert_not_awaited()
    assert api.CRM_LEADS_API_HEALTH["auto_fallback"] == before + 1


# ── nothing twice ───────────────────────────────────────────────


@pytest.mark.parametrize("code,status", [("TIMEOUT", 0), ("NETWORK", 0), ("SERVER_ERROR", 503),
                                         ("RATE_LIMITED", 429), ("BAD_ANSWER", 200), ("LEASE_LOST", 0)])
@pytest.mark.asyncio
async def test_a_call_that_may_have_reached_the_crm_is_not_sent_the_old_way(configured, code, status):
    before = api.CRM_LEADS_API_HEALTH["auto_retry"]
    out, _, keep, _ = await _send(_conv(), call=api.CrmLeadCall(ok=False, status=status, error_code=code))
    assert (out.route, out.ok, out.error) == ("new", False, f"crm_api:{code}")
    keep.assert_not_awaited()
    assert api.CRM_LEADS_API_HEALTH["auto_retry"] == before + 1


@pytest.mark.asyncio
async def test_a_chat_that_cant_be_read_sends_nothing_either_way(configured):
    before = api.CRM_LEADS_API_HEALTH["auto_read_failed"]
    out, post, _, _ = await _send(_conv(), read_error=RuntimeError("db down"))
    assert (out.route, out.ok, out.error) == ("new", False, "crm_api:READ_FAILED")
    post.assert_not_awaited()
    assert api.CRM_LEADS_API_HEALTH["auto_read_failed"] == before + 1


# ── the old senders ask first ───────────────────────────────────


def _visitor():
    return SimpleNamespace(name="Mara Test", email="mara@example.com", phone="+14155550123")


def _old_client_must_not_be_used():
    client = MagicMock()
    client.post = AsyncMock(side_effect=AssertionError("the old endpoint was called"))
    return patch.object(crm, "_get_crm_client", return_value=client)


@pytest.mark.asyncio
async def test_submit_with_an_agent_returns_the_chat_apis_lead_and_skips_the_old_endpoint():
    sent = api.AgentSend(route="new", ok=True, request_id="98765", reason="sent")
    with patch.object(api, "send_for_agent", new=AsyncMock(return_value=sent)) as ask, _old_client_must_not_be_used():
        result = await crm.submit_to_crm(_conv()["lead"], _visitor(), "c-1", conv_metadata={"site": "bbc"})
    assert result.success and result.request_id == "98765"
    assert ask.await_args.args == ("c-1",)


@pytest.mark.asyncio
async def test_submit_to_try_again_reports_a_failure_and_skips_the_old_endpoint():
    sent = api.AgentSend(route="new", ok=False, error="crm_api:TIMEOUT", reason="retry")
    with patch.object(api, "send_for_agent", new=AsyncMock(return_value=sent)), _old_client_must_not_be_used():
        result = await crm.submit_to_crm(_conv()["lead"], _visitor(), "c-1", conv_metadata={"site": "bbc"})
    assert not result.success and result.error == "crm_api:TIMEOUT"


@pytest.mark.asyncio
async def test_submit_without_an_agent_goes_the_old_way_as_before(monkeypatch):
    monkeypatch.setattr(crm.settings, "crm_api_url", "https://webapi.example.test")
    sent = api.AgentSend(route="old", reason="no_agent")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"data": {"id": "6b06c1b566f1e609f63bff26"}}
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    with patch.object(api, "send_for_agent", new=AsyncMock(return_value=sent)), \
            patch.object(crm, "_get_crm_client", return_value=client), \
            patch.object(crm, "_resolve_crm_base", return_value="https://webapi.example.test"):
        result = await crm.submit_to_crm(_conv()["lead"], _visitor(), "c-1", conv_metadata={"site": "bbc"})
    assert result.success and result.request_id == "6b06c1b566f1e609f63bff26"
    assert client.post.await_args.args[0] == "https://webapi.example.test/requests/chatbot"


@pytest.mark.asyncio
async def test_send_to_crm_passes_who_pressed_it():
    me = {"id": AGENT, "crm_sub": "57"}
    sent = api.AgentSend(route="new", ok=True, request_id="98765", reason="sent")
    with patch.object(api, "send_for_agent", new=AsyncMock(return_value=sent)) as ask, \
            patch.object(crm, "crm_push_gate", return_value=None), \
            patch("app.db.supabase.mark_lead_created_in_crm", new=AsyncMock(return_value={"id": "L1"})) as mark:
        result = await crm.push_lead_to_crm(_conv()["lead"], _visitor(), "c-1", set_by=AGENT, presser=me)
    assert result.success
    assert ask.await_args.kwargs["presser"] is me
    assert mark.await_args.kwargs["crm_lead_id"] == "98765", "the lead row carries the chat API's number"


@pytest.mark.asyncio
async def test_the_abandoned_sender_asks_after_its_gates_and_skips_the_old_endpoint(monkeypatch):
    monkeypatch.setattr(crm.settings, "crm_api_url", "https://webapi.example.test")
    sent = api.AgentSend(route="new", ok=True, request_id="98765", reason="sent")
    with patch.object(api, "send_for_agent", new=AsyncMock(return_value=sent)) as ask, \
            patch.object(crm, "crm_hygiene_gate", return_value=None), \
            patch.object(crm, "validate_crm_payload", return_value=None), \
            _old_client_must_not_be_used():
        result = await crm.submit_abandoned_to_crm(_conv(), _conv()["lead"], presser={"id": AGENT})
    assert result.success and result.request_id == "98765"
    assert ask.await_args.args == ("c-1",) and ask.await_args.kwargs["presser"] == {"id": AGENT}


@pytest.mark.asyncio
async def test_a_gate_refusal_stays_a_refusal_and_asks_nobody(monkeypatch):
    monkeypatch.setattr(crm.settings, "crm_api_url", "https://webapi.example.test")
    with patch.object(api, "send_for_agent", new=AsyncMock()) as ask, \
            patch.object(crm, "crm_hygiene_gate", return_value="test_contact"), _old_client_must_not_be_used():
        result = await crm.submit_abandoned_to_crm(_conv(), _conv()["lead"])
    assert not result.success and result.error == "gate:test_contact"
    ask.assert_not_awaited()


def test_the_panels_send_to_crm_names_who_pressed_it():
    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "leads.py").read_text(encoding="utf-8")
    assert "submit_abandoned_to_crm(_conv_payload, lead, presser=user)" in src
    assert "set_by=user.get(\"id\"),\n            presser=user," in src
