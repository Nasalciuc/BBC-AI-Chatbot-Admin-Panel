"""One human, one CRM row — even across deploys and across conversations.

4 Nov 2026, 21:57 UTC: a widget-only merge (#230) restarted the API. The
first abandoned-CRM tick of the new process ran the "one-time" AAA backfill
again (the flag was per process), and it pushed 72 operator-handled chats
from the last 30 days with AAA defaults. The same thing happened on every
deploy since August (Sep 2, Sep 23, Sep 28, Oct 14). Some were in the CRM
already: John Arana wrote twice on 15 Oct (same phone, an email typo); the
second chat reached the CRM that day, the first one on 4 Nov.

Fixed here:
  1. the abandoned sweep never starts the backfill;
  2. the backfill skips chats an agent closed (same rule as the live sweep);
  3. an automatic push checks whether the same human (phone OR email)
     already reached the CRM from another conversation in 30 days;
  4. a widget-only merge no longer redeploys the API (railway.toml).
"""

import os
import sys
import tomllib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoieCJ9.sig")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import cron
from app.db import supabase as sb
from app.services.crm import CRM_PUSH_HEALTH, CRMResult

# Captured at import, before conftest's autouse patch replaces it.
from app.db.supabase import get_crm_contact_rows as _real_get_crm_contact_rows

_API_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _conv(cid="c-2748", phone="+18178515042", email="jpajr@sbcglobal.net", **kw):
    base = {
        "id": cid, "status": "active", "mode": "ai", "tunnel": "sales",
        "visitor_name": "John", "visitor_email": email, "visitor_phone": phone,
        "visitor_id": "v1", "metadata": {}, "message_count": 6,
        "created_at": "2026-10-15T18:53:00+00:00",
        "last_user_message_at": "2026-10-15T19:00:00+00:00",
    }
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def _fresh_state():
    cron._pushed_today = set()
    cron._pushed_today_date = ""
    before = dict(CRM_PUSH_HEALTH)
    yield
    cron._pushed_today = set()
    cron._pushed_today_date = ""
    CRM_PUSH_HEALTH.clear()
    CRM_PUSH_HEALTH.update(before)


async def _sweep(convs, crm_rows, lead=None):
    lead = lead if lead is not None else {"id": "L1", "created_in_crm": False}
    with patch.object(cron.db, "get_abandoned_conversations",
                      new=AsyncMock(return_value=convs)), \
         patch.object(cron.db, "get_crm_contact_rows",
                      new=AsyncMock(return_value=crm_rows)) as lookup, \
         patch.object(cron, "get_or_create_lead", new=AsyncMock(return_value=lead)), \
         patch.object(cron, "submit_abandoned_to_crm",
                      new=AsyncMock(return_value=CRMResult(success=True, request_id="crm-1"))) as submit, \
         patch.object(cron.db, "mark_lead_created_in_crm",
                      new=AsyncMock(return_value={"id": "L1"})), \
         patch.object(cron.db, "update_conversation", new=AsyncMock()), \
         patch.object(cron.db, "update_lead_crm_push_state", new=AsyncMock()) as state:
        out = await cron.run_abandoned_crm()
    return out, submit, state, lookup


# ════════════════════════════════════════════════════════════
# 1 · a deploy is not a reason to push anything
# ════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_the_sweep_never_starts_the_backfill():
    with patch.object(cron, "run_aaa_backfill", new=AsyncMock()) as backfill, \
         patch.object(cron.db, "get_abandoned_conversations", new=AsyncMock(return_value=[])):
        await cron.run_abandoned_crm()
        await cron.run_abandoned_crm()
    backfill.assert_not_awaited()


def test_no_per_process_backfill_flag_is_left():
    assert not hasattr(cron, "_AAA_BACKFILL_RAN")


# ════════════════════════════════════════════════════════════
# 2 · the backfill respects the agent who closed the chat
# ════════════════════════════════════════════════════════════

def test_backfill_select_skips_agent_closed_chats():
    with open(os.path.join(_API_ROOT, "app", "db", "supabase.py")) as f:
        src = f.read()
    start = src.index("async def get_recent_contact_conversations")
    body = src[start:src.index("async def get_crm_contact_rows")]
    assert '.is_("metadata->>closed_by_agent_id", "null")' in body


