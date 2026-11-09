"""CodeRabbit #227, full review (9 nov 2026), checked against the code.

Each section is one finding: what the code did, and the path that must not
change (rule 5)."""

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import lessons as lessons_api  # noqa: E402
from app.services import learning  # noqa: E402

_VID = "11111111-2222-3333-4444-555555555555"
_OWNER = {"id": "u0", "role": "owner"}


def _sync(fn, **_kw):
    return fn()


class _Table:
    """One table, answered the way PostgREST does: at most 1,000 rows per
    response, whatever .range() asks for."""

    def __init__(self, rows):
        self.rows = rows
        self.ranges: list[tuple[int, int]] = []
        self.updates: list[dict] = []
        self._slice = None

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *_a):
        return self

    def range(self, lo, hi):
        self.ranges.append((lo, hi))
        self._slice = self.rows[lo:min(hi + 1, lo + 1000)]
        return self

    def update(self, payload):
        self.updates.append(payload)
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows[:1000] if self._slice is None else self._slice)


class _Client:
    def __init__(self, **tables):
        self.tables = {name: _Table(rows) for name, rows in tables.items()}

    def table(self, name):
        return self.tables[name]


# ── Learning reads every outcome, not the first 1,000 (Major) ─────────────

def _outcomes(n_yes, n_no, **extra):
    return ([{"id": f"y{i}", "answered": True, **extra} for i in range(n_yes)]
            + [{"id": f"n{i}", "answered": False, **extra} for i in range(n_no)])


@pytest.mark.asyncio
async def test_variant_stats_count_every_page_of_outcomes():
    client = _Client(question_variants=[{"id": "v1", "slot": "route", "text": "From where?", "status": "active"}],
                     question_outcomes=_outcomes(1000, 500, variant_id="v1"))
    with patch("app.db.supabase._run_sync", new=AsyncMock(side_effect=_sync)), \
         patch("app.db.supabase.get_client", lambda: client):
        out = await learning.get_question_variants()
    assert out[0]["shows"] == 1500 and out[0]["answered"] == 1000  # one page alone said 1000 / 1000
    assert client.tables["question_outcomes"].ranges == [(0, 999), (1000, 1999)]


@pytest.mark.asyncio
async def test_report_counts_every_page_of_outcomes():
    client = _Client(question_outcomes=_outcomes(700, 500, slot="route", control=False))
    with patch("app.db.supabase._run_sync", new=AsyncMock(side_effect=_sync)), \
         patch("app.db.supabase.get_client", lambda: client):
        rep = await learning.build_learning_report()
    assert rep["treatment_n"] == 1200 and rep["treatment_answer_rate"] == round(700 / 1200, 3)
    assert "error" not in rep


@pytest.mark.asyncio
async def test_a_short_first_page_is_the_whole_read():
    client = _Client(question_outcomes=_outcomes(3, 1, slot="route", control=True))
    with patch("app.db.supabase._run_sync", new=AsyncMock(side_effect=_sync)), \
         patch("app.db.supabase.get_client", lambda: client):
        rep = await learning.build_learning_report()
    assert rep["control_n"] == 4 and client.tables["question_outcomes"].ranges == [(0, 999)]


# ── a variant update: retired_at follows the status; failure is not 404 ───

@pytest.mark.asyncio
@pytest.mark.parametrize("status, retired", [("retired", True), ("active", False), ("proposed", False)])
async def test_retired_at_is_set_on_retiring_and_cleared_otherwise(status, retired):
    client = _Client(question_variants=[{"id": _VID}])
    with patch("app.db.supabase._run_sync", new=AsyncMock(side_effect=_sync)), \
         patch("app.db.supabase.get_client", lambda: client):
        assert await learning.set_question_variant_status(_VID, status) is True
    payload = client.tables["question_variants"].updates[0]
    assert payload["status"] == status
    assert isinstance(payload["retired_at"], str) if retired else payload["retired_at"] is None


@pytest.mark.asyncio
async def test_a_failed_variant_update_raises_and_no_row_is_false():
    with patch("app.db.supabase._run_sync", new=AsyncMock(side_effect=RuntimeError("connection reset"))), \
         patch("app.db.supabase.get_client", lambda: _Client(question_variants=[])):
        with pytest.raises(RuntimeError):
            await learning.set_question_variant_status(_VID, "active")
    with patch("app.db.supabase._run_sync", new=AsyncMock(side_effect=_sync)), \
         patch("app.db.supabase.get_client", lambda: _Client(question_variants=[])):
        assert await learning.set_question_variant_status(_VID, "active") is False


async def _patch_variant(variant_id, effect):
    body = lessons_api.VariantStatusUpdate(status="active")
    with patch("app.services.learning.set_question_variant_status", new=AsyncMock(side_effect=effect)) as upd:
        try:
            return await lessons_api.update_question_variant(variant_id, body, user=_OWNER), None, upd
        except HTTPException as e:
            return None, e, upd


@pytest.mark.asyncio
async def test_the_endpoint_says_502_when_the_update_fails_and_404_when_no_variant():
    _, err, _ = await _patch_variant(_VID, RuntimeError("down"))
    assert err.status_code == 502
    _, err, _ = await _patch_variant(_VID, [False])
    assert err.status_code == 404
    res, err, _ = await _patch_variant(_VID, [True])
    assert err is None and res["success"] is True


