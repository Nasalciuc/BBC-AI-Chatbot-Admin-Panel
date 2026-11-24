"""The old sweep and the backstop never send a second lead for a chat the agent's button already sent, and with the
button on for everyone they leave an agent's open chat to the agent. Every name here is invented."""

import base64
import json
import os
import sys
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


# ── CodeRabbit #280, round 4 ────────────────────────────────────
# The sweep and the backstop read their rows at the start of a run and submit minutes later. Each now reads the
# receipts again under the chat's CRM lock (the one the button holds around its own call), and keeps the lock until
# its own mark is written.

from app.services import crm_leads_api  # noqa: E402


def _receipts(**kw):
    base = {"crm_lead": None, "created_in_crm": False, "crm_lead_id": None}
    base.update(kw)
    return patch.object(cron.db, "get_crm_receipts", new=AsyncMock(return_value=base))


@pytest.mark.asyncio
async def test_a_lead_the_button_made_after_the_sweeps_read_is_not_sent_again():
    with _receipts(crm_lead={"flight_request_id": 98765}):
        out, submit = await _sweep(_conv())
    submit.assert_not_awaited()
    assert "made_by_button" in _reasons(out)


@pytest.mark.asyncio
async def test_a_lead_marked_after_the_sweeps_read_is_not_sent_again():
    with _receipts(created_in_crm=True, crm_lead_id="R-1"):
        out, submit = await _sweep(_conv())
    submit.assert_not_awaited()
    assert "already_in_crm" in _reasons(out)


@pytest.mark.asyncio
async def test_receipts_that_cannot_be_read_again_wait_a_cycle():
    from app.services.crm import CRM_PUSH_HEALTH

    before = CRM_PUSH_HEALTH["receipt_read_failed"]
    with patch.object(cron.db, "get_crm_receipts", new=AsyncMock(return_value=None)):
        out, submit = await _sweep(_conv())
    submit.assert_not_awaited()
    assert "receipt_read_failed" in _reasons(out)
    assert CRM_PUSH_HEALTH["receipt_read_failed"] == before + 1


@pytest.mark.asyncio
async def test_the_sweep_holds_the_chats_crm_lock_from_its_read_to_its_mark():
    held = []

    def _locked(*_a, **_k):
        held.append(crm_leads_api.submit_lock(_CID).locked())

    result = MagicMock(success=True, request_id="crm-1", error=None)
    lead = {"id": "l1", "created_in_crm": False, "origin_code": "JFK", "destination_code": "LHR"}

    async def _read(*a, **k):
        _locked()
        return {"crm_lead": None, "created_in_crm": False, "crm_lead_id": None}

    async def _submit(*a, **k):
        _locked()
        return result

    async def _mark(*a, **k):
        _locked()
        return {"id": "l1"}

    with _flags({}), \
            patch.object(cron.db, "get_abandoned_conversations", new=AsyncMock(return_value=[_conv()])), \
            patch.object(cron, "get_or_create_lead", new=AsyncMock(return_value=lead)), \
            patch.object(cron, "_contacts_already_in_crm", new=AsyncMock(return_value=set())), \
            patch.object(cron.db, "get_crm_receipts", new=AsyncMock(side_effect=_read)), \
            patch("app.api.cron.submit_abandoned_to_crm", new=AsyncMock(side_effect=_submit)), \
            patch.object(cron.db, "update_conversation", new=AsyncMock()), \
            patch.object(cron.db, "mark_lead_created_in_crm", new=AsyncMock(side_effect=_mark)), \
            patch.object(cron.db, "add_message", new=AsyncMock()), \
            patch.object(cron, "_persist_refusal", new=AsyncMock(return_value=True)):
        await cron.run_abandoned_crm()
    assert held == [True, True, True], "the read, the submit and the mark, all under the lock"
    assert crm_leads_api.submit_lock(_CID).locked() is False, "and it is let go"


# A button lead whose lead-row mark failed: its receipt is still "this human is in the CRM" for every other chat.

@pytest.mark.asyncio
async def test_a_button_receipt_counts_for_the_contact_guard():
    rows = [{"visitor_phone": "+14155550123", "visitor_email": None}]
    with patch.object(cron.db, "get_crm_contact_rows", new=AsyncMock(return_value=[])), \
            patch.object(cron.db, "get_button_lead_contact_rows", new=AsyncMock(return_value=rows)):
        ids = await cron._contacts_already_in_crm()
    assert any(i.startswith("p:") for i in ids)
    assert cron._contact_in_crm(_conv(id="conv-second"), ids)


