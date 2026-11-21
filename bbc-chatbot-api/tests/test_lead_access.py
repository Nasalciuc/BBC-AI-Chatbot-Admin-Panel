"""Hole B: the CRM routes and the lead edit act on the agent's own leads only. Every name here is invented."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import lead_access

ME, OTHER = "agent-me", "agent-other"


def _conv(**kw):
    return {"id": "c1", "status": "closed", "mode": "ai", "tunnel": "sales", "metadata": {}, **kw}


@pytest.mark.parametrize("conv", [
    _conv(assigned_agent_id=ME),
    _conv(metadata={"closed_by_agent_id": ME}),
    _conv(metadata={"engaged_agent_id": ME}),
    _conv(metadata={"followup": {"state": "pending", "agent_id": ME}}),
])
def test_an_agent_may_act_on_their_own_lead(conv):
    assert lead_access.refusal({"role": "sales", "id": ME}, conv) is None


@pytest.mark.parametrize("conv", [
    _conv(assigned_agent_id=OTHER),
    _conv(metadata={"closed_by_agent_id": OTHER}),
    _conv(metadata={"engaged_agent_id": OTHER}),
    _conv(metadata={"followup": {"state": "acked", "agent_id": OTHER}}),
])
def test_never_on_a_colleagues(conv):
    assert lead_access.refusal({"role": "sales", "id": ME}, conv) == "This lead is another agent's."


def test_never_on_a_chat_waiting_blind_in_the_shared_line():
    conv = _conv(status="needs_agent", mode="waiting_for_agent")
    assert "Take the chat first" in lead_access.refusal({"role": "sales", "id": ME}, conv)


def test_a_lead_nobody_has_while_the_lottery_is_off_is_todays_practice():
    """Rule 5: an AI-only lead stays open to any agent until the lottery shares leads out."""
    assert lead_access.refusal({"role": "sales", "id": ME}, _conv()) is None


def test_with_the_lottery_on_a_lead_nobody_has_waits_for_it(monkeypatch):
    monkeypatch.setenv("FOLLOWUP_LOTTERY", "true")
    assert "lottery" in lead_access.refusal({"role": "sales", "id": ME}, _conv())


@pytest.mark.parametrize("role", ["owner", "admin", "dev"])
def test_managers_act_on_any_lead(role):
    assert lead_access.refusal({"role": role, "id": ME}, _conv(assigned_agent_id=OTHER)) is None


@pytest.mark.asyncio
async def test_a_chat_that_cannot_be_read_refuses_with_503():
    with patch("app.db.supabase.get_conversation_simple", new=AsyncMock(side_effect=RuntimeError("down"))):
        with pytest.raises(HTTPException) as e:
            await lead_access.check({"role": "sales", "id": ME}, {"id": "L1", "conversation_id": "c1"}, "crm id")
    assert e.value.status_code == 503


# ── the routes, the way hole B was opened ──────────────────────


_LEAD = {"id": "L1", "conversation_id": "c1", "created_in_crm": False}


@pytest.mark.asyncio
async def test_pasting_a_crm_id_on_a_colleagues_lead_is_refused():
    from app.api import leads as leads_api

    mark = AsyncMock()
    with patch("app.api.leads.db.get_lead_full", new=AsyncMock(return_value=dict(_LEAD))), \
            patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=_conv(assigned_agent_id=OTHER))), \
            patch("app.api.leads.db.mark_lead_created_in_crm", new=mark):
        with pytest.raises(HTTPException) as e:
            await leads_api.set_manual_crm_id("L1", crm_id="a" * 24, confirm_odd=False, user={"role": "sales", "id": ME})
    assert e.value.status_code == 403
    mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_pushing_a_colleagues_lead_to_the_crm_is_refused():
    from app.api import leads as leads_api

    with patch("app.api.leads.db.get_lead_full", new=AsyncMock(return_value=dict(_LEAD))), \
            patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=_conv(assigned_agent_id=OTHER))), \
            patch("app.services.crm.push_lead_to_crm", new=AsyncMock()) as push:
        with pytest.raises(HTTPException) as e:
            await leads_api.mark_lead_created_in_crm("L1", confirm_locked=False, user={"role": "sales", "id": ME})
    assert e.value.status_code == 403
    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_editing_a_colleagues_lead_is_refused_and_ones_own_is_not():
    from app.api import leads as leads_api

    update = AsyncMock(return_value={"id": "L1", "score": 70})
    with patch("app.api.leads.db.get_lead_full", new=AsyncMock(return_value=dict(_LEAD))), \
            patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=_conv(assigned_agent_id=OTHER))), \
            patch("app.api.leads.db.update_lead", new=update):
        with pytest.raises(HTTPException) as e:
            await leads_api.update_lead("L1", {"score": 70}, user={"role": "sales", "id": ME})
        assert e.value.status_code == 403
        update.assert_not_awaited()
        assert await leads_api.update_lead("L1", {"score": 70}, user={"role": "admin", "id": "adm"})
    with patch("app.api.leads.db.get_lead_full", new=AsyncMock(return_value=dict(_LEAD))), \
            patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=_conv(assigned_agent_id=ME))), \
            patch("app.api.leads.db.update_lead", new=update), patch("app.api.leads.db.invalidate_leads_universe"):
        assert await leads_api.update_lead("L1", {"score": 70}, user={"role": "sales", "id": ME})
