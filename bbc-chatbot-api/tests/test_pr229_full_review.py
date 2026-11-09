"""CodeRabbit #229, full review (9 nov 2026), checked against the code.

Each section is one finding: what the code did, and the path that must not
change (rule 5)."""

import os
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.security import ban_guard, ban_store  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ban_guard._reset_for_tests()
    ban_store._reset_for_tests()
    yield
    ban_guard._reset_for_tests()
    ban_store._reset_for_tests()


# ── alert-only hits: one email per jail per cooldown ──────────────────────

@pytest.mark.asyncio
async def test_a_planted_bait_sends_one_email_per_jail_per_cooldown(monkeypatch):
    # An <img> to the bait on a busy page: every visitor's browser hits it,
    # cross-site, so nobody is banned, and each hit used to send an email.
    alert = AsyncMock()
    monkeypatch.setattr(ban_guard, "_ops_alert", alert)
    for i in range(50):
        await ban_guard.trip(None, f"203.0.113.{i}", "widget_bait", 3600, alert=True, staff=False, mode="alert_only")
    assert alert.await_count == 1
    assert ban_guard.FAIL2BAN_HEALTH["alerts_suppressed"] == 49
    assert ban_guard.FAIL2BAN_HEALTH["by_jail"]["widget_bait"] == 50  # every hit still counted
    # Another jail has its own cooldown.
    await ban_guard.trip(None, "203.0.113.99", "decoy_key", 3600, alert=True, staff=False, mode="alert_only")
    assert alert.await_count == 2


@pytest.mark.asyncio
async def test_after_the_cooldown_the_next_hit_emails_again(monkeypatch):
    alert = AsyncMock()
    monkeypatch.setattr(ban_guard, "_ops_alert", alert)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ban_guard.time, "monotonic", lambda: clock["t"])
    await ban_guard.trip(None, "198.51.100.1", "widget_bait", 3600, alert=True, staff=False, mode="alert_only")
    clock["t"] += ban_guard._OPERATOR_ALERT_COOLDOWN + 1
    await ban_guard.trip(None, "198.51.100.1", "widget_bait", 3600, alert=True, staff=False, mode="alert_only")
    assert alert.await_count == 2 and ban_guard.FAIL2BAN_HEALTH["alerts_suppressed"] == 0


@pytest.mark.asyncio
async def test_staff_and_real_bans_still_alert_as_before(monkeypatch):
    alert = AsyncMock()
    monkeypatch.setattr(ban_guard, "_ops_alert", alert)
    monkeypatch.setattr(ban_guard, "record_ban_row", AsyncMock())
    store = ban_store.BanStore(None)
    await ban_guard.trip(store, "192.0.2.7", "honeypot", 3600, alert=True, staff=True)
    await ban_guard.trip(store, "192.0.2.8", "widget_bait", 3600, alert=True, staff=False, mode="always")
    assert alert.await_count == 2 and ban_guard.FAIL2BAN_HEALTH["alerts_suppressed"] == 0


# ── Redis counts each member in its own window, like the memory store ─────

class _ZRedis:
    """Sorted sets, enough for _seen_in_window."""

    def __init__(self):
        self.z: dict = {}

    async def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)

    async def zremrangebyscore(self, key, lo, hi):
        exclusive = str(hi).startswith("(")
        bound = float(str(hi).lstrip("("))
        self.z[key] = {m: t for m, t in self.z.get(key, {}).items() if (t >= bound if exclusive else t > bound)}

    async def expire(self, key, seconds):
        return True

    async def zcard(self, key):
        return len(self.z.get(key, {}))


@pytest.mark.asyncio
@pytest.mark.parametrize("redis", [False, True], ids=["memory", "redis"])
async def test_a_member_ages_out_of_the_window_in_both_stores(monkeypatch, redis):
    # Behind one NAT, visitors poll /api/chat/status/<their id> hours apart.
    # The Redis set's TTL was renewed by every call, so they added up as if
    # all in one 10-minute window and tripped the enumeration jail.
    clock = {"t": 10_000.0}
    monkeypatch.setattr(ban_store.time, "time", lambda: clock["t"])
    store = ban_store.BanStore(redis_client=_ZRedis() if redis else None)
    counts = []
    for conv in ("c1", "c2", "c3", "c4", "c5"):
        counts.append(await store.distinct("enum:203.0.113.5", conv, 600))
        clock["t"] += 400  # one every ~7 minutes: never more than 2 inside 10 minutes
    assert counts == [1, 2, 2, 2, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("redis", [False, True], ids=["memory", "redis"])
async def test_a_member_exactly_one_window_old_is_still_in_it(monkeypatch, redis):
    clock = {"t": 10_000.0}
    monkeypatch.setattr(ban_store.time, "time", lambda: clock["t"])
    store = ban_store.BanStore(redis_client=_ZRedis() if redis else None)
    await store.distinct("enum:203.0.113.7", "c1", 600)
    clock["t"] += 600
    assert await store.distinct("enum:203.0.113.7", "c2", 600) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("redis", [False, True], ids=["memory", "redis"])
async def test_members_inside_the_window_still_add_up(monkeypatch, redis):
    clock = {"t": 10_000.0}
    monkeypatch.setattr(ban_store.time, "time", lambda: clock["t"])
    store = ban_store.BanStore(redis_client=_ZRedis() if redis else None)
    seen = [await store.note_auth_email("203.0.113.6", f"user{i}@example.com", 600) for i in range(4)]
    assert seen == [1, 2, 3, 4]
    assert await store.note_auth_email("203.0.113.6", "USER0@example.com", 600) == 4  # the same address, once
