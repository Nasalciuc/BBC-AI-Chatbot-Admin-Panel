"""PR-1 — login backoff keyed on email+IP, and the spoofable-IP self-test."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.security import client_ip as cip
from app.security import login_guard


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    flags._reset_for_tests()
    login_guard._reset_for_tests()
    cip._reset_for_tests()
    # memory backend, not a real Redis
    monkeypatch.setattr("app.services.response_cache._get_redis", lambda: None)
    yield
    login_guard._reset_for_tests()
    cip._reset_for_tests()


def _fail(email, ip, n):
    for _ in range(n):
        asyncio.run(login_guard.record_failure(email, ip))


def test_four_failures_do_not_throttle():
    _fail("a@b.com", "1.1.1.1", 4)
    assert login_guard.retry_after("a@b.com", "1.1.1.1") == 0


def test_fifth_failure_throttles():
    _fail("a@b.com", "1.1.1.1", 5)
    wait = login_guard.retry_after("a@b.com", "1.1.1.1")
    assert 0 < wait <= 60


def test_backoff_grows():
    _fail("a@b.com", "1.1.1.1", 5)
    first = login_guard.retry_after("a@b.com", "1.1.1.1")
    _fail("a@b.com", "1.1.1.1", 3)
    assert login_guard.retry_after("a@b.com", "1.1.1.1") > first


def test_same_email_other_ip_is_free():
    _fail("victim@bbc.com", "9.9.9.9", 8)
    assert login_guard.retry_after("victim@bbc.com", "9.9.9.9") > 0
    assert login_guard.retry_after("victim@bbc.com", "2.2.2.2") == 0


def test_a_correct_password_clears_the_pair():
    _fail("a@b.com", "1.1.1.1", 6)
    assert login_guard.retry_after("a@b.com", "1.1.1.1") > 0
    login_guard.clear("a@b.com", "1.1.1.1")
    assert login_guard.retry_after("a@b.com", "1.1.1.1") == 0


def test_tenth_failure_alerts_once():
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()) as alert:
        _fail("a@b.com", "1.1.1.1", 12)
    assert alert.await_count == 1


def test_disabled_flag_never_throttles(monkeypatch):
    monkeypatch.setenv("LOGIN_BACKOFF_ENABLED", "false")
    _fail("a@b.com", "1.1.1.1", 9)
    assert login_guard.retry_after("a@b.com", "1.1.1.1") == 0


# ── client_ip header choice ───────────────────────────────────────────────

def _req(headers, peer="203.0.0.1"):
    r = MagicMock()
    r.headers = headers
    r.client = MagicMock()
    r.client.host = peer
    return r


def test_legacy_prefers_cf_then_xff_then_peer():
    assert cip.client_ip(_req({"cf-connecting-ip": "1.1.1.1", "x-forwarded-for": "2.2.2.2"})) == "1.1.1.1"
    assert cip.client_ip(_req({"x-forwarded-for": "2.2.2.2, 3.3.3.3"})) == "2.2.2.2"
    assert cip.client_ip(_req({})) == "203.0.0.1"


def test_rate_limiter_legacy_ignores_cf():
    # prefer_cf=False: the rate limiter never trusted cf-connecting-ip
    assert cip.client_ip(_req({"cf-connecting-ip": "1.1.1.1", "x-forwarded-for": "2.2.2.2"}), prefer_cf=False) == "2.2.2.2"


def test_configured_header_wins_when_present(monkeypatch):
    monkeypatch.setenv("CLIENT_IP_HEADER", "x-real-ip")
    r = _req({"x-real-ip": "5.5.5.5", "x-forwarded-for": "9.9.9.9"})
    assert cip.client_ip(r) == "5.5.5.5"


def test_missing_configured_header_falls_back_and_counts(monkeypatch):
    monkeypatch.setenv("CLIENT_IP_HEADER", "x-real-ip")
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()):
        got = cip.client_ip(_req({"x-forwarded-for": "9.9.9.9"}))
    assert got == "9.9.9.9"  # legacy order
    assert cip.CLIENT_IP_HEALTH["header_missing"] == 1


def test_xff_hops_reads_from_the_right(monkeypatch):
    # The client prepends a forged value; the edge appends the real one.
    monkeypatch.setenv("CLIENT_IP_HEADER", "xff-hops:1")
    r = _req({"x-forwarded-for": "203.0.113.7, 4.4.4.4"})
    assert cip.client_ip(r, prefer_cf=False) == "4.4.4.4"
    monkeypatch.setenv("CLIENT_IP_HEADER", "xff-hops:2")
    flags._reset_for_tests()
    r = _req({"x-forwarded-for": "203.0.113.7, 4.4.4.4, 10.0.0.2"})
    assert cip.client_ip(r, prefer_cf=False) == "4.4.4.4"


def test_xff_hops_with_too_few_elements_falls_back_and_counts(monkeypatch):
    monkeypatch.setenv("CLIENT_IP_HEADER", "xff-hops:2")
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()):
        got = cip.client_ip(_req({"x-forwarded-for": "4.4.4.4"}), prefer_cf=False)
    assert got == "4.4.4.4"
    assert cip.CLIENT_IP_HEALTH["header_missing"] == 1


def test_untrusted_ip_keys_the_backoff_on_the_email():
    # A forged X-Forwarded-For per attempt must not reset the backoff.
    cip.CLIENT_IP_HEALTH["ip_trusted"] = False
    for i in range(5):
        asyncio.run(login_guard.record_failure("victim@bbc.com", f"198.51.100.{i}"))
    assert login_guard.retry_after("victim@bbc.com", "198.51.100.99") > 0


def test_trusted_ip_keeps_the_pair():
    _fail("victim@bbc.com", "9.9.9.9", 5)
    assert login_guard.retry_after("victim@bbc.com", "2.2.2.2") == 0


# ── IP self-test ──────────────────────────────────────────────────────────

def test_selftest_clears_trust_when_spoof_echoes(monkeypatch):
    from app.services import ip_selftest
    from config.settings import settings

    monkeypatch.setattr(settings, "public_api_url", "https://api.test")
    monkeypatch.setattr(settings, "cron_secret", "s")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"client_ip": "203.0.113.7", "source_ip": "203.0.113.7"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp()

    with patch("httpx.AsyncClient", return_value=_Client()), \
         patch("app.services.email.send_ops_alert_email", new=AsyncMock()) as alert:
        out = asyncio.run(ip_selftest.run_ip_selftest())
    assert out["spoofable"] is True
    assert cip.ip_trusted() is False and cip.ip_verified() is False
    assert alert.await_count == 1


def test_selftest_keeps_trust_when_spoof_is_dropped(monkeypatch):
    from app.services import ip_selftest
    from config.settings import settings

    monkeypatch.setattr(settings, "public_api_url", "https://api.test")
    monkeypatch.setattr(settings, "cron_secret", "s")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"client_ip": "198.51.100.9", "source_ip": "198.51.100.9"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp()

    with patch("httpx.AsyncClient", return_value=_Client()):
        out = asyncio.run(ip_selftest.run_ip_selftest())
    assert out["spoofable"] is False
    assert cip.ip_trusted() is True and cip.ip_verified() is True


# ── CodeRabbit #226 follow-up: limits that hold before the edge is proven ─

def test_the_email_wide_ceiling_holds_across_ips():
    # Trusted IP, a fresh address per attempt: 19 pairs of one failure each
    # are below the email ceiling (4x the pair ladder), the 20th is not.
    for i in range(19):
        asyncio.run(login_guard.record_failure("victim@bbc.com", f"198.51.100.{i}"))
    assert login_guard.retry_after("victim@bbc.com", "198.51.100.200") == 0
    asyncio.run(login_guard.record_failure("victim@bbc.com", "198.51.100.19"))
    assert login_guard.retry_after("victim@bbc.com", "198.51.100.200") > 0
    assert login_guard.LOGIN_GUARD_HEALTH["email_throttled"] == 1


def test_a_correct_password_clears_the_email_ceiling_too():
    for i in range(20):
        asyncio.run(login_guard.record_failure("victim@bbc.com", f"198.51.100.{i}"))
    login_guard.clear("victim@bbc.com", "198.51.100.200")
    assert login_guard.retry_after("victim@bbc.com", "198.51.100.200") == 0


def test_a_spoofable_edge_moves_legacy_to_the_rightmost_hop():
    cip.CLIENT_IP_HEALTH["ip_trusted"] = False
    r = _req({"x-forwarded-for": "203.0.113.7, 4.4.4.4"})
    assert cip.client_ip(r, prefer_cf=False) == "4.4.4.4"
    assert cip.CLIENT_IP_HEALTH["spoofable_fallback"] == 1


def test_an_unproven_edge_keeps_legacy():
    r = _req({"x-forwarded-for": "2.2.2.2, 4.4.4.4"})
    assert cip.client_ip(r, prefer_cf=False) == "2.2.2.2"
    assert cip.ip_verified() is False  # not run yet: unverified, not "safe"


def test_a_skipped_or_failed_selftest_never_marks_the_ip_verified(monkeypatch):
    from app.services import ip_selftest
    from config.settings import settings

    monkeypatch.setattr(settings, "public_api_url", "", raising=False)
    asyncio.run(ip_selftest.run_ip_selftest())
    assert cip.CLIENT_IP_HEALTH["ip_verified"] is None

    monkeypatch.setattr(settings, "public_api_url", "https://api.test")
    monkeypatch.setattr(settings, "cron_secret", "s")
    with patch("httpx.AsyncClient", side_effect=RuntimeError("unreachable")):
        asyncio.run(ip_selftest.run_ip_selftest())
    assert cip.CLIENT_IP_HEALTH["ip_verified"] is False and cip.ip_verified() is False


def test_a_detected_edge_stays_detected_while_it_is_unchanged(monkeypatch):
    """The fallback must not make the next self-test read the edge as fixed:
    the test judges source_ip (the leftmost, still forged), not client_ip."""
    from app.services import ip_selftest
    from config.settings import settings

    monkeypatch.setattr(settings, "public_api_url", "https://api.test")
    monkeypatch.setattr(settings, "cron_secret", "s")
    cip.CLIENT_IP_HEALTH["ip_trusted"] = False

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):  # what /health/ip-echo answers while the fallback is on
            return {"client_ip": "198.51.100.4", "source_ip": "203.0.113.7"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp()

    with patch("httpx.AsyncClient", return_value=_Client()), \
         patch("app.services.email.send_ops_alert_email", new=AsyncMock()):
        for _ in range(3):
            out = asyncio.run(ip_selftest.run_ip_selftest())
            assert out["spoofable"] is True and cip.ip_trusted() is False



# ── CodeRabbit #226 full review ───────────────────────────────────────────

def _echo_client(status: int, body: dict):
    import httpx

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    return _Client()


@pytest.mark.parametrize("status, body", [
    (401, {"detail": "Invalid cron token"}),          # a rotated CRON_SECRET
    (200, {"client_ip": "198.51.100.9"}),             # no source_ip: no evidence
    (200, {"source_ip": ""}),
])
def test_a_selftest_without_a_real_echo_proves_nothing(status, body, monkeypatch):
    from app.services import ip_selftest
    from config.settings import settings

    monkeypatch.setattr(settings, "public_api_url", "https://api.test")
    monkeypatch.setattr(settings, "cron_secret", "s")
    cip.CLIENT_IP_HEALTH["ip_trusted"] = False  # a spoof was found before
    with patch("httpx.AsyncClient", return_value=_echo_client(status, body)):
        out = asyncio.run(ip_selftest.run_ip_selftest())
    assert "error" in out
    # Not verified, and the earlier finding stands.
    assert cip.CLIENT_IP_HEALTH["ip_verified"] is False and cip.ip_trusted() is False


def test_the_missing_header_alert_task_is_held_until_it_ends():
    sent = asyncio.Event()

    async def _alert(*a, **k):
        sent.set()

    async def _run():
        with patch("app.services.email.send_ops_alert_email", new=_alert):
            cip._alert_missing_once("x-real-ip")
            assert len(cip._ALERT_TASKS) == 1
            await asyncio.wait_for(sent.wait(), 1)
            await asyncio.sleep(0)
        assert not cip._ALERT_TASKS

    asyncio.run(_run())


def test_a_login_read_leaves_no_key_behind():
    for i in range(50):
        assert login_guard.retry_after(f"nobody{i}@x.com", "1.1.1.1") == 0
    assert login_guard._mem == {}


def test_memory_drops_keys_past_the_window_once_it_is_large(monkeypatch):
    monkeypatch.setattr(login_guard, "_SWEEP_AT", 100)
    old = 1_000_000.0
    for i in range(100):
        login_guard._record_memory(f"login_fail:a{i}@x.com|*", old)
    assert len(login_guard._mem) == 100
    login_guard._record_memory("login_fail:new@x.com|*", old + login_guard._WINDOW + 1)
    assert list(login_guard._mem) == ["login_fail:new@x.com|*"]
