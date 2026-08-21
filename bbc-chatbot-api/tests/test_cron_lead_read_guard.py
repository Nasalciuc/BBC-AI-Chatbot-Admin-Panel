"""A failed read is not an absent lead.

Both created_in_crm guards in the abandoned sweep were conditioned on `lead`
being truthy. get_or_create_lead fails intermittently on SSL drops; when it
returned None both guards were skipped, the code fell through to the
recreate-and-push branch, and a lead correctly marked YESTERDAY got a second
CRM record because one query gave up for a second. Eight clients from the
hand-cleaned list of 207 were re-pushed on 21 Aug, 14:25-15:29 UTC.
"""

import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoieCJ9.sig")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import cron

_CID = "conv-guard-1"


def _conv(status="active"):
    return {
        "id": _CID, "status": status, "tunnel": "sales",
        "visitor_name": "Chris", "visitor_email": "c@x.com",
        "visitor_phone": "+18280000000", "chat_number": 1373,
        "visitor_id": "v1", "metadata": {}, "created_at": "2026-08-20T10:00:00+00:00",
        "last_user_message_at": "2026-08-20T10:05:00+00:00",
    }


@pytest.fixture(autouse=True)
def _fresh_day_guard():
    cron._pushed_today = set()
    cron._pushed_today_date = ""
    yield
    cron._pushed_today = set()
    cron._pushed_today_date = ""


def _sweep_patches(lead, convs=None, lead_raises=False):
    lead_mock = (
        AsyncMock(side_effect=RuntimeError("SSL EOF"))
        if lead_raises else AsyncMock(return_value=lead)
    )
    return {
        "abandoned": patch.object(
            cron.db, "get_abandoned_conversations",
            new=AsyncMock(return_value=convs if convs is not None else [_conv()])),
        "lead": patch.object(cron, "get_or_create_lead", lead_mock),
        "submit": patch("app.api.cron.submit_abandoned_to_crm",
                        new=AsyncMock()),
        "update": patch.object(cron.db, "update_conversation", new=AsyncMock()),
        "mark": patch.object(cron.db, "mark_lead_created_in_crm",
                             new=AsyncMock(return_value={"id": "l1"})),
        # The zombie-close path calls these against the real client with
        # retries; unmocked they cost ~15s of network timeouts per test.
        "claim_close": patch("app.services.closing.claim_closing_sent",
                             new=AsyncMock(return_value=True)),
        "add_msg": patch.object(cron.db, "add_message", new=AsyncMock()),
        "refusal": patch.object(cron, "_persist_refusal",
                                new=AsyncMock(return_value=True)),
    }


async def _run_sweep(ps):
    with ps["abandoned"], ps["lead"] as lead_mock, ps["submit"] as submit, \
         ps["update"], ps["mark"], ps["claim_close"], ps["add_msg"], ps["refusal"]:
        out = await cron.run_abandoned_crm()
    return out, submit, lead_mock


# ══════════════════════════════════════════════════════════════
# 1-2 · a failed read never reaches the CRM
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_lead_none_skips_the_cycle_and_never_pushes(caplog):
    ps = _sweep_patches(lead=None)
    with caplog.at_level(logging.WARNING):
        out, submit, _ = await _run_sweep(ps)
    submit.assert_not_awaited(), "a duplicate CRM record costs a consultant's call"
    assert any(r.get("reason") == "lead_read_failed" for r in out.get("results", [])
               if isinstance(r, dict))
    assert any("lead read failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_lead_read_exception_behaves_the_same(caplog):
    """get_or_create_lead swallows its own errors and returns None, but if it
    ever raises instead, the sweep's per-conversation try/except must still
    keep the push from happening."""
    ps = _sweep_patches(lead=None, lead_raises=True)
    with caplog.at_level(logging.WARNING):
        out, submit, _ = await _run_sweep(ps)
    submit.assert_not_awaited()


# ══════════════════════════════════════════════════════════════
# 3 · the recreate branch is gone
# ══════════════════════════════════════════════════════════════

def test_the_sweep_never_creates_leads_any_more():
    import inspect

    src = inspect.getsource(cron)
    assert "ensure_lead_for_conversation" not in src, (
        "creating a lead is the pipeline's job; the sweep recreating one "
        "after a failed read is exactly how the duplicates were minted"
    )


# ══════════════════════════════════════════════════════════════
# 4-5 · marked leads still behave: already_done / close-only
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_marked_lead_on_closed_conversation_is_already_done():
    ps = _sweep_patches(lead={"id": "l1", "created_in_crm": True},
                        convs=[_conv(status="closed")])
    out, submit, _ = await _run_sweep(ps)
    submit.assert_not_awaited()
    assert any(r.get("status") == "already_done" for r in out.get("results", [])
               if isinstance(r, dict))


@pytest.mark.asyncio
async def test_marked_lead_on_active_conversation_closes_without_pushing():
    ps = _sweep_patches(lead={"id": "l1", "created_in_crm": True},
                        convs=[_conv(status="active")])
    with ps["abandoned"], ps["lead"], ps["submit"] as submit, \
         ps["update"] as upd, ps["mark"], ps["claim_close"], ps["add_msg"], \
         ps["refusal"]:
        out = await cron.run_abandoned_crm()
    submit.assert_not_awaited(), "zombie cleanup is a close, never a push"
    assert upd.await_count >= 1, "the zombie conversation gets closed"


# ══════════════════════════════════════════════════════════════
# 6 · non-regression: a valid unmarked lead still pushes
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_valid_unmarked_lead_still_pushes():
    result = MagicMock(success=True, request_id="crm-1", error=None)
    ps = _sweep_patches(lead={"id": "l1", "created_in_crm": False,
                              "origin_code": "HNL", "destination_code": "AKL"})
    with ps["abandoned"], ps["lead"], \
         patch("app.api.cron.submit_abandoned_to_crm",
               new=AsyncMock(return_value=result)) as submit, \
         ps["update"], ps["mark"], ps["claim_close"], ps["add_msg"], \
         ps["refusal"]:
        out = await cron.run_abandoned_crm()
    submit.assert_awaited_once(), "the good path is untouched"


# ══════════════════════════════════════════════════════════════
# 7 · the backfill distinguishes the two skips
# ══════════════════════════════════════════════════════════════

def test_backfill_tells_read_failed_apart_from_already_pushed():
    import inspect

    src = inspect.getsource(cron.run_aaa_backfill)
    i = src.index("get_or_create_lead(cid)")
    block = src[i:i + 900]
    assert "if not lead:" in block
    assert "lead read failed" in block, "the silent conflation cost three days of hunting"
    assert 'if lead.get("created_in_crm"):' in block
    # read-failed logs a warning; already-in-CRM stays quiet — they differ.
    warn_at = block.index("lead read failed")
    crm_at = block.index('if lead.get("created_in_crm"):')
    assert warn_at < crm_at


# ══════════════════════════════════════════════════════════════
# 8 · #216's day guard is untouched
# ══════════════════════════════════════════════════════════════

def test_the_day_guard_from_216_still_runs_first():
    import inspect

    src = inspect.getsource(cron.run_abandoned_crm)
    guard = src.index("_day_guard_blocks(cid)")
    fetch = src.index("get_or_create_lead(cid)")
    assert guard < fetch, "the day guard still fires before the lead read"
