"""Three things the system did that nobody asked it to.

  1. It repeated an action that had already happened: one missed handoff wrote
     21 `Timeout` rows into Nolan Hunt's history in three seconds, and 16 into
     Robert Doyle's. The sweeper runs on EVERY operator heartbeat, so with N
     operators it passes the same stale conversation every 5s, N times, and
     nothing checked whether it had already run.
  2. It overruled a human's decision: an agent closed a conversation — spam,
     or already handled on the phone — and the abandoned-conversation cron
     pushed it to the CRM as a flight request anyway.
  3. It offered a button that could not lead anywhere good (panel side, pinned
     in tests/embedded-signout.test.ts).
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoieCJ9.sig")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.services import handoff
from app.services.handoff import HANDOFF_HEALTH, fall_back_to_ai

_CID = "conv-1"
_AGENT = "agent-7"


class _ConditionalTable:
    """A conversations table where `.eq("assigned_agent_id", X)` can only
    match while the row still holds X — the guarantee Postgres gives us for a
    single UPDATE statement, and the whole point of the fix."""

    def __init__(self, holder: str | None = _AGENT):
        self.holder = holder
        self.releases = 0

    def table(self, _name):
        return self

    def update(self, payload):
        self._payload = payload
        return self

    def eq(self, col, val):
        if col == "assigned_agent_id":
            self._wants = val
        return self

    def is_(self, *_a):
        return self

    def execute(self):
        if getattr(self, "_wants", None) is not None:
            if self.holder != self._wants:
                return MagicMock(data=[])
            self.holder = None
            self.releases += 1
            return MagicMock(data=[{"id": _CID}])
        return MagicMock(data=[{"id": _CID}])


def _conv(agent=_AGENT):
    return {
        "id": _CID,
        "assigned_agent_id": agent,
        "mode": "human",
        "metadata": {"announce_pending": False},
    }


def _patches(table, conv):
    """Everything fall_back_to_ai touches, so the test exercises the real
    control flow and only the database is fake."""
    return [
        patch.object(handoff.db, "get_conversation_simple", new=AsyncMock(return_value=conv)),
        patch.object(handoff.db, "get_client", return_value=table),
        patch.object(handoff.db, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())),
        patch.object(handoff.db, "update_conversation", new=AsyncMock(return_value=True)),
        patch.object(handoff.db, "get_recent_messages", new=AsyncMock(return_value=[])),
        patch.object(handoff, "_handoff_phrase_recently_sent", new=AsyncMock(return_value=True)),
        patch.object(handoff, "add_message", new=AsyncMock(return_value={"id": "m1"})),
        patch.object(handoff.manager, "push", new=AsyncMock()),
        patch.object(handoff.db, "get_last_system_message", new=AsyncMock(return_value=None)),
        patch("app.services.presence.log_activity", new=AsyncMock()),
        patch("app.pipeline.orchestrator._fire_and_forget", new=MagicMock()),
    ]


# ══════════════════════════════════════════════════════════════
# Defect 1 — one release, one set of consequences
# ══════════════════════════════════════════════════════════════

class TestIdempotentFallback:
    @pytest.mark.asyncio
    async def test_ten_simultaneous_sweeps_write_one_timeout_and_one_message(self):
        table = _ConditionalTable()
        conv = _conv()
        HANDOFF_HEALTH.update({"expired_since_boot": 0, "fallback_races": 0})

        ps = _patches(table, conv)
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8], ps[9], ps[10] as fire_mock:
            await asyncio.gather(*[fall_back_to_ai(_CID) for _ in range(10)])

        assert table.releases == 1, "exactly one run may hand the conversation back"
        assert HANDOFF_HEALTH["expired_since_boot"] == 1, "one missed handoff, counted once"
        assert HANDOFF_HEALTH["fallback_races"] == 9, "the other nine are visible in /health"
        # deadline_fired is scheduled through _fire_and_forget — once.
        assert fire_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_a_conversation_already_released_produces_nothing(self):
        """Another sweep got there first: no log, no message, no push."""
        table = _ConditionalTable(holder="somebody-else")
        HANDOFF_HEALTH.update({"expired_since_boot": 0, "fallback_races": 0})
        ps = _patches(table, _conv())
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8], ps[9], ps[10] as fire_mock:
            await fall_back_to_ai(_CID)

        assert table.releases == 0
        assert HANDOFF_HEALTH["expired_since_boot"] == 0, "no timeout against a human's record"
        assert HANDOFF_HEALTH["fallback_races"] == 1
        assert fire_mock.call_count == 0

    @pytest.mark.asyncio
    async def test_the_ordinary_single_sweep_still_does_its_job(self):
        table = _ConditionalTable()
        HANDOFF_HEALTH.update({"expired_since_boot": 0, "fallback_races": 0})
        ps = _patches(table, _conv())
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8], ps[9], ps[10]:
            await fall_back_to_ai(_CID)

        assert table.releases == 1
        assert HANDOFF_HEALTH["expired_since_boot"] == 1
        assert HANDOFF_HEALTH["fallback_races"] == 0

    @pytest.mark.asyncio
    async def test_with_no_agent_there_is_nobody_to_charge_a_timeout_to(self):
        table = _ConditionalTable(holder=None)
        HANDOFF_HEALTH.update({"expired_since_boot": 0, "fallback_races": 0})
        ps = _patches(table, _conv(agent=None))
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8], ps[9], ps[10] as fire_mock:
            await fall_back_to_ai(_CID)

        assert HANDOFF_HEALTH["expired_since_boot"] == 0
        assert fire_mock.call_count == 0, "no activity row without an agent"

    @pytest.mark.asyncio
    async def test_a_failed_release_records_no_timeout(self):
        """If we cannot prove we released it, we must not mark a human late."""
        with patch.object(handoff.db, "get_client", side_effect=RuntimeError("db down")):
            assert await handoff._release_from_agent(_CID, _AGENT, {}) is False

    def test_every_consequence_sits_behind_the_release(self):
        import inspect

        src = inspect.getsource(fall_back_to_ai)
        release = src.index("_release_from_agent")
        for effect in ("expired_since_boot", "HANDOFF EXPIRED", "deadline_fired"):
            assert src.index(effect) > release, f"{effect} must follow the release"
        assert "fallback_races" in src

    def test_the_race_is_visible_in_health(self):
        assert "fallback_races" in HANDOFF_HEALTH


# ══════════════════════════════════════════════════════════════
# Defect 2 — the cron does not overrule an agent
# ══════════════════════════════════════════════════════════════

class TestAgentClosedStaysClosed:
    def test_the_sweep_skips_conversations_a_human_closed(self):
        import inspect

        from app.db import supabase as sb

        src = inspect.getsource(sb.get_abandoned_conversations)
        assert 'is_("metadata->>closed_by_agent_id", "null")' in src
        # …and #200's rescue of the Diana/Paulette classes is untouched.
        assert 'in_("status", ["active", "closed"])' in src
        assert 'in_("mode", ["ai", "human"])' in src

    def test_closing_records_who_did_it_before_it_records_that_it_is_closed(self):
        """Otherwise there is an instant where the row reads `closed` with no
        author, and a cron tick inside that instant pushes it."""
        import inspect

        from app.api import conversations as capi

        src = inspect.getsource(capi.close_conversation)
        marker = src.index("closed_by_agent_id")
        status = src.index('"status": "closed"')
        assert marker < status

    def test_the_marker_is_merged_not_snapshotted(self):
        import inspect

        from app.api import conversations as capi

        src = inspect.getsource(capi.close_conversation)
        assert "patch_conversation_presence" in src, "a full metadata write erases concurrent keys"

    @pytest.mark.asyncio
    async def test_a_close_still_closes_even_if_the_marker_cannot_be_patched(self):
        """Migration 031 missing must not stop an agent closing a chat."""
        import inspect

        from app.api import conversations as capi

        src = inspect.getsource(capi.close_conversation)
        assert "update_conversation" in src.split("patch_conversation_presence")[1]