@pytest.mark.asyncio
async def test_a_button_receipt_read_that_fails_holds_the_push():
    with patch.object(cron.db, "get_crm_contact_rows", new=AsyncMock(return_value=[])), \
            patch.object(cron.db, "get_button_lead_contact_rows", new=AsyncMock(return_value=None)):
        assert await cron._contacts_already_in_crm() is None


@pytest.mark.asyncio
async def test_no_button_receipt_leaves_the_guard_as_it_was():
    rows = [{"visitor_phone": "+447700900123", "visitor_email": "other@example.com"}]
    with patch.object(cron.db, "get_crm_contact_rows", new=AsyncMock(return_value=rows)), \
            patch.object(cron.db, "get_button_lead_contact_rows", new=AsyncMock(return_value=[])):
        ids = await cron._contacts_already_in_crm()
    assert not cron._contact_in_crm(_conv(), ids)


# The backstop pages past the leads it may not send, so they never use up its run.

def _orphan(i, **kw):
    lead = {"id": f"l{i}", "conversation_id": f"conv-{i}", "crm_push_attempts": 0}
    lead.update(kw)
    return lead


async def _backstop(pages, convs, flags=None, push_ok=True, receipts=None):
    calls = []

    async def _page(**kw):
        calls.append(kw)
        idx = kw["offset"] // cron._BACKSTOP_PAGE
        return pages[idx] if idx < len(pages) else []

    async def _conv_of(cid):
        return convs(cid)

    with _flags(flags or {}), \
            patch.object(cron.db, "get_crm_orphan_leads", new=AsyncMock(side_effect=_page)), \
            patch.object(cron.db, "get_conversation_simple", new=AsyncMock(side_effect=_conv_of)), \
            patch.object(cron.db, "get_crm_receipts", new=AsyncMock(return_value=receipts or {
                "crm_lead": None, "created_in_crm": False, "crm_lead_id": None})), \
            patch.object(cron.db, "update_lead_crm_push_state", new=AsyncMock()), \
            patch("app.services.crm.push_lead_to_crm",
                  new=AsyncMock(return_value=MagicMock(success=push_ok, request_id="crm-1", error=None))) as push:
        out = await cron.run_crm_orphan_backstop()
    return out, push, calls


@pytest.mark.asyncio
async def test_held_chats_at_the_top_do_not_starve_the_older_orphans():
    held = [_orphan(i) for i in range(cron._BACKSTOP_PAGE)]
    older = [_orphan(1000)]

    def convs(cid):
        if cid == "conv-1000":
            return _conv(id=cid)
        return _conv(id=cid, assigned_agent_id="agent-1")

    out, push, calls = await _backstop([held, older], convs, flags={"CRM_LEADS_API": True})
    push.assert_awaited_once()
    assert push.await_args.args[2] == "conv-1000"
    assert [c["offset"] for c in calls] == [0, cron._BACKSTOP_PAGE]
    assert out["scanned"] == cron._BACKSTOP_PAGE + 1


@pytest.mark.asyncio
async def test_the_backstop_still_sends_at_most_twenty_a_run():
    page = [_orphan(i) for i in range(cron._BACKSTOP_PAGE)]
    out, push, calls = await _backstop([page, page], lambda cid: _conv(id=cid))
    assert push.await_count == cron._BACKSTOP_SENDS
    assert len(calls) == 1, "twenty sent: no second page"


