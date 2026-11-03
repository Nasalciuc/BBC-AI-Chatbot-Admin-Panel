"""PR-6 — the router table, row by row, plus budget and family selection."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.ai import router
from config.settings import settings


@pytest.fixture(autouse=True)
def _clean():
    flags._reset_for_tests()
    yield
    flags._reset_for_tests()


def test_sales_default_is_sonnet_low():
    r = router.decide(router.SALES_REPLY)
    assert r.model == settings.claude_sonnet_model and r.effort == "low"


def test_decisive_4x_is_opus():
    r = router.decide(router.SALES_REPLY, decisive=True)
    assert r.model == settings.claude_opus_model and r.effort == "medium"


def test_decisive_55_without_allow_opus_is_sonnet():
    r = router.decide(router.SALES_REPLY, decisive=True, family_55=True)
    assert r.model == settings.claude_sonnet_55_model


def test_decisive_55_with_allow_opus(monkeypatch):
    monkeypatch.setenv("ALLOW_OPUS", "true")
    r = router.decide(router.SALES_REPLY, decisive=True, family_55=True)
    assert r.model == settings.claude_opus_55_model


def test_family_55_default_is_sonnet_55():
    r = router.decide(router.SALES_REPLY, family_55=True)
    assert r.model == settings.claude_sonnet_55_model


def test_budget_80_drops_to_haiku():
    r = router.decide(router.SALES_REPLY, decisive=True, budget_fraction=0.85)
    assert r.model == settings.claude_haiku_model and r.reason == "budget_80_haiku"


def test_budget_100_is_template():
    r = router.decide(router.SALES_REPLY, budget_fraction=1.0)
    assert r.model is None and r.reason == "budget_exhausted"


def test_collect_haiku_ab(monkeypatch):
    monkeypatch.setenv("COLLECT_MODEL", "haiku")
    r = router.decide(router.COLLECT_REPLY)
    assert r.model == settings.claude_haiku_model


def test_support_is_haiku():
    r = router.decide(router.SUPPORT_REPLY)
    assert r.model == settings.claude_haiku_model


def test_cache_ttl_flag(monkeypatch):
    assert router.decide(router.SALES_REPLY).cache_ttl == "5m"
    monkeypatch.setenv("CACHE_TTL", "1h")
    assert router.decide(router.SALES_REPLY).cache_ttl == "1h"


def test_closing_4x_is_opus_55_is_sonnet():
    assert router.decide(router.CLOSING).model == settings.claude_opus_model
    assert router.decide(router.CLOSING, family_55=True).model == settings.claude_sonnet_55_model
