"""The old sweep and the backstop never send a second lead for a chat the agent's button already sent, and with the
button on for everyone they leave an agent's open chat to the agent. Every name here is invented."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoieCJ9.sig")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import cron  # noqa: E402

_CID = "conv-button-1"


def _conv(**kw):
    base = {
        "id": _CID, "status": "active", "mode": "human", "tunnel": "sales",
        "assigned_agent_id": None,
        "visitor_name": "Mara", "visitor_email": "mara@example.com", "visitor_phone": "+14155550123",
        "visitor_id": "v1", "metadata": {}, "created_at": "2026-11-24T10:00:00+00:00",
    }
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def _fresh_day_guard():
    cron._pushed_today = set()
    cron._pushed_today_date = ""
    yield
    cron._pushed_today = set()
    cron._pushed_today_date = ""


def _flags(values):
    return patch("app.flags.flag", side_effect=lambda name, default: values.get(name, default))


async def _sweep(conv, flags=None):
    result = MagicMock(success=True, request_id="crm-1", error=None)
    lead = {"id": "l1", "created_in_crm": False, "origin_code": "JFK", "destination_code": "LHR"}
    with _flags(flags or {}), \
            patch.object(cron.db, "get_abandoned_conversations", new=AsyncMock(return_value=[conv])), \
            patch.object(cron, "get_or_create_lead", new=AsyncMock(return_value=lead)), \
            patch.object(cron, "_contacts_already_in_crm", new=AsyncMock(return_value=set())), \
            patch("app.api.cron.submit_abandoned_to_crm", new=AsyncMock(return_value=result)) as submit, \
            patch.object(cron.db, "update_conversation", new=AsyncMock()), \
            patch.object(cron.db, "mark_lead_created_in_crm", new=AsyncMock(return_value={"id": "l1"})), \
            patch.object(cron.db, "add_message", new=AsyncMock()), \
            patch.object(cron, "_persist_refusal", new=AsyncMock(return_value=True)):
        out = await cron.run_abandoned_crm()
    return out, submit


def _reasons(out):
    return [r.get("reason") for r in out.get("results", []) if isinstance(r, dict)]


@pytest.mark.asyncio
async def test_a_chat_the_button_sent_is_never_sent_again():
    conv = _conv(metadata={"crm_lead": {"flight_request_id": 98765, "url": "https://crm.example.test/requests/98765"}})
    out, submit = await _sweep(conv)
    submit.assert_not_awaited()
    assert "made_by_button" in _reasons(out)


@pytest.mark.asyncio
async def test_a_chat_the_button_never_touched_still_goes():
    out, submit = await _sweep(_conv())
    submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_with_the_button_on_for_everyone_an_agents_open_chat_is_theirs():
    out, submit = await _sweep(_conv(assigned_agent_id="agent-1"), flags={"CRM_LEADS_API": True})
    submit.assert_not_awaited()
    assert "agent_holds_chat" in _reasons(out)


@pytest.mark.asyncio
async def test_during_the_pilot_the_sweep_is_unchanged():
    out, submit = await _sweep(_conv(assigned_agent_id="agent-1"), flags={"CRM_LEADS_API_PILOT": "agent@example.com"})
    submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_closed_chat_is_not_held_by_anyone():
    out, submit = await _sweep(_conv(assigned_agent_id="agent-1", status="closed", mode="ai"),
                               flags={"CRM_LEADS_API": True})
    submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_backstop_skips_a_chat_the_button_sent():
    lead = {"id": "l1", "conversation_id": _CID, "crm_push_attempts": 0}
    conv = _conv(metadata={"crm_lead": {"flight_request_id": 98765}})
    with _flags({}), \
            patch.object(cron.db, "get_crm_orphan_leads", new=AsyncMock(return_value=[lead])), \
            patch.object(cron.db, "get_conversation_simple", new=AsyncMock(return_value=conv)), \
            patch("app.services.crm.push_lead_to_crm", new=AsyncMock()) as push:
        await cron.run_crm_orphan_backstop()
    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_backstop_still_sends_an_ordinary_orphan():
    lead = {"id": "l1", "conversation_id": _CID, "crm_push_attempts": 0}
    with _flags({}), \
            patch.object(cron.db, "get_crm_orphan_leads", new=AsyncMock(return_value=[lead])), \
            patch.object(cron.db, "get_conversation_simple", new=AsyncMock(return_value=_conv())), \
            patch("app.services.crm.push_lead_to_crm",
                  new=AsyncMock(return_value=MagicMock(success=True, request_id="crm-1"))) as push:
        await cron.run_crm_orphan_backstop()
    push.assert_awaited_once()