@pytest.mark.asyncio
async def test_a_malformed_variant_id_is_404_without_a_database_call():
    _, err, upd = await _patch_variant("not-a-uuid", AssertionError("no call"))
    assert err.status_code == 404 and upd.await_count == 0


# ── the daily learning run keeps the event loop free (Major) ──────────────

@pytest.mark.asyncio
async def test_the_learning_run_never_blocks_the_event_loop():
    """MAP and REDUCE are sync model calls of seconds each. Run on the loop,
    they froze every chat, /health and the other scheduler jobs meanwhile."""
    import asyncio
    import time

    def _slow_model(*_a, task="learn", **_k):
        time.sleep(0.25)
        return ('{"lessons": []}' if task == "learn_reduce" else '{"killer_patterns": [], "winning_patterns": []}'), 0.01

    ticks = 0

    async def _ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    cards = [{"chat": 1, "tag": "abandoned", "segment": "sales/direct"}]
    patches = {
        "collect_cards": AsyncMock(return_value=cards), "get_lessons": AsyncMock(return_value=[]),
        "_save_run": AsyncMock(), "_recent_runs": AsyncMock(return_value=[]), "_prune_runs": AsyncMock(),
        "_prior_run_today": AsyncMock(return_value=None), "_approved_lesson_ids": AsyncMock(return_value=[]),
        "merge_lessons": AsyncMock(return_value=(0, 0)), "call_sonnet_learning": _slow_model,
    }
    with patch.multiple(learning, **patches):
        t = asyncio.create_task(_ticker())
        result = await learning.run_learning()
        t.cancel()
    assert result["status"] == "ok"
    # Two calls of 0.25 s: on the loop the ticker got ~0 turns, in a thread ~50.
    assert ticks >= 20


# ── the reward loop: a failure is counted and logged, not hidden at DEBUG ──

class _ApiError(Exception):
    """The shape of postgrest's APIError: code, message and the details,
    which can quote the refused row."""

    def __init__(self, code, message="boom", details=None):
        super().__init__(f"{code} {message} {details}")
        self.code, self.message, self.details = code, message, details


@pytest.fixture
def _reward_loop(monkeypatch):
    from app import flags
    from app.learning import outcomes

    flags._reset_for_tests()
    outcomes._reset_for_tests()
    monkeypatch.setenv("LEARNING_ENABLED", "true")
    yield outcomes
    flags._reset_for_tests()


def _warnings(caplog):
    import logging

    return [r for r in caplog.records if r.name == "app.learning.outcomes" and r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_a_failed_reward_write_is_counted_and_logged_without_the_row(_reward_loop, caplog):
    err = _ApiError("42501", "permission denied", details="Failing row contains (Ana, +1 555 014 2290)")
    with patch("app.db.supabase._run_sync", new=AsyncMock(side_effect=err)), \
         patch("app.db.supabase.get_client", lambda: None), \
         caplog.at_level("DEBUG", logger="app.learning.outcomes"):
        await _reward_loop.record_ask("c1", "m1", "route", "v1")
    assert _reward_loop.OUTCOMES_HEALTH["record_failed"] == 1
    [w] = _warnings(caplog)
    assert "record_ask failed" in w.getMessage() and "42501" in w.getMessage()
    assert "555" not in caplog.text and "Ana" not in caplog.text


@pytest.mark.asyncio
async def test_a_missing_table_is_counted_apart_and_stays_at_debug(_reward_loop, caplog):
    with patch("app.db.supabase._run_sync", new=AsyncMock(side_effect=_ApiError("PGRST205", "table not found"))), \
         patch("app.db.supabase.get_client", lambda: None), \
         caplog.at_level("DEBUG", logger="app.learning.outcomes"):
        await _reward_loop.record_ask("c1", "m1", "route", "v1")
        assert await _reward_loop.resolve_outcomes("c1", lead={}) == 0
    assert _reward_loop.OUTCOMES_HEALTH == {"record_failed": 0, "resolve_failed": 0, "table_missing": 2}
    assert _warnings(caplog) == []


@pytest.mark.asyncio
async def test_a_failed_resolve_is_counted_and_nothing_to_resolve_is_not(_reward_loop):
    with patch("app.db.supabase._run_sync", new=AsyncMock(side_effect=_ApiError("57014", "statement timeout"))), \
         patch("app.db.supabase.get_client", lambda: None):
        assert await _reward_loop.resolve_outcomes("c1", lead={}) == 0
    assert _reward_loop.OUTCOMES_HEALTH["resolve_failed"] == 1
    with patch("app.db.supabase._run_sync", new=AsyncMock(return_value=SimpleNamespace(data=[]))), \
         patch("app.db.supabase.get_client", lambda: None):
        assert await _reward_loop.resolve_outcomes("c1", lead={}) == 0
    assert _reward_loop.OUTCOMES_HEALTH["resolve_failed"] == 1


@pytest.mark.asyncio
async def test_health_detail_shows_the_reward_loop_counters():
    from app.api.health import health_detail

    with patch("app.api.health.get_scheduler_health", return_value={}), \
         patch("app.api.health.supervisor_columns_status", return_value=True):
        payload = await health_detail()
    assert set(payload["learning_outcomes"]) == {"record_failed", "resolve_failed", "table_missing"}