@pytest.mark.asyncio
async def test_the_backstop_stops_reading_at_its_cap():
    page = [_orphan(i) for i in range(cron._BACKSTOP_PAGE)]
    pages = [page] * (cron._BACKSTOP_SCAN // cron._BACKSTOP_PAGE + 2)
    out, push, calls = await _backstop(pages, lambda cid: _conv(id=cid, assigned_agent_id="agent-1"),
                                       flags={"CRM_LEADS_API": True})
    push.assert_not_awaited()
    assert out["scanned"] == cron._BACKSTOP_SCAN


@pytest.mark.asyncio
async def test_the_backstop_reads_the_receipts_again_under_the_lock():
    out, push, _ = await _backstop([[_orphan(1)]], lambda cid: _conv(id=cid),
                                   receipts={"crm_lead": {"flight_request_id": 5}, "created_in_crm": False,
                                             "crm_lead_id": None})
    push.assert_not_awaited()
    assert out["results"] == [{"id": "l1", "status": "skipped", "reason": "made_by_button"}]


# The two reads themselves (captured before conftest stands in for them).

from app.db import supabase as sb  # noqa: E402

_real_receipts = sb.get_crm_receipts
_real_button_rows = sb.get_button_lead_contact_rows


def _db_returning(res):
    return patch.object(sb, "get_client", return_value=MagicMock()), \
        patch.object(sb, "_run_sync", new=AsyncMock(return_value=res))


@pytest.mark.parametrize("row,want", [
    ({"id": _CID, "metadata": {"crm_lead": {"flight_request_id": 7}},
      "leads": [{"created_in_crm": True, "crm_lead_id": 55}]},
     {"crm_lead": {"flight_request_id": 7}, "created_in_crm": True, "crm_lead_id": "55"}),
    ({"id": _CID, "metadata": {}, "leads": [{"created_in_crm": False, "crm_lead_id": None}]},
     {"crm_lead": None, "created_in_crm": False, "crm_lead_id": None}),
    ({"id": _CID, "metadata": None, "leads": {"created_in_crm": True, "crm_lead_id": None}},
     {"crm_lead": None, "created_in_crm": True, "crm_lead_id": None}),
    ({"id": _CID, "metadata": {"crm_lead": "not a receipt"}, "leads": []},
     {"crm_lead": None, "created_in_crm": False, "crm_lead_id": None}),
])
@pytest.mark.asyncio
async def test_the_receipts_read_both_marks_in_one_query(row, want):
    a, b = _db_returning(MagicMock(data=[row]))
    with a, b:
        assert await _real_receipts(_CID) == want


@pytest.mark.asyncio
async def test_receipts_that_cannot_be_read_are_none_not_nothing_sent():
    with patch.object(sb, "get_client", side_effect=RuntimeError("SSL EOF")):
        assert await _real_receipts(_CID) is None


@pytest.mark.asyncio
async def test_button_contacts_come_from_the_receipts_of_the_last_30_days():
    client = MagicMock()
    chain = client.table.return_value.select.return_value.gte.return_value.order.return_value.range.return_value
    chain.execute.return_value = MagicMock(
        data=[{"id": "c9", "visitor_phone": "+14155550123", "visitor_email": "mara@example.com"}], count=1)
    with patch.object(sb, "get_client", return_value=client), \
            patch.object(sb, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())):
        rows = await _real_button_rows(days=30)
    assert rows == [{"visitor_phone": "+14155550123", "visitor_email": "mara@example.com"}]
    col, cutoff = client.table.return_value.select.return_value.gte.call_args.args
    assert col == "metadata->crm_lead->>at", "the receipt's own time, not the chat's"
    from datetime import datetime, timedelta, timezone
    assert abs(datetime.fromisoformat(cutoff) - (datetime.now(timezone.utc) - timedelta(days=30))).total_seconds() < 60


@pytest.mark.asyncio
async def test_button_contacts_that_cannot_be_read_are_none():
    with patch.object(sb, "get_client", side_effect=RuntimeError("SSL EOF")):
        assert await _real_button_rows() is None
    a, b = _db_returning(MagicMock(data=[], count=None))
    with a, b:
        assert await _real_button_rows() is None, "no count: the list cannot be proved complete"
    a, b = _db_returning(MagicMock(data=[], count=0))
    with a, b:
        assert await _real_button_rows() == []


@pytest.mark.asyncio
async def test_the_orphan_page_starts_at_its_offset():
    client = MagicMock()
    ordered = client.table.return_value.select.return_value.gte.return_value.or_.return_value.lt.return_value \
        .order.return_value.order.return_value
    ordered.range.return_value.execute.return_value = MagicMock(data=[{"id": "l1"}])
    with patch.object(sb, "get_client", return_value=client), \
            patch.object(sb, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())):
        rows = await sb.get_crm_orphan_leads(limit=50, offset=100)
    assert rows == [{"id": "l1"}]
    ordered.range.assert_called_once_with(100, 149)
    assert client.table.return_value.select.return_value.gte.return_value.or_.return_value.lt.return_value \
        .order.call_args.args == ("created_at",), "newest first, then by id so a page boundary is stable"
