"""The queue cannot grow in silence — health, alert, load signal.

Ten people notified is nine people assuming somebody else will take it. The
system watches instead of trusting.
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoieCJ9.sig")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import cron
from app.api.cron import _QUEUE_ALERT, run_queue_stall_alert
from app.services.routing import STICKY_HEALTH


def _reset():
    _QUEUE_ALERT.update({"last_sent_at": None, "last_count": 0, "active": False})


def _stats(waiting, oldest):
    return AsyncMock(return_value={"waiting_now": waiting, "oldest_seconds": oldest,
                                   "claimed_today": 0, "avg_time_to_claim_seconds": None})


# ══════════════════════════════════════════════════════════════
# The stall alert: shout once, again only on growth, recover once
# ══════════════════════════════════════════════════════════════

class TestStallAlert:
    @pytest.mark.asyncio
    async def test_one_alert_then_suppressed_inside_the_cooldown(self):
        _reset()
        email = AsyncMock()
        with patch.object(cron.db, "get_queue_stats", _stats(2, 200)), \
             patch("app.services.email.send_super_alert_email", email):
            first = await run_queue_stall_alert()
            second = await run_queue_stall_alert()
        assert first["state"] == "alerted"
        assert second["state"] == "suppressed"
        assert email.await_count == 1

    @pytest.mark.asyncio
    async def test_growth_breaks_through_the_cooldown(self):
        _reset()
        email = AsyncMock()
        with patch("app.services.email.send_super_alert_email", email):
            with patch.object(cron.db, "get_queue_stats", _stats(2, 200)):
                await run_queue_stall_alert()
            with patch.object(cron.db, "get_queue_stats", _stats(5, 260)):
                out = await run_queue_stall_alert()
        assert out["state"] == "alerted", "more people waiting is new information"
        assert email.await_count == 2

    @pytest.mark.asyncio
    async def test_recovery_sends_exactly_one_email_then_silence(self):
        _reset()
        email = AsyncMock()
        with patch("app.services.email.send_super_alert_email", email):
            with patch.object(cron.db, "get_queue_stats", _stats(2, 200)):
                await run_queue_stall_alert()
            with patch.object(cron.db, "get_queue_stats", _stats(0, 0)):
                rec = await run_queue_stall_alert()
                after = await run_queue_stall_alert()
        assert rec["state"] == "recovered"
        assert after["state"] == "ok"
        assert email.await_count == 2, "one alert + one recovery, nothing more"

    @pytest.mark.asyncio
    async def test_a_young_queue_is_not_a_stalled_queue(self):
        _reset()
        email = AsyncMock()
        with patch.object(cron.db, "get_queue_stats", _stats(4, 60)), \
             patch("app.services.email.send_super_alert_email", email):
            out = await run_queue_stall_alert()
        assert out["state"] == "ok"
        email.assert_not_awaited(), "waiting 60s is a queue, not a stall"

    def test_the_job_runs_every_60s(self):
        import inspect

        from app.services import scheduler

        src = inspect.getsource(scheduler)
        assert '("queue_stall_alert", run_queue_stall_alert, 60),' in src


# ══════════════════════════════════════════════════════════════
# Health: the numbers come from the right places
# ══════════════════════════════════════════════════════════════

def test_avg_time_to_claim_reads_claim_won_not_queued_at():
    import inspect

    from app.db import supabase as sb

    src = inspect.getsource(sb.get_queue_stats)
    assert '"claim_won"' in src
    assert "response_seconds" in src


def test_sticky_counters_sit_on_the_right_paths():
    import inspect

    from app.services import routing as rt

    src = inspect.getsource(rt.route_conversation)
    assert 'STICKY_HEALTH["sticky_routed"] += 1' in src

    from app.services import handoff as ho

    src2 = inspect.getsource(ho.fall_back_to_ai)
    assert 'STICKY_HEALTH["sticky_fell_back"] += 1' in src2
    assert '"affinity"' in src2, "only a fallen AFFINITY assignment counts"


def test_sticky_counters_exist():
    assert set(STICKY_HEALTH) == {"sticky_routed", "sticky_fell_back"}


def test_health_exposes_sticky_in_both_and_queue_in_the_full_payload():
    import pathlib

    src = (pathlib.Path(__file__).resolve().parent.parent / "app/api/health.py").read_text(encoding="utf-8")
    assert src.count('"sticky": dict(STICKY_HEALTH),') == 2
    assert '"queue": _queue_stats,' in src
    assert '"operator_load": _load,' in src


def test_operator_load_is_a_signal_never_a_barrier():
    import inspect

    from app.db import supabase as sb

    src = inspect.getsource(sb.get_operator_load)
    assert "never a barrier" in src
    # And nothing in routing consults it.
    from app.services import routing as rt

    assert "get_operator_load" not in inspect.getsource(rt)
