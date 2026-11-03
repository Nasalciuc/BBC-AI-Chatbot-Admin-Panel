"""PR-2 — the cost ledger: prices with cache, one recording path, the AST guard."""

import ast
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.ai import ledger
from app.ai.prices import cost_from_usage, rates_for

APP = Path(__file__).resolve().parent.parent / "app"
# Only these modules may call the Anthropic SDK directly. PR-6 adds app/ai/llm.py.
_ALLOWED = {"app/ai/claude.py", "app/ai/llm.py"}
_RAW = {"create", "stream"}


@pytest.fixture(autouse=True)
def _clean():
    flags._reset_for_tests()
    ledger._reset_for_tests()
    yield
    ledger._reset_for_tests()


# ── prices ────────────────────────────────────────────────────────────────

def test_cache_tokens_are_costed_not_zero():
    # 8000 tokens written to Sonnet 4.6 cache (5m) → ~0.03, not 0.
    usage = SimpleNamespace(input_tokens=0, output_tokens=0,
                            cache_creation_input_tokens=8000, cache_read_input_tokens=0)
    assert cost_from_usage("claude-sonnet-4-6", usage) == round(8000 * 3.75 / 1_000_000, 6)


def test_cache_read_is_cheap():
    usage = SimpleNamespace(input_tokens=0, output_tokens=0,
                            cache_creation_input_tokens=0, cache_read_input_tokens=10000)
    assert cost_from_usage("claude-sonnet-4-6", usage) == round(10000 * 0.30 / 1_000_000, 6)


def test_5m_and_1h_split_uses_each_rate():
    cc = SimpleNamespace(ephemeral_5m_input_tokens=1000, ephemeral_1h_input_tokens=2000)
    usage = SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                            cache_creation_input_tokens=3000, cache_creation=cc)
    expected = round(1000 * 2.5 / 1_000_000 + 2000 * 4.0 / 1_000_000, 6)
    assert cost_from_usage("claude-sonnet-5-5", usage) == expected


def test_haiku_55_second_tier_over_100k():
    small, exact = rates_for("claude-haiku-5-5", 50_000)
    big, _ = rates_for("claude-haiku-5-5", 150_000)
    assert exact is True
    assert big["input"] > small["input"]


def test_unknown_model_uses_family_then_zero():
    r, exact = rates_for("claude-sonnet-9-9")
    assert exact is False and r["input"] > 0
    r2, exact2 = rates_for("totally-unknown")
    assert exact2 is False and r2["input"] == 0


# ── ledger recording ────────────────────────────────────────────────────────

def test_record_enqueues_and_tallies():
    usage = SimpleNamespace(input_tokens=100, output_tokens=50,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0)
    cost = ledger.record_ai_call(task="classify", model="claude-haiku-5-5", usage=usage)
    assert cost > 0
    assert ledger.LEDGER_HEALTH["recorded"] == 1
    assert ledger.LEDGER_HEALTH["by_task"]["classify"]["calls"] == 1


def test_disabled_flag_records_nothing(monkeypatch):
    monkeypatch.setenv("AI_LEDGER_ENABLED", "false")
    usage = SimpleNamespace(input_tokens=100, output_tokens=50,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0)
    cost = ledger.record_ai_call(task="classify", model="claude-haiku-5-5", usage=usage)
    assert cost > 0  # cost still computed for the caller
    assert ledger.LEDGER_HEALTH["recorded"] == 0


def test_full_queue_drops_and_counts(monkeypatch):
    monkeypatch.setattr(ledger, "_q", __import__("queue").Queue(maxsize=2))
    for _ in range(5):
        ledger.record_ai_call(task="x", model="claude-haiku-5-5", cost=0.0)
    assert ledger.LEDGER_HEALTH["dropped"] >= 3


@pytest.mark.asyncio
async def test_missing_table_disables_without_crashing(monkeypatch):
    async def _boom(fn, idempotent=True, *, label="other"):
        raise Exception('relation "ai_calls" does not exist')

    monkeypatch.setattr("app.db.supabase._run_sync", _boom)
    monkeypatch.setattr("app.db.supabase.get_client", lambda: None)
    ledger.record_ai_call(task="x", model="claude-haiku-5-5", cost=0.01)
    await ledger.flush_once()
    assert ledger.LEDGER_HEALTH["table_missing"] is True


# ── the AST guard ───────────────────────────────────────────────────────────

def test_only_allowed_modules_call_the_sdk():
    offenders = []
    for path in APP.rglob("*.py"):
        rel = path.relative_to(APP.parent).as_posix()
        if rel in _ALLOWED or "__pycache__" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _RAW):
                # .messages.create / .messages.stream / .batches.create
                val = node.func.value
                if isinstance(val, ast.Attribute) and val.attr in ("messages", "batches"):
                    offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, f"Anthropic SDK called outside {_ALLOWED}: {offenders}"