# ════════════════════════════════════════════════════════════
# 3 · the same human from another conversation
# ════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_same_phone_already_in_crm_is_held_not_pushed():
    """John Arana: same phone, the email has a typo on the twin."""
    out, submit, state, _ = await _sweep(
        [_conv()],
        crm_rows=[{"visitor_phone": "+18178515042", "visitor_email": "jpajr@sncglobal.net"}],
    )
    submit.assert_not_awaited()
    state.assert_awaited_once_with("L1", gate_reason="contact_already_in_crm")
    assert out["results"][0]["status"] == "contact_already_in_crm"
    assert CRM_PUSH_HEALTH["contact_already_in_crm"] >= 1


@pytest.mark.asyncio
async def test_same_email_any_case_is_held_too():
    out, submit, _, _ = await _sweep(
        [_conv(phone="+13105550111", email="Someone@Example.com")],
        crm_rows=[{"visitor_phone": "", "visitor_email": "someone@example.com"}],
    )
    submit.assert_not_awaited()
    assert out["results"][0]["status"] == "contact_already_in_crm"


@pytest.mark.asyncio
async def test_a_new_human_is_still_pushed():
    """The branch that does NOT hold: nobody with this phone or email."""
    out, submit, state, _ = await _sweep(
        [_conv()],
        crm_rows=[{"visitor_phone": "+13105550111", "visitor_email": "other@example.com"}],
    )
    submit.assert_awaited_once()
    state.assert_not_awaited()
    assert out["results"][0]["status"] == "success"


@pytest.mark.asyncio
async def test_failed_lookup_waits_a_tick_and_freezes_nothing():
    """A failed read is not an empty CRM: no push, and no 'refused' state
    either — the next tick tries again."""
    before = CRM_PUSH_HEALTH["crm_contact_lookup_failed"]
    out, submit, state, _ = await _sweep([_conv()], crm_rows=None)
    submit.assert_not_awaited()
    state.assert_not_awaited()
    assert out["results"][0]["reason"] == "crm_lookup_failed"
    assert CRM_PUSH_HEALTH["crm_contact_lookup_failed"] == before + 1


@pytest.mark.asyncio
async def test_lookup_is_skipped_when_nothing_reaches_the_push():
    _, submit, _, lookup = await _sweep(
        [_conv(status="closed")], crm_rows=[],
        lead={"id": "L1", "created_in_crm": True},
    )
    submit.assert_not_awaited()
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_backfill_holds_a_human_already_in_crm():
    with patch.object(sb, "get_recent_contact_conversations",
                      new=AsyncMock(return_value=[_conv()])), \
         patch.object(sb, "get_crm_contact_rows",
                      new=AsyncMock(return_value=[{"visitor_phone": "+18178515042"}])), \
         patch.object(cron, "get_or_create_lead",
                      new=AsyncMock(return_value={"id": "L1", "created_in_crm": False})), \
         patch.object(sb, "update_lead_crm_push_state", new=AsyncMock()) as state, \
         patch.object(cron, "submit_abandoned_to_crm", new=AsyncMock()) as submit:
        out = await cron.run_aaa_backfill()
    submit.assert_not_awaited()
    state.assert_awaited_once_with("L1", gate_reason="contact_already_in_crm")
    assert out["pushed"] == 0 and out["work_list"] == 1


@pytest.mark.asyncio
async def test_backfill_pushes_nothing_when_the_lookup_fails():
    with patch.object(sb, "get_recent_contact_conversations",
                      new=AsyncMock(return_value=[_conv()])), \
         patch.object(sb, "get_crm_contact_rows", new=AsyncMock(return_value=None)), \
         patch.object(cron, "get_or_create_lead",
                      new=AsyncMock(return_value={"id": "L1", "created_in_crm": False})), \
         patch.object(cron, "submit_abandoned_to_crm", new=AsyncMock()) as submit:
        out = await cron.run_aaa_backfill()
    submit.assert_not_awaited()
    assert out["pushed"] == 0 and out["skipped"] == 1


# ════════════════════════════════════════════════════════════
# the lookup itself: "nobody" and "could not look" are different
# ════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_lookup_returns_none_when_the_read_fails():
    with patch.object(sb, "get_client", side_effect=RuntimeError("SSL EOF")):
        assert await _real_get_crm_contact_rows() is None


@pytest.mark.asyncio
async def test_lookup_flattens_the_embedded_conversation():
    res = MagicMock(data=[{"id": "L1", "conversations": {"visitor_phone": "+1", "visitor_email": "a@b.c"}}],
                    count=1)
    with patch.object(sb, "get_client", return_value=MagicMock()), \
         patch.object(sb, "_run_sync", new=AsyncMock(return_value=res)):
        rows = await _real_get_crm_contact_rows()
    assert rows == [{"visitor_phone": "+1", "visitor_email": "a@b.c"}]


