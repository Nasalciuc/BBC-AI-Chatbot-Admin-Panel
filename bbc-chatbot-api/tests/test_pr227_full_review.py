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


# ── the fallback notice (FALLBACK_MSG_ONCE): an unknown status is silence ──

async def _fall_back(second_read, monkeypatch):
    """fall_back_to_ai for a chat an agent never answered; the second status
    read (the one FALLBACK_MSG_ONCE makes) answers `second_read`."""
    from unittest.mock import MagicMock

    from app import flags
    from app.services import handoff

    flags._reset_for_tests()
    monkeypatch.setenv("FALLBACK_MSG_ONCE", "true")
    handoff.HANDOFF_HEALTH["notice_held_status_unknown"] = 0
    conv = {"id": "c1", "assigned_agent_id": "a7", "mode": "human", "status": "active",
            "metadata": {"announce_pending": False}}
    claim = AsyncMock(return_value=True)
    send = AsyncMock(return_value={"id": "m1"})
    with patch.object(handoff.db, "get_conversation_simple", new=AsyncMock(side_effect=[conv, second_read])), \
         patch.object(handoff, "_release_from_agent", new=AsyncMock(return_value=True)), \
         patch.object(handoff, "_handoff_phrase_recently_sent", new=AsyncMock(return_value=True)), \
         patch.object(handoff, "_claim_fallback_notice", new=claim), \
         patch.object(handoff, "_safe_system_msg", new=send), \
         patch.object(handoff.manager, "push", new=AsyncMock()), \
         patch.object(handoff.db, "get_recent_messages", new=AsyncMock(return_value=[])), \
         patch("app.services.presence.log_activity", new=MagicMock()), \
         patch("app.pipeline.orchestrator._fire_and_forget", new=MagicMock()):
        await handoff.fall_back_to_ai("c1")
    flags._reset_for_tests()
    return claim, send, handoff.HANDOFF_HEALTH["notice_held_status_unknown"]


@pytest.mark.asyncio
async def test_a_failed_status_read_sends_no_notice_and_is_counted(monkeypatch):
    claim, send, held = await _fall_back(None, monkeypatch)
    assert claim.await_count == 0 and send.await_count == 0 and held == 1


@pytest.mark.asyncio
async def test_a_closed_chat_still_gets_no_notice(monkeypatch):
    claim, send, held = await _fall_back({"id": "c1", "status": "closed"}, monkeypatch)
    assert claim.await_count == 0 and send.await_count == 0 and held == 0


@pytest.mark.asyncio
async def test_an_open_chat_still_gets_the_notice(monkeypatch):
    claim, send, held = await _fall_back({"id": "c1", "status": "active"}, monkeypatch)
    assert claim.await_count == 1 and send.await_count == 1 and held == 0


# ── the startup budget seed is bounded ────────────────────────────────────

@pytest.fixture
def _ledger():
    from app.ai import ledger

    ledger._reset_for_tests()
    saved = dict(ledger._budget)
    yield ledger
    ledger._budget.update(saved)
    ledger._reset_for_tests()


@pytest.mark.asyncio
async def test_a_slow_seed_gives_up_and_keeps_the_in_memory_total(_ledger):
    import asyncio
    import time

    async def _slow(*_a, **_k):
        await asyncio.sleep(5)

    _ledger._budget.update(day=_ledger._today_utc(), total=1.25)
    started = time.monotonic()
    with patch("app.db.supabase._run_sync", new=_slow), patch("app.db.supabase.get_client", lambda: None):
        await _ledger.seed_budget_bounded(timeout=0.05)
    assert time.monotonic() - started < 1
    assert _ledger.LEDGER_HEALTH["budget_seed"] == "timed_out"
    assert _ledger._budget["total"] == 1.25


@pytest.mark.asyncio
async def test_a_quick_seed_still_seeds_and_a_failed_one_says_so(_ledger):
    with patch("app.db.supabase._run_sync", new=AsyncMock(return_value=SimpleNamespace(data=[{"cost_usd": 0.5}]))), \
         patch("app.db.supabase.get_client", lambda: None):
        await _ledger.seed_budget_bounded(timeout=5)
    assert _ledger.LEDGER_HEALTH["budget_seed"] == "seeded" and _ledger._budget["total"] >= 0.5
    with patch("app.db.supabase._run_sync", new=AsyncMock(side_effect=RuntimeError("down"))), \
         patch("app.db.supabase.get_client", lambda: None):
        await _ledger.seed_budget_bounded(timeout=5)
    assert _ledger.LEDGER_HEALTH["budget_seed"] == "failed"


def test_startup_uses_the_bounded_seed():
    import inspect

    from app import main

    src = inspect.getsource(main.startup)
    assert "seed_budget_bounded()" in src and "await ledger.seed_budget()" not in src


# ── a client message in human mode never waits on the operator card ───────

@pytest.mark.asyncio
async def test_the_queued_reply_does_not_wait_for_the_operator_card():
    import asyncio
    from unittest.mock import MagicMock

    from app.api.chat import chat
    from app.models.chat import ChatRequest, VisitorInfo
    from app.pipeline import orchestrator

    cid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    conv = {"id": cid, "status": "active", "mode": "human", "assigned_agent_id": "agent-1", "metadata": {}}
    started = asyncio.Event()
    never = asyncio.Event()

    async def _card(*_a):
        started.set()
        await never.wait()  # lead writes and the push, as slow as they like

    req = MagicMock(headers={}, client=MagicMock(host="127.0.0.1"))
    body = ChatRequest(message="JFK to London on June 1", conversation_id=cid, tunnel="sales",
                       visitor=VisitorInfo(name="Test"), visitor_id="visitor-uuid-1")
    with patch("app.api.chat.db.count_messages", new=AsyncMock(return_value=3)), \
         patch("app.api.chat.db.get_conversation_simple", new=AsyncMock(return_value=conv)), \
         patch("app.api.chat.lead_service.get_or_create_lead", new=AsyncMock(return_value=None)), \
         patch("app.api.chat.db.get_conversation_mode", new=AsyncMock(return_value="human")), \
         patch("app.services.handoff.is_agent_effectively_offline", new=AsyncMock(return_value=False)), \
         patch("app.services.conversation_service.add_message", new=AsyncMock(return_value={"id": "m1"})), \
         patch("app.services.handoff._handoff_phrase_recently_sent", new=AsyncMock(return_value=False)), \
         patch("app.pipeline.operator_card.extract_and_push", new=_card):
        resp = await asyncio.wait_for(chat(body, req, None), timeout=2)
        await asyncio.wait_for(started.wait(), timeout=1)  # it still runs, beside the answer
    assert resp.type == "queued"
    for t in list(orchestrator._background_tasks):
        t.cancel()


# ── ensure_one_question: the dead conditional is gone, the text is the same ─

@pytest.mark.parametrize("body, expected", [
    ("Got it, JFK to London.", "Got it, JFK to London. What date would you like to depart?"),
    ("Got it", "Got it What date would you like to depart?"),  # as before: one space, no stop added
    ("", "What date would you like to depart?"),
])
def test_the_slot_question_is_appended_after_one_space(body, expected):
    from app.pipeline import slots

    assert slots.ensure_one_question(body, "What date would you like to depart?") == expected
