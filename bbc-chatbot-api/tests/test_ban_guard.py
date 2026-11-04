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

_REAL_RECORD_BAN_ROW = ban_guard.record_ban_row


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    flags._reset_for_tests()
    ban_guard._reset_for_tests()
    ban_store._reset_for_tests()
    client_ip._reset_for_tests()
    # No REDIS_URL → memory store; IP trusted by default. The audit row is
    # exercised in its own test; elsewhere it would reach for a real client.
    monkeypatch.setattr(ban_guard, "record_ban_row",
                        __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock())
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
async def test_enforce_bans_from_the_next_request(monkeypatch):
    # The tripwire itself gets its normal answer; the next request is a 403.
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    app = _App()
    mw = ban_guard.BanGuardMiddleware(app)
    sent = await _run(mw, _scope("/.env"))
    assert app.called is True and _status(sent) == 200
    app.called = False
    sent = await _run(mw, _scope("/api/chat/abc"))
    assert app.called is False and _status(sent) == 403
    assert ban_guard.FAIL2BAN_HEALTH["banned"] == 1
    assert ban_guard.FAIL2BAN_HEALTH["blocked"] == 1


@pytest.mark.asyncio
async def test_api_honeypot_path(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    mw = ban_guard.BanGuardMiddleware(_App())
    await _run(mw, _scope("/api/admin/export"))
    assert _status(await _run(mw, _scope("/"))) == 403
    assert ban_guard.FAIL2BAN_HEALTH["by_jail"]["honeypot_api"] == 1


@pytest.mark.asyncio
async def test_segment_exact_not_prefix(monkeypatch):
    # /api/chats is a honeypot; /api/chat/x (the real chat) must pass.
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    app = _App()
    mw = ban_guard.BanGuardMiddleware(app)
    sent = await _run(mw, _scope("/api/chat/abc"))
    assert app.called is True and _status(sent) == 200
    assert ban_guard.FAIL2BAN_HEALTH["by_jail"] == {}


@pytest.mark.asyncio
async def test_basic_header_banned(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    mw = ban_guard.BanGuardMiddleware(_App())
    await _run(mw, _scope("/api/leads", {"authorization": "Basic Zm9vOmJhcg=="}))
    assert _status(await _run(mw, _scope("/api/leads"))) == 403


class _NotFoundApp(_App):
    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


@pytest.mark.asyncio
async def test_scan_jail_five_404s_ban(monkeypatch):
    # The 8 Sep replay: a burst of 404s on /api/* → ban; the next request 403.
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    mw = ban_guard.BanGuardMiddleware(_NotFoundApp())
    for i in range(4):
        await _run(mw, _scope(f"/api/nope{i}"))
    assert ban_guard.FAIL2BAN_HEALTH["by_jail"].get("scan") is None
    await _run(mw, _scope("/api/nope4"))
    assert ban_guard.FAIL2BAN_HEALTH["by_jail"]["scan"] == 1
    assert _status(await _run(mw, _scope("/api/chat/x"))) == 403


@pytest.mark.asyncio
async def test_staff_with_valid_jwt_never_banned(monkeypatch):
    import jwt as _jwt
    from config.settings import settings
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-for-ban-guard-0123456789")
    tok = _jwt.encode({"sub": "u1"}, settings.jwt_secret, algorithm="HS256")
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    mw = ban_guard.BanGuardMiddleware(_NotFoundApp())
    hdr = {"authorization": f"Bearer {tok}"}
    for i in range(20):
        await _run(mw, _scope(f"/api/nope{i}", hdr))
    await _run(mw, _scope("/.env", hdr))
    assert ban_guard.FAIL2BAN_HEALTH["banned"] == 0
    assert ban_guard.FAIL2BAN_HEALTH["staff_alerts"] == 1
    # The same IP without the token is not banned either.
    app = _App()
    sent = await _run(ban_guard.BanGuardMiddleware(app), _scope("/api/chat/x"))
    assert _status(sent) == 200


@pytest.mark.asyncio
async def test_ignored_ip_never_banned(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "ban_ignore_ips", "198.51.100.7, 198.51.100.8")
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    mw = ban_guard.BanGuardMiddleware(_App())
    await _run(mw, _scope("/.env", ip="198.51.100.7"))
    assert _status(await _run(mw, _scope("/", ip="198.51.100.7"))) == 200
    assert ban_guard.FAIL2BAN_HEALTH["banned"] == 0


@pytest.mark.asyncio
async def test_decoy_key_bans_a_week(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "decoy_bundle_key", "bbc_live_DECOYKEY_abcdef")
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    client_ip.CLIENT_IP_HEALTH["ip_verified"] = True   # a passed IP self-test
    mw = ban_guard.BanGuardMiddleware(_App())
    await _run(mw, _scope("/api/leads", {"x-api-key": "bbc_live_DECOYKEY_abcdef"}))
    assert ban_guard.FAIL2BAN_HEALTH["by_jail"]["decoy_key"] == 1
    store = ban_store.get_store()
    # PR-15: the decoy key never waits — it bans under the always: identity.
    assert store._mem["always:203.0.113.9"] - __import__("time").time() > 6 * 24 * 3600


@pytest.mark.asyncio
async def test_unban_lets_the_next_request_through(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    mw = ban_guard.BanGuardMiddleware(_App())
    await _run(mw, _scope("/.env"))
    assert _status(await _run(mw, _scope("/"))) == 403
    await ban_store.get_store().unban("203.0.113.9")
    assert _status(await _run(mw, _scope("/"))) == 200


@pytest.mark.asyncio
async def test_exception_inside_guard_lets_request_through(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    monkeypatch.setattr(ban_store, "get_store", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    app = _App()
    sent = await _run(ban_guard.BanGuardMiddleware(app), _scope("/.env"))
    assert app.called is True and _status(sent) == 200


@pytest.mark.asyncio
async def test_streaming_body_passes_untouched(monkeypatch):
    # SSE: many body chunks with more_body=True must pass through in order.
    class _Stream(_App):
        async def __call__(self, scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")]})
            for i in range(3):
                await send({"type": "http.response.body", "body": f"data: {i}\n\n".encode(), "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    sent = await _run(ban_guard.BanGuardMiddleware(_Stream()), _scope("/api/chat/stream"))
    bodies = [m["body"] for m in sent if m["type"] == "http.response.body"]
    assert bodies[:3] == [b"data: 0\n\n", b"data: 1\n\n", b"data: 2\n\n"]


@pytest.mark.asyncio
async def test_revoke_sessions_old_token_rejected():
    import time as _t
    store = ban_store.get_store()
    await store.revoke_sessions("u-42")
    rb = ban_store.revoked_before_sync("u-42")
    assert rb is not None and rb <= int(_t.time())


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


@pytest.mark.asyncio
async def test_real_account_wrong_passwords_never_ban_ip(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    for _ in range(5):
        await ban_guard.note_login_failure("203.0.113.9", "dan@buybusinessclass.com")
    assert await ban_store.get_store().is_banned("203.0.113.9") is False


@pytest.mark.asyncio
async def test_four_distinct_emails_ban_ip(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    for e in ("a@x.com", "b@x.com", "c@x.com"):
        await ban_guard.note_login_failure("203.0.113.9", e)
    assert await ban_store.get_store().is_banned("203.0.113.9") is False
    await ban_guard.note_login_failure("203.0.113.9", "d@x.com")
    assert await ban_store.get_store().is_banned("203.0.113.9") is True


@pytest.mark.asyncio
async def test_decoy_login_and_form_trap_ban(monkeypatch):
    monkeypatch.setenv("FAIL2BAN_ENFORCE", "true")
    await ban_guard.note_login_failure("203.0.113.10", "admin@buybusinessclass.com")
    assert await ban_store.get_store().is_banned("203.0.113.10") is True
    await ban_guard.note_login_failure("203.0.113.11", "x@y.com", form_trap=True)
    assert await ban_store.get_store().is_banned("203.0.113.11") is True


@pytest.mark.asyncio
async def test_missing_bans_table_logs_once_and_stays_quiet(monkeypatch):
    real = _REAL_RECORD_BAN_ROW

    calls = {"n": 0}

    async def _fail(fn, **kw):
        calls["n"] += 1
        raise RuntimeError('relation "security_bans" does not exist (42P01)')

    monkeypatch.setattr("app.db.supabase._run_sync", _fail)
    await real("203.0.113.9", "honeypot", 60)
    await real("203.0.113.9", "honeypot", 60)
    assert calls["n"] == 1
    assert ban_guard.FAIL2BAN_HEALTH["table_missing"] is True


@pytest.mark.asyncio
async def test_revocation_is_seen_even_when_the_sync_redis_is_down(monkeypatch):
    """Review fix: revoke_sessions also keeps the mark in memory, so the sync
    reader sees it when response_cache's Redis client is unavailable."""
    from unittest.mock import AsyncMock as _AM

    fake = _AM()
    store = ban_store.BanStore(redis_client=fake)
    monkeypatch.setattr("app.services.response_cache._get_redis", lambda: None)
    await store.revoke_sessions("u-77")
    assert ban_store.revoked_before_sync("u-77") is not None
