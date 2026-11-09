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
from app.security import ban_guard as _ban_guard_module  # noqa: E402

# The real writer, before any fixture replaces it.
_REAL_RECORD_BAN_ROW = _ban_guard_module.record_ban_row


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
        return self.keys.get(key) or -2  # what SET gave it; -2: no such key

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


class _LostReply(_Redis):
    """EXPIRE on the ban is applied, then its reply is lost."""

    def __init__(self, ttl_fails=False):
        super().__init__()
        self.ttl_fails = ttl_fails
        self.ttl_now = None

    async def expire(self, key, seconds):
        if key.startswith("ban:"):
            self.ttl_now = seconds  # applied …
            raise ConnectionError("reply lost")  # … but the reply never came
        return True

    async def ttl(self, key):
        if self.ttl_fails:
            raise ConnectionError("still down")
        return self.ttl_now if self.ttl_now is not None else 100


@pytest.mark.asyncio
async def test_a_lost_reply_after_the_claim_records_the_length_redis_reads_back(_bans):
    _guard, ban_store, _rows, _alert = _bans
    # incr says 2: the doubled length (7200 s) was applied, its reply lost.
    assert await ban_store.BanStore(redis_client=_LostReply()).ban("5.5.5.5", "honeypot", 3600) == (7200, True)
    # Nothing to read back either: the base length, still created.
    assert await ban_store.BanStore(redis_client=_LostReply(ttl_fails=True)).ban("5.5.5.6", "honeypot", 3600) == (3600, True)


@pytest.mark.asyncio
async def test_a_ban_another_request_holds_is_still_already_held(_bans):
    ban_guard, ban_store, rows, alert = _bans
    store = ban_store.BanStore(redis_client=_Redis())
    await ban_guard._ban_identity(store, "9.9.9.9", "honeypot", 3600, alert=True)
    await ban_guard._ban_identity(store, "9.9.9.9", "honeypot", 3600, alert=True)
    assert rows.await_count == 1 and alert.await_count == 1
    assert ban_guard.FAIL2BAN_HEALTH["ban_already_held"] == 1 and ban_guard.FAIL2BAN_HEALTH["ban_errors"] == 0


# ── the ban history: the missing table by its code, never by the text ──────

class _DbError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(f"{message} {details or ''}")
        self.code, self.message, self.details = code, message, details


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["42P01", "PGRST205"])
async def test_the_missing_table_turns_the_history_off_once(code, monkeypatch, _bans):
    ban_guard, _store, _rows, _alert = _bans
    calls = []

    async def _fail(fn, **_kw):
        calls.append(1)
        raise _DbError(code, "relation does not exist")

    monkeypatch.setattr("app.db.supabase._run_sync", _fail)
    await _REAL_RECORD_BAN_ROW("203.0.113.9", "honeypot", 60)
    await _REAL_RECORD_BAN_ROW("203.0.113.9", "honeypot", 60)
    assert len(calls) == 1 and ban_guard.FAIL2BAN_HEALTH["table_missing"] is True


@pytest.mark.asyncio
async def test_a_missing_column_is_a_row_error_and_the_history_stays_on(monkeypatch, caplog, _bans):
    # The old text check read 'column … of relation "security_bans" does not
    # exist' as the table missing, and wrote no row again until a restart.
    ban_guard, _store, _rows, _alert = _bans
    calls = []

    async def _fail(fn, **_kw):
        calls.append(1)
        raise _DbError("42703", 'column "ban_count" of relation "security_bans" does not exist',
                       details="Failing row contains (203.0.113.9, honeypot)")

    monkeypatch.setattr("app.db.supabase._run_sync", _fail)
    with caplog.at_level("WARNING", logger="app.security.ban_guard"):
        await _REAL_RECORD_BAN_ROW("203.0.113.9", "honeypot", 60)
        await _REAL_RECORD_BAN_ROW("203.0.113.9", "honeypot", 60)
    assert len(calls) == 2 and ban_guard.FAIL2BAN_HEALTH["table_missing"] is False
    assert ban_guard.FAIL2BAN_HEALTH["row_errors"] == 2
    assert "42703" in caplog.text and "203.0.113.9" not in caplog.text
