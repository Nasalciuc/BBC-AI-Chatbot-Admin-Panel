"""PR-6 — BudgetState tracks today's spend; llm.py builds 5.5 params and records."""

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.ai import ledger, llm
from config.settings import settings


@pytest.fixture(autouse=True)
def _clean():
    flags._reset_for_tests()
    ledger._reset_for_tests()
    yield
    ledger._reset_for_tests()


def test_budget_advances_and_fractions(monkeypatch):
    monkeypatch.setattr(settings, "daily_budget", 10.0)
    usage = SimpleNamespace(input_tokens=1000, output_tokens=1000,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0)
    # Sonnet 4.6: 1000*3/1e6 + 1000*15/1e6 = 0.018
    ledger.record_ai_call(task="sales_reply", model="claude-sonnet-4-6", usage=usage)
    assert 0 < ledger.today_cost() < 10
    assert ledger.budget_fraction() == ledger.today_cost() / 10.0


def test_budget_tracked_even_when_ledger_disabled(monkeypatch):
    monkeypatch.setenv("AI_LEDGER_ENABLED", "false")
    monkeypatch.setattr(settings, "daily_budget", 10.0)
    ledger.record_ai_call(task="x", model="claude-sonnet-4-6", cost=2.0)
    assert ledger.today_cost() == 2.0
    assert ledger.LEDGER_HEALTH["recorded"] == 0  # queue write skipped


def test_llm_builds_55_params_with_effort_not_temperature():
    p = llm._build_params("claude-sonnet-5-5", "sys", [{"role": "user", "content": "hi"}],
                          None, "medium", 1024, "1h")
    assert p["output_config"] == {"effort": "medium"}
    assert "temperature" not in p
    assert p["max_tokens"] >= 512
    assert p["system"][0]["cache_control"]["ttl"] == "1h"


def test_llm_builds_4x_params_with_temperature():
    p = llm._build_params("claude-sonnet-4-6", "sys", [{"role": "user", "content": "hi"}],
                          None, "low", 400, "5m")
    assert "output_config" not in p and p["temperature"] == 0.6


@pytest.mark.asyncio
async def test_llm_call_records_cost_and_parses_blocks(monkeypatch):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0)
    resp = SimpleNamespace(
        usage=usage, stop_reason="end_turn", id="req_1",
        content=[SimpleNamespace(type="text", text="hello there")],
    )

    class _Client:
        class messages:
            @staticmethod
            async def create(**kw):
                return resp

    monkeypatch.setattr(llm, "get_async_client", lambda: _Client())
    out = await llm.call("sales_reply", model="claude-sonnet-5-5", system="s",
                         messages=[{"role": "user", "content": "hi"}])
    assert out.text == "hello there" and out.cost > 0
    assert ledger.LEDGER_HEALTH["by_task"]["sales_reply"]["calls"] == 1


@pytest.mark.asyncio
async def test_llm_refusal_returns_empty(monkeypatch):
    usage = SimpleNamespace(input_tokens=10, output_tokens=0,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0)
    resp = SimpleNamespace(usage=usage, stop_reason="refusal", id="r", content=[])

    class _Client:
        class messages:
            @staticmethod
            async def create(**kw):
                return resp

    monkeypatch.setattr(llm, "get_async_client", lambda: _Client())
    out = await llm.call("sales_reply", model="claude-sonnet-5-5", system="s",
                         messages=[{"role": "user", "content": "hi"}])
    assert out.text == "" and out.stop_reason == "refusal"


def test_llm_strips_temperature_for_opus_4_8():
    # Same denylist as the sync path: opus-4-8 answers 400 to a non-default temperature.
    p = llm._build_params("claude-opus-4-8", "sys", [{"role": "user", "content": "hi"}],
                          None, "low", 400, "5m")
    assert "temperature" not in p and "output_config" not in p
    assert p["max_tokens"] == 400


def _paged_client(pages):
    """A fake supabase client whose ai_calls select returns pages by .range()."""
    seen = []

    class _Q:
        def __init__(self):
            self._start = 0
        def select(self, *a): return self
        def gte(self, *a): return self
        def order(self, *a): return self
        def range(self, start, end):
            self._start = start
            seen.append((start, end))
            return self
        def execute(self):
            idx = self._start // ledger._SEED_PAGE
            return SimpleNamespace(data=pages[idx] if idx < len(pages) else [])

    class _C:
        def table(self, *a): return _Q()

    return _C(), seen


@pytest.mark.asyncio
async def test_seed_budget_sums_every_page():
    full = [{"cost_usd": 0.01}] * ledger._SEED_PAGE
    last = [{"cost_usd": 0.5}, {"cost_usd": 0.5}]
    client, seen = _paged_client([full, full, last])

    async def _run(fn, idempotent=True, *, label="other"):
        return fn()

    with patch("app.db.supabase.get_client", lambda: client), patch("app.db.supabase._run_sync", _run):
        await ledger.seed_budget()
    assert len(seen) == 3
    assert ledger.today_cost() == round(2 * ledger._SEED_PAGE * 0.01 + 1.0, 6)
    assert ledger.LEDGER_HEALTH["budget_durable"] is True


@pytest.mark.asyncio
async def test_seed_budget_keeps_calls_recorded_meanwhile():
    ledger.record_ai_call(task="x", model="claude-sonnet-4-6", cost=5.0)
    client, _ = _paged_client([[{"cost_usd": 1.0}]])

    async def _run(fn, idempotent=True, *, label="other"):
        return fn()

    with patch("app.db.supabase.get_client", lambda: client), patch("app.db.supabase._run_sync", _run):
        await ledger.seed_budget()
    assert ledger.today_cost() == 5.0


@pytest.mark.asyncio
async def test_seed_budget_marks_cap_not_durable_when_ledger_off(monkeypatch):
    monkeypatch.setenv("AI_LEDGER_ENABLED", "false")
    client, _ = _paged_client([[]])

    async def _run(fn, idempotent=True, *, label="other"):
        return fn()

    with patch("app.db.supabase.get_client", lambda: client), patch("app.db.supabase._run_sync", _run):
        await ledger.seed_budget()
    assert ledger.LEDGER_HEALTH["budget_durable"] is False


def test_budget_advances_are_not_lost_across_threads():
    import threading

    def _work():
        for _ in range(500):
            ledger._advance_budget(0.001)

    ts = [threading.Thread(target=_work) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert ledger.today_cost() == round(8 * 500 * 0.001, 6)
