"""One client, six CRM records. Nobody wrote a bug — four reasonable decisions
formed a circle, and these tests pin every cut we made through it.

  1. mark_lead_created_in_crm returned None for two different things — an
     exception (logged) and an UPDATE that matched no row (silent). The silent
     one is now loud.
  2. Two of its four callers ignored the return value; the pipeline was one of
     them, at the exact moment the client confirms. All four check now.
  3. The abandoned sweep ran from TWO schedulers with one per-process lock; the
     "job idempotency" covering the overlap was the very flag that failed to
     write. One source now, plus a switch.
  4. The default departure date was clock+30d, fresh on every push — four
     records, four dates, none the client's. Anchored to the conversation now.
  5. A per-process day guard caps the damage when the flag write dies anyway.
"""

import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoieCJ9.sig")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import cron
from app.db import supabase as sb
from app.services.crm import build_crm_payload


# ══════════════════════════════════════════════════════════════
# 1 — the mark never stays silent again
# ══════════════════════════════════════════════════════════════

class _ZeroRowTable:
    def table(self, _n):
        return self

    def update(self, _p):
        return self

    def eq(self, *_a):
        return self

    def execute(self):
        return MagicMock(data=[])


@pytest.mark.asyncio
async def test_update_matching_no_row_screams(caplog):
    with patch.object(sb, "get_client", return_value=_ZeroRowTable()), \
         patch.object(sb, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())), \
         caplog.at_level(logging.ERROR):
        out = await sb.mark_lead_created_in_crm("lead-1", crm_lead_id="crm-9")
    assert out is None
    assert any("matched NO ROW" in r.message for r in caplog.records), \
        "silence here is how one client got six CRM records"


# ══════════════════════════════════════════════════════════════
# 2-3 — the pipeline checks (source contract: the block is deep in _pipeline)
# ══════════════════════════════════════════════════════════════

def test_pipeline_only_counts_submitted_when_the_mark_succeeded():
    import pathlib

    src = (pathlib.Path(__file__).resolve().parent.parent
           / "app/pipeline/orchestrator.py").read_text(encoding="utf-8")
    i = src.index("_marked = await db.mark_lead_created_in_crm(_lead_fresh")
    block = src[i:i + 1400]
    assert "if _marked is None:" in block
    assert "flag write FAILED" in block
    # submitted flips ONLY in the else branch — a lead we cannot mark is a
    # lead the crons must be told about.
    assert block.index("flag write FAILED") < block.index("_crm_submitted_this_turn = True")
    assert "else:" in block[:block.index("_crm_submitted_this_turn = True")]


# ══════════════════════════════════════════════════════════════
# 4 — AAA backfill: a failed mark is a skip, not a success
# ══════════════════════════════════════════════════════════════

def test_aaa_backfill_skips_on_failed_mark():
    import inspect

    src = inspect.getsource(cron.run_aaa_backfill)
    i = src.index("_marked = await db.mark_lead_created_in_crm(")
    block = src[i:i + 700]
    assert "if _marked is None:" in block
    assert "manual reconciliation needed" in block
    assert block.index("skipped += 1") < block.index("pushed += 1"), \
        "the failure path must not count as pushed"


# ══════════════════════════════════════════════════════════════
# 5-6 — the day guard: per-process, resets on date change
# ══════════════════════════════════════════════════════════════

class TestDayGuard:
    def test_second_push_same_day_is_blocked(self):
        cron._pushed_today = set()
        cron._pushed_today_date = ""
        assert cron._day_guard_blocks("c1") is False
        cron._pushed_today.add("c1")
        assert cron._day_guard_blocks("c1") is True
        assert cron._day_guard_blocks("c2") is False

    def test_the_guard_resets_when_the_day_changes(self):
        cron._pushed_today = {"c1"}
        cron._pushed_today_date = "2026-08-20"   # yesterday, from this test's view
        assert cron._day_guard_blocks("c1") is False, "a new day starts clean"
        assert cron._pushed_today == set() or "c1" not in cron._pushed_today

    def test_both_crons_consult_it_before_fetching_the_lead(self):
        import inspect

        for fn in (cron.run_aaa_backfill, cron.run_abandoned_crm):
            src = inspect.getsource(fn)
            guard = src.index("_day_guard_blocks(cid)")
            fetch = src.index("get_or_create_lead(cid)")
            assert guard < fetch, f"{fn.__name__}: guard must come first"

    def test_successful_pushes_are_recorded(self):
        import inspect

        assert "_pushed_today.add(cid)" in inspect.getsource(cron.run_aaa_backfill)
        assert "_pushed_today.add(cid)" in inspect.getsource(cron.run_abandoned_crm)


# ══════════════════════════════════════════════════════════════
# 7 — the switch
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_disabled_sweep_never_touches_the_database():
    from config.settings import settings

    db_call = AsyncMock()
    with patch.object(settings, "abandoned_cron_enabled", False), \
         patch.object(cron.db, "get_abandoned_conversations", db_call):
        out = await cron.run_abandoned_crm()
    assert out == {"disabled": True}
    db_call.assert_not_awaited()


def test_the_actions_schedule_is_off_but_manual_stays():
    import pathlib

    wf = (pathlib.Path(__file__).resolve().parent.parent.parent
          / ".github/workflows/abandoned-crm.yml").read_text(encoding="utf-8")
    assert "# schedule:" in wf, "the scheduled run must stay commented out"
    assert "workflow_dispatch" in wf, "manual runs stay possible"
    active_schedule = [
        ln for ln in wf.splitlines()
        if ln.strip().startswith("schedule:") and not ln.strip().startswith("#")
    ]
    assert active_schedule == [], "two schedulers and one per-process lock is the incident"


# ══════════════════════════════════════════════════════════════
# 8 — the default date is a fact about the conversation, not the clock
# ══════════════════════════════════════════════════════════════

def _lead():
    return {"origin_code": "HNL", "destination_code": "AKL",
            "departure_date": None, "return_date": None}


def _visitor():
    return SimpleNamespace(name="Kathryn", email="k@x.com", phone="+18282174558")


def test_default_departure_is_stable_across_pushes():
    meta = {"conversation_created_at": "2026-08-17T05:42:29+00:00"}
    p1 = build_crm_payload(_lead(), _visitor(), conv_metadata=meta, allow_defaults=True)
    p2 = build_crm_payload(_lead(), _visitor(), conv_metadata=meta, allow_defaults=True)
    d1 = p1["flights"][0]["date"]
    d2 = p2["flights"][0]["date"]
    assert d1 == d2 == "2026-09-16", \
        "17 Aug + 30d, every single time — never a fresh clock+30d"


def test_no_anchor_still_defaults_without_crashing():
    p = build_crm_payload(_lead(), _visitor(), conv_metadata={}, allow_defaults=True)
    assert p is not None


# ══════════════════════════════════════════════════════════════
# 9 — non-regression: the two callers that always checked, still check
# ══════════════════════════════════════════════════════════════

def test_backstop_and_sweep_still_check_the_mark():
    import inspect
    import pathlib

    crm_src = (pathlib.Path(__file__).resolve().parent.parent
               / "app/services/crm.py").read_text(encoding="utf-8")
    assert "marked" in crm_src and "mark_lead_created_in_crm" in crm_src

    sweep = inspect.getsource(cron.run_abandoned_crm)
    assert "if marked is None:" in sweep
    assert "manual reconciliation needed" in sweep
