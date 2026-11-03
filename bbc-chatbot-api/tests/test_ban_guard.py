"""PR-11 — the ASGI fail2ban guard: jails, fail-open, poisoned-ban, enforce."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.security import ban_guard, ban_store, client_ip


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    flags._reset_for_tests()
    ban_guard._reset_for_tests()
    ban_store._reset_for_tests()
    client_ip._reset_for_tests()
    # No REDIS_URL → memory store; IP trusted by default.
    monkeypatch.setattr("app.services.email.send_ops_alert_email",
                        __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock())
    yield
    ban_guard._reset_for_tests()
    ban_store._reset_for_tests()


class _App:
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _scope(path, headers=None, ip="203.0.113.9"):
    return {
        "type": "http",
        "path": path,
        "client": (ip, 12345),
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
    }


async def _run(mw, scope):
    sent = []

    async def send(m):
        sent.append(m)

    async def receive():
        return {"type": "http.request", "body": b""}

    await mw(scope, receive, send)
    return sent


def _status(sent):
    for m in sent:
        if m["type"] == "http.response.start":
            return m["status"]
    return None


@pytest.mark.asyncio
async def test_shadow_mode_counts_but_does_not_block():
    app = _App()
    mw = ban_guard.BanGuardMiddleware(app)
    sent = await _run(mw, _scope("/.env"))
    assert app.called is True  # passed through
    assert _status(sent) == 200
    assert ban_guard.FAIL2BAN_HEALTH["would_ban"] == 1
    assert ban_guard.FAIL2BAN_HEALTH["banned"] == 0


@pytest.mark.asyncio
async def test_enforce_blocks_honeypot(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    app = _App()
    mw = ban_guard.BanGuardMiddleware(app)
    sent = await _run(mw, _scope("/.env"))
    assert app.called is False
    assert _status(sent) == 404
    assert ban_guard.FAIL2BAN_HEALTH["banned"] >= 1


@pytest.mark.asyncio
async def test_api_honeypot_path(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    mw = ban_guard.BanGuardMiddleware(_App())
    assert _status(await _run(mw, _scope("/api/admin/export"))) == 404


@pytest.mark.asyncio
async def test_segment_exact_not_prefix(monkeypatch):
    # /api/chats is a honeypot; /api/chat/x (the real chat) must pass.
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    app = _App()
    mw = ban_guard.BanGuardMiddleware(app)
    sent = await _run(mw, _scope("/api/chat/abc"))
    assert app.called is True and _status(sent) == 200


@pytest.mark.asyncio
async def test_basic_header_banned(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    mw = ban_guard.BanGuardMiddleware(_App())
    sent = await _run(mw, _scope("/api/leads", {"authorization": "Basic Zm9vOmJhcg=="}))
    assert _status(sent) == 404


@pytest.mark.asyncio
async def test_poisoned_cross_site_is_counted_not_banned(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    app = _App()
    mw = ban_guard.BanGuardMiddleware(app)
    sent = await _run(mw, _scope("/.env", {"sec-fetch-site": "cross-site", "sec-fetch-mode": "no-cors"}))
    assert app.called is True and _status(sent) == 200
    assert ban_guard.FAIL2BAN_HEALTH["poisoned_skipped"] == 1


@pytest.mark.asyncio
async def test_excluded_paths_never_ban(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    app = _App()
    mw = ban_guard.BanGuardMiddleware(app)
    for p in ("/health", "/api/cron/daily-learning", "/api/integration/x"):
        app.called = False
        sent = await _run(mw, _scope(p))
        assert app.called is True, p


@pytest.mark.asyncio
async def test_fail_open_when_ip_untrusted(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    client_ip.CLIENT_IP_HEALTH["ip_trusted"] = False
    app = _App()
    mw = ban_guard.BanGuardMiddleware(app)
    sent = await _run(mw, _scope("/.env"))
    assert app.called is True  # fail-open
    assert ban_guard.FAIL2BAN_HEALTH["degraded"] is True


@pytest.mark.asyncio
async def test_ban_doubles_capped_at_week():
    store = ban_store.BanStore(None)
    a = await store.ban("1.1.1.1", "honeypot", 3600)
    b = await store.ban("1.1.1.1", "honeypot", 3600)
    assert b == 2 * a
    for _ in range(10):
        last = await store.ban("1.1.1.1", "honeypot", 3600)
    assert last == 7 * 24 * 3600


@pytest.mark.asyncio
async def test_disabled_flag_is_noop(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENABLED", "false")
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    app = _App()
    mw = ban_guard.BanGuardMiddleware(app)
    sent = await _run(mw, _scope("/.env"))
    assert app.called is True
