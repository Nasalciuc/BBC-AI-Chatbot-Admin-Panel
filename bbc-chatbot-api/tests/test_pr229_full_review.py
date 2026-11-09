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
