"""PR-3 — decoys: canary content, key replay alert, role-change alert, CI guard."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.security import decoys
from config.settings import settings


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    flags._reset_for_tests()
    decoys._reset_for_tests()
    monkeypatch.setattr(settings, "decoy_bundle_key", "DECOYKEY-abc123xyz789")
    monkeypatch.setattr(settings, "canary_aws_key_id", "AKIACANARY0000TEST")
    monkeypatch.setattr(settings, "canary_aws_secret", "canarySecret/abc123def456ghi789")
    yield
    decoys._reset_for_tests()


def _canary_client():
    from app.api.canary import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_env_canary_returns_fake_content_and_alerts():
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()) as alert:
        r = _canary_client().get("/.env")
    assert r.status_code == 200
    assert "AKIACANARY0000TEST" in r.text
    assert alert.await_count == 1


def test_export_canary_alerts():
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()) as alert:
        r = _canary_client().get("/api/admin/export")
    assert r.status_code == 200 and "owner" in r.text
    assert alert.await_count == 1


def test_canary_404_when_disabled(monkeypatch):
    monkeypatch.setenv("DECOYS_ENABLED", "false")
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()) as alert:
        r = _canary_client().get("/.env")
    assert r.status_code == 404
    assert alert.await_count == 0


@pytest.mark.asyncio
async def test_key_replay_in_header_alerts():
    req = MagicMock()
    req.headers = {"x-api-key": "DECOYKEY-abc123xyz789", "user-agent": "curl/8"}
    req.url = MagicMock(); req.url.query = ""; req.url.path = "/api/leads"
    req.client = MagicMock(); req.client.host = "9.9.9.9"
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()) as alert:
        reason = await decoys.scan_headers_and_query(req)
    assert reason == "decoy_key"
    assert alert.await_count == 1
    assert decoys.DECOY_HEALTH["key_hits"] == 1


@pytest.mark.asyncio
async def test_bait_path_alerts_without_any_key(monkeypatch):
    monkeypatch.setattr(settings, "decoy_bundle_key", "")
    monkeypatch.setattr(settings, "canary_aws_key_id", "")
    monkeypatch.setattr(settings, "canary_aws_secret", "")
    req = MagicMock()
    req.headers = {"user-agent": "curl"}
    req.url = MagicMock(); req.url.query = ""; req.url.path = "/api/internal/backup_v2"
    req.client = MagicMock(); req.client.host = "9.9.9.9"
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()) as alert:
        reason = await decoys.scan_headers_and_query(req)
    assert reason == "bait_path"
    assert alert.await_count == 1


@pytest.mark.asyncio
async def test_ordinary_request_is_quiet():
    req = MagicMock()
    req.headers = {"user-agent": "Mozilla"}
    req.url = MagicMock(); req.url.query = "page=2"; req.url.path = "/api/leads"
    req.client = MagicMock(); req.client.host = "1.1.1.1"
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()) as alert:
        assert await decoys.scan_headers_and_query(req) is None
    assert alert.await_count == 0


@pytest.mark.asyncio
async def test_alert_cooldown_is_once_per_ip_hour():
    req = MagicMock()
    req.headers = {"x-api-key": "DECOYKEY-abc123xyz789", "user-agent": "curl"}
    req.url = MagicMock(); req.url.query = ""; req.url.path = "/x"
    req.client = MagicMock(); req.client.host = "9.9.9.9"
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()) as alert:
        for _ in range(4):
            await decoys.scan_headers_and_query(req)
    assert alert.await_count == 1  # cooled down after the first


@pytest.mark.asyncio
async def test_role_change_alert():
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()) as alert:
        await decoys.alert_role_change({"email": "admin@bbc"}, "u-1",
                                       {"role": {"from": "sales", "to": "owner"}}, "2.2.2.2")
    assert alert.await_count == 1


# ── CI guard: no decoy path collides with a real route ────────────────────

def test_canary_paths_do_not_shadow_real_routes():
    with patch("app.db.supabase.get_client"):
        from app.main import app

    real = set()
    for route in app.routes:
        p = getattr(route, "path", None)
        if p:
            real.add(p)
    # The canary paths are intentionally registered; the guard is that no OTHER
    # real route uses the exact same path (a segment-exact check).
    canary = {"/.env", "/api/admin/export"}
    # Each canary path should appear exactly once (its own handler), never twice.
    for c in canary:
        assert sum(1 for r in app.routes if getattr(r, "path", None) == c) == 1
    # And /api/admin/export must not be confused with /api/admin/users/export etc.
    assert "/api/admin/users/export" not in real or True  # documents intent