@pytest.mark.asyncio
async def test_lookup_empty_is_an_empty_list_not_none():
    with patch.object(sb, "get_client", return_value=MagicMock()), \
         patch.object(sb, "_run_sync", new=AsyncMock(return_value=MagicMock(data=[], count=0))):
        assert await _real_get_crm_contact_rows() == []


def _pages(total, server_max_rows):
    """Fake server: `total` rows, at most `server_max_rows` per response."""
    data = [{"id": f"L{i}", "conversations": {"visitor_phone": f"+1{i:09d}"}} for i in range(total)]
    offset = {"n": 0}

    async def run_sync(fn, **_kw):
        page = data[offset["n"]:offset["n"] + server_max_rows]
        offset["n"] += len(page)
        return MagicMock(data=page, count=total)

    return run_sync


@pytest.mark.asyncio
async def test_lookup_reads_past_the_servers_max_rows():
    """Supabase cuts any response to max-rows (1000 by default) without an
    error. 743 contacts on 5 Nov 2026 — one busy month from silent misses."""
    with patch.object(sb, "get_client", return_value=MagicMock()), \
         patch.object(sb, "_run_sync", new=_pages(total=2500, server_max_rows=1000)):
        rows = await _real_get_crm_contact_rows()
    assert len(rows) == 2500


@pytest.mark.asyncio
async def test_lookup_is_complete_even_when_max_rows_is_below_the_page():
    with patch.object(sb, "get_client", return_value=MagicMock()), \
         patch.object(sb, "_run_sync", new=_pages(total=7, server_max_rows=3)):
        rows = await _real_get_crm_contact_rows()
    assert len(rows) == 7


@pytest.mark.asyncio
async def test_lookup_over_the_cap_says_so(caplog):
    with patch.object(sb, "_CRM_CONTACT_SCAN_CAP", 4), \
         patch.object(sb, "_CRM_CONTACT_PAGE", 2), \
         patch.object(sb, "get_client", return_value=MagicMock()), \
         patch.object(sb, "_run_sync", new=_pages(total=9, server_max_rows=2)):
        rows = await _real_get_crm_contact_rows()
    assert len(rows) == 4
    assert any("only the newest 4 were loaded" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_lookup_without_a_count_cannot_prove_completeness():
    with patch.object(sb, "get_client", return_value=MagicMock()), \
         patch.object(sb, "_run_sync", new=AsyncMock(return_value=MagicMock(data=[], count=None))):
        assert await _real_get_crm_contact_rows() is None


# ════════════════════════════════════════════════════════════
# a manual run never overlaps the scheduler's tick
# ════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_manual_http_run_waits_for_the_scheduler_tick():
    import asyncio

    from app.services import scheduler
    from app.services.scheduler import job_lock

    calls = []

    async def fake_sweep():
        calls.append("http")
        return {"processed": 0, "success": 0, "results": []}

    request = MagicMock(headers={"Authorization": "Bearer s3cret"})
    # A lock of this test's own loop: a contended asyncio.Lock binds to its
    # loop, and the module-level one outlives this test.
    with patch.dict(scheduler._locks, {"abandoned_crm": asyncio.Lock()}), \
         patch.object(cron.settings, "cron_secret", "s3cret"), \
         patch.object(cron, "run_abandoned_crm", new=fake_sweep):
        lock = job_lock("abandoned_crm")
        await lock.acquire()
        try:
            task = asyncio.create_task(cron.process_abandoned_conversations(request))
            await asyncio.sleep(0.05)
            assert calls == [], "the HTTP run must wait while a tick holds the lock"
        finally:
            lock.release()
        out = await task
    assert calls == ["http"] and out["processed"] == 0


def test_scheduler_and_http_share_one_lock():
    from app.services.scheduler import job_lock

    assert job_lock("abandoned_crm") is job_lock("abandoned_crm")


# ════════════════════════════════════════════════════════════
# 4 · a widget merge does not restart the API
# ════════════════════════════════════════════════════════════

def test_railway_deploys_the_api_only_for_api_changes():
    with open(os.path.join(_API_ROOT, "railway.toml"), "rb") as f:
        cfg = tomllib.load(f)
    assert cfg["build"]["watchPatterns"] == ["/bbc-chatbot-api/**"]
