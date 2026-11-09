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
async def test_a_rotating_forwarded_ip_cannot_flood_ops():
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()) as alert:
        for i in range(50):
            req = MagicMock()
            req.headers = {"x-api-key": settings.decoy_bundle_key, "user-agent": "curl",
                           "x-forwarded-for": f"198.51.100.{i}"}
            req.url = MagicMock(); req.url.query = ""; req.url.path = "/x"
            req.client = MagicMock(); req.client.host = "9.9.9.9"
            await decoys.scan_headers_and_query(req)
    assert alert.await_count == 1  # one mail per reason per 5 minutes
    assert decoys.DECOY_HEALTH["alerts_suppressed"] == 49


@pytest.mark.asyncio
async def test_the_cooldown_table_stays_bounded(monkeypatch):
    monkeypatch.setattr(decoys, "_MAX_KEYS", 10)
    monkeypatch.setattr(decoys, "_GLOBAL_COOLDOWN", 0.0)
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()):
        for i in range(25):
            await decoys._alert("key_replay", f"198.51.100.{i}", "x")
    assert len(decoys._last_alert) <= 10


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
    # The match is on the whole path: /api/admin/export says nothing about
    # /api/admin/users/export or any other route that merely starts the same.
    for c in canary:
        assert c in real
        assert sum(1 for r in app.routes if getattr(r, "path", None) == c) == 1


def test_seeding_a_phone_only_decoy_twice_adds_one_lead(monkeypatch):
    # CodeRabbit #226: with only DECOY_LEAD_PHONE set the lookup asked for
    # visitor_email = "" and never found the stored NULL: a new lead each run.
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "create_decoys", Path(__file__).resolve().parent.parent / "scripts" / "create_decoys.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    rows = {"users": [], "conversations": [], "leads": []}

    class _Q:
        def __init__(self, table):
            self.table, self.filters, self.row = table, [], None

        def select(self, *_a):
            return self

        def eq(self, col, val):
            self.filters.append((col, val))
            return self

        def insert(self, row):
            self.row = row
            return self

        def execute(self):
            from unittest.mock import MagicMock

            if self.row is not None:
                row = {"id": f"{self.table}-{len(rows[self.table]) + 1}", **self.row}
                rows[self.table].append(row)
                return MagicMock(data=[row])
            found = [r for r in rows[self.table] if all(r.get(c) == v for c, v in self.filters)]
            return MagicMock(data=found)

    class _Db:
        def table(self, name):
            return _Q(name)

    monkeypatch.setattr(script, "get_client", lambda: _Db())
    monkeypatch.setattr(script, "_hash", lambda pw: "h")
    monkeypatch.setenv("DECOY_LEAD_PHONE", "+1 555 010 0199")
    monkeypatch.delenv("DECOY_LEAD_EMAIL", raising=False)
    assert script.main() == 0 and script.main() == 0
    assert len(rows["conversations"]) == 1 and len(rows["leads"]) == 1 and len(rows["users"]) == 1
