"""Dashboard stats: fetch once, filter per caller, never per panel."""

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.db import supabase as db  # noqa: E402
from app.db.supabase import _scope_rows  # noqa: E402

_CONVO_KEYS = {
    "id", "tunnel", "status", "visitor_name", "created_at", "closed_at", "assigned_agent_id",
}


def _reset_dash():
    db._DASH_RAW.update({"at": 0.0, "convos": [], "leads": [], "runs": [], "msgs": 0})
    db.DASHBOARD_CACHE_HEALTH.update({"hits": 0, "misses": 0, "single_flight_waits": 0})


def _chain():
    m = MagicMock()
    m.table.return_value = m
    m.select.return_value = m
    m.order.return_value = m
    m.limit.return_value = m
    m.execute.return_value = MagicMock(data=[], count=0)
    return m


def _sql_scope(convos, leads, runs, tunnel, agent):
    """Independent replica of the old SQL .eq() rules (not _scope_rows)."""
    c, l, r = [], [], []
    for row in convos:
        if tunnel and row.get("tunnel") != tunnel:
            continue
        if agent and row.get("assigned_agent_id") != agent:
            continue
        c.append(row)
    for row in leads:
        if tunnel and row.get("_tunnel") != tunnel:
            continue
        if agent and row.get("_agent") != agent:
            continue
        l.append(row)
    for row in runs:
        if tunnel and row.get("tunnel") != tunnel:
            continue
        # runs were filtered on tunnel only — never on agent
        r.append(row)
    return c, l, r


def test_scope_rows_matches_old_sql_for_owner_supervisor_sales():
    convos = [
        {"id": f"c{i}", "tunnel": "sales" if i % 2 == 0 else "support",
         "assigned_agent_id": "a1" if i % 3 == 0 else "a2"}
        for i in range(30)
    ]
    leads = [
        {"id": f"l{i}", "_tunnel": "sales" if i % 2 == 0 else "support",
         "_agent": "a1" if i % 5 == 0 else "a2"}
        for i in range(40)
    ]
    runs = [
        {"id": f"r{i}", "tunnel": "sales" if i < 12 else "support"}
        for i in range(20)
    ]
    cases = [
        (None, None),          # owner
        ("sales", None),       # supervisor
        ("sales", "a1"),       # sales
        ("support", "a2"),
    ]
    for tunnel, agent in cases:
        got_c = _scope_rows(convos, "tunnel", "assigned_agent_id", tunnel, agent)
        got_l = _scope_rows(leads, "_tunnel", "_agent", tunnel, agent)
        got_r = _scope_rows(runs, "tunnel", None, tunnel, agent)
        exp_c, exp_l, exp_r = _sql_scope(convos, leads, runs, tunnel, agent)
        assert got_c == exp_c, (tunnel, agent, "convos")
        assert got_l == exp_l, (tunnel, agent, "leads")
        assert got_r == exp_r, (tunnel, agent, "runs")
        if agent:
            unscoped_runs = _scope_rows(runs, "tunnel", None, tunnel, None)
            assert got_r == unscoped_runs


@pytest.mark.asyncio
async def test_six_expired_callers_single_flight_one_miss():
    _reset_dash()
    calls = {"n": 0}

    async def slow_run(fn, *a, **k):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        r = MagicMock()
        r.data = []
        r.count = 0
        return r

    with (
        patch("app.db.supabase.get_client", return_value=_chain()),
        patch("app.db.supabase._run_sync", side_effect=slow_run),
    ):
        first = asyncio.create_task(db.get_dashboard_stats())
        await asyncio.sleep(0.01)
        rest = [asyncio.create_task(db.get_dashboard_stats()) for _ in range(5)]
        await asyncio.gather(first, *rest)

    assert db.DASHBOARD_CACHE_HEALTH["misses"] == 1
    assert db.DASHBOARD_CACHE_HEALTH["single_flight_waits"] == 5
    assert calls["n"] == 4  # one 4-way gather


@pytest.mark.asyncio
async def test_second_call_under_ttl_is_a_hit_and_runs_nothing():
    _reset_dash()
    calls = {"n": 0}

    async def run(fn, *a, **k):
        calls["n"] += 1
        r = MagicMock()
        r.data = [{"id": "1", "tunnel": "sales", "status": "active",
                   "visitor_name": "A", "created_at": "2026-01-02T00:00:00Z",
                   "closed_at": None, "assigned_agent_id": None}]
        r.count = 0
        return r

    with (
        patch("app.db.supabase.get_client", return_value=_chain()),
        patch("app.db.supabase._run_sync", side_effect=run) as spy,
    ):
        await db.get_dashboard_stats()
        n_after_miss = calls["n"]
        await db.get_dashboard_stats()
        assert calls["n"] == n_after_miss
        assert db.DASHBOARD_CACHE_HEALTH["hits"] == 1
        spy.assert_awaited()  # the miss did call it


@pytest.mark.asyncio
async def test_cached_convos_carry_no_extra_pii():
    _reset_dash()

    async def run(fn, *a, **k):
        r = MagicMock()
        r.data = [{"id": "1", "tunnel": "sales", "status": "active",
                   "visitor_name": "A", "created_at": "2026-01-02T00:00:00Z",
                   "closed_at": None, "assigned_agent_id": None}]
        r.count = 0
        return r

    with (
        patch("app.db.supabase.get_client", return_value=_chain()),
        patch("app.db.supabase._run_sync", side_effect=run),
    ):
        await db.get_dashboard_stats()

    row = db._DASH_RAW["convos"][0]
    assert set(row.keys()) <= _CONVO_KEYS


def test_ordered_truncation_drops_the_oldest():
    """ORDER created_at DESC LIMIT 10000 — the row that does not fit is the oldest."""
    from datetime import datetime, timedelta, timezone
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    rows = [{"id": i, "created_at": (base + timedelta(seconds=i)).isoformat()} for i in range(10_001)]
    kept = sorted(rows, key=lambda r: r["created_at"], reverse=True)[:10_000]
    dropped = {r["id"] for r in rows} - {r["id"] for r in kept}
    oldest = min(rows, key=lambda r: r["created_at"])
    assert oldest["id"] in dropped
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "db", "supabase.py"), encoding="utf-8").read()
    assert src.count('.order("created_at", desc=True).limit(10000)') >= 3
    assert len(db._DASH_RAW["convos"]) <= 10_000


@pytest.mark.asyncio
async def test_new_labels_land_in_by_label():
    db.DB_HEALTH["by_label"].clear()
    await db._run_sync(lambda: 1, label="conv_simple")
    await db._run_sync(lambda: 1, label="presence")
    await db._run_sync(lambda: 1, label="cron")
    await db._run_sync(lambda: 1, label="pipeline")
    await db._run_sync(lambda: 1, label="messages")
    await db._run_sync(lambda: 1, label="dashboard")
    for label in ("conv_simple", "presence", "cron", "pipeline", "messages", "dashboard"):
        assert label in db.DB_HEALTH["by_label"], label
        assert db.DB_HEALTH["by_label"][label]["calls"] >= 1
