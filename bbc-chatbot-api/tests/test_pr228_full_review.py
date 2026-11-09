"""CodeRabbit #228, full review (9 nov 2026), checked against the code.

Each section is one finding: what the code did, and the path that must not
change (rule 5)."""

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api.auth_routes import _login_place  # noqa: E402


# ── the login place: one network, one key, whatever its spelling ───────────

def _req(headers=None):
    return SimpleNamespace(headers=headers or {})


@pytest.mark.parametrize("ip", ["2001:db8::5", "2001:0db8:0:1::5", "2001:db8:0000:ffff::1", "2001:DB8::abcd"])
def test_one_ipv6_48_is_one_place(ip):
    # Cut from the text these gave "2001:db8:", "2001:0db8:0" … : a new place
    # each, and a "new place" alert for the same person on the same network.
    assert _login_place(_req(), ip) == "net:2001:db8::/48"


def test_ipv4_is_its_24_and_a_mapped_address_is_the_same_place():
    assert _login_place(_req(), "203.0.113.9") == "net:203.0.113.0/24"
    assert _login_place(_req(), "203.0.113.200") == "net:203.0.113.0/24"
    assert _login_place(_req(), "::ffff:203.0.113.9") == "net:203.0.113.0/24"


@pytest.mark.parametrize("ip", ["1.2.3.x", "999.1.1.1", "not-an-ip", "", "unknown"])
def test_what_is_not_an_address_is_no_place(ip):
    assert _login_place(_req(), ip) == ""


def test_the_edges_country_still_wins():
    assert _login_place(_req({"cf-ipcountry": "ro"}), "203.0.113.9") == "country:RO"
    assert _login_place(_req({"cf-ipcountry": "XX"}), "203.0.113.9") == "net:203.0.113.0/24"


# ── a ban the store failed to set is not "already held" ───────────────────

class _Redis:
    """SET NX, TTL, INCR, EXPIRE; `fail` names the first call that raises."""

    def __init__(self, fail=None):
        self.fail = fail
        self.keys: dict = {}

    async def _maybe(self, name):
        if self.fail == name:
            raise ConnectionError("redis down")

    async def set(self, key, value, nx=False, ex=None):
        await self._maybe("set")
        if nx and key in self.keys:
            return None
        self.keys[key] = ex
        return True

    async def ttl(self, key):
        await self._maybe("ttl")
        return 100

    async def incr(self, key):
        await self._maybe("incr")
        return 2  # a repeat: the ban would double

    async def expire(self, key, seconds):
        await self._maybe("expire")
        return True


@pytest.fixture
def _bans(monkeypatch):
    from app.security import ban_guard, ban_store

    ban_guard._reset_for_tests()
    ban_store._reset_for_tests()
    rows, alert = AsyncMock(), AsyncMock()
    monkeypatch.setattr(ban_guard, "record_ban_row", rows)
    monkeypatch.setattr(ban_guard, "_ops_alert", alert)
    yield ban_guard, ban_store, rows, alert
    ban_guard._reset_for_tests()
    ban_store._reset_for_tests()


@pytest.mark.asyncio
async def test_a_redis_failure_before_the_claim_raises_and_is_counted_apart(_bans):
    ban_guard, ban_store, rows, alert = _bans
    store = ban_store.BanStore(redis_client=_Redis(fail="set"))
    with pytest.raises(ConnectionError):
        await store.ban("6.6.6.6", "honeypot", 3600)
    await ban_guard._ban_identity(store, "6.6.6.6", "honeypot", 3600, alert=True)
    assert ban_guard.FAIL2BAN_HEALTH["ban_errors"] == 1
    assert ban_guard.FAIL2BAN_HEALTH["ban_already_held"] == 0
    assert rows.await_count == 0 and alert.await_count == 0  # no ban, so no row and no alert


@pytest.mark.asyncio
async def test_a_ban_set_whose_repeat_count_fails_stands_at_its_base_length(_bans):
    ban_guard, ban_store, rows, alert = _bans
    store = ban_store.BanStore(redis_client=_Redis(fail="incr"))
    assert await store.ban("7.7.7.7", "honeypot", 3600) == (3600, True)
    await ban_guard._ban_identity(store, "8.8.8.8", "honeypot", 3600, alert=True)
    rows.assert_awaited_once_with("8.8.8.8", "honeypot", 3600)
    assert ban_guard.FAIL2BAN_HEALTH["ban_errors"] == 0


@pytest.mark.asyncio
async def test_a_ban_another_request_holds_is_still_already_held(_bans):
    ban_guard, ban_store, rows, alert = _bans
    store = ban_store.BanStore(redis_client=_Redis())
    await ban_guard._ban_identity(store, "9.9.9.9", "honeypot", 3600, alert=True)
    await ban_guard._ban_identity(store, "9.9.9.9", "honeypot", 3600, alert=True)
    assert rows.await_count == 1 and alert.await_count == 1
    assert ban_guard.FAIL2BAN_HEALTH["ban_already_held"] == 1 and ban_guard.FAIL2BAN_HEALTH["ban_errors"] == 0
