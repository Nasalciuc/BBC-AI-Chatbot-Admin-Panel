"""POST /api/cron/ai-cost-report reconciles the chatbot key's rows.

Independent-review fix: _ledger_cost_for is async and was called without
await, so every row of the chatbot's own key raised TypeError (billed minus
a coroutine) and the endpoint answered 500."""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

with patch("app.db.supabase.get_client"):
    from app.main import app

from config.settings import settings


@pytest.mark.asyncio
async def test_ai_cost_report_reconciles_the_chatbot_key(monkeypatch):
    from app.api import cron

    monkeypatch.setattr(cron, "_require_cron", lambda r: None)
    monkeypatch.setattr(settings, "anthropic_api_key_id", "key-bot", raising=False)
    monkeypatch.setattr(cron, "_ledger_cost_for", AsyncMock(return_value=9.0))

    async def _run_sync(fn, **kw):
        class R:
            data = []
        return R()

    monkeypatch.setattr(cron.db, "_run_sync", _run_sync)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/cron/ai-cost-report", json={"rows": [
            {"day": "2026-11-02", "api_key_id": "key-bot", "model": "claude-sonnet-4-6", "billed_cost_usd": 10.0},
        ]})
    assert r.status_code == 200, r.text
    from app.ai.ledger import LEDGER_HEALTH
    assert LEDGER_HEALTH["reconcile"]["max_diff_pct"] == 10.0


@pytest.mark.asyncio
async def test_without_the_chatbot_key_id_the_report_is_refused_and_nothing_stored(monkeypatch):
    # CodeRabbit #226: with ANTHROPIC_API_KEY_ID unset no row is the chatbot's,
    # every row went in with ledger 0 and diff = billed, no alert could fire,
    # and the answer was success.
    from app.ai.ledger import LEDGER_HEALTH
    from app.api import cron

    monkeypatch.setattr(cron, "_require_cron", lambda r: None)
    monkeypatch.setattr(settings, "anthropic_api_key_id", "", raising=False)
    writes = []

    async def _run_sync(fn, **kw):
        writes.append(kw.get("label"))

    monkeypatch.setattr(cron.db, "_run_sync", _run_sync)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/cron/ai-cost-report", json={"rows": [
            {"day": "2026-11-02", "api_key_id": "key-bot", "model": "claude-sonnet-5-5", "billed_cost_usd": 10.0},
        ]})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY_ID" in r.json()["detail"]
    assert writes == [] and LEDGER_HEALTH["reconcile"] == {"error": "ANTHROPIC_API_KEY_ID not set"}
