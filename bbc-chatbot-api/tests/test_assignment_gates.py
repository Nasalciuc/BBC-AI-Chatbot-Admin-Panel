"""One axis of truth: the claim gate, and the release generalised on #211.

A conversation could have two owners: claim_conversation read the field and
perform_handoff wrote it tens of milliseconds later, so two agents pressing
inside that window both passed the read and both wrote. The database decides
now — exactly one UPDATE matches, everyone else gets zero rows and NOTHING
else happens for them.
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

from datetime import datetime, timedelta, timezone

from app.db import supabase as sb
from app.services import handoff

_CID = "conv-1"


class _ConvTable:
    """One conversation row; honours the conditional filters the gates use."""

    def __init__(self, row=None):
        self.row = row if row is not None else {
            "id": _CID, "assigned_agent_id": None, "status": "active",
            "queued_at": None, "metadata": {},
        }
        self.update_count = 0

    def table(self, _name):
        return _Chain(self)


class _Chain:
    def __init__(self, t):
        self.t = t
        self._update = None
        self._select = None
        self._filters = []

    def select(self, cols, **_k):
        self._select = cols
        return self

    def update(self, payload):
        self._update = payload
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self._filters.append(("is", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        row = self.t.row
        for kind, col, val in self._filters:
            have = row.get(col)
            if kind == "eq" and have != val:
                return MagicMock(data=[])
            if kind == "is" and val == "null" and have is not None:
                return MagicMock(data=[])
            if kind == "in" and have not in val:
                return MagicMock(data=[])
        if self._update is not None:
            row.update(self._update)
            self.t.update_count += 1
            return MagicMock(data=[dict(row)])
        if self._select:
            return MagicMock(data=[{k: row.get(k) for k in
                                    [c.strip() for c in self._select.split(",")]}])
        return MagicMock(data=[dict(row)])


def _wire(table, agent_row=None):
    return [
        patch.object(sb, "get_client", return_value=table),
        patch.object(sb, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())),
        patch.object(sb, "get_user_by_id",
                     new=AsyncMock(return_value=agent_row or {"id": "a1", "name": "Ana",
                                                              "chats_served_today": 3,
                                                              "chats_served_date": "x"})),
        patch.object(sb, "increment_chats_served", new=AsyncMock()),
    ]


# ══════════════════════════════════════════════════════════════
# Gate 1 — the pure race
# ══════════════════════════════════════════════════════════════

class TestClaimGate:
    @pytest.mark.asyncio
    async def test_five_simultaneous_claims_one_winner(self):
        table = _ConvTable()
        ps = _wire(table)
        with ps[0], ps[1], ps[2], ps[3]:
            sb._queued_at_ok = None
            results = await asyncio.gather(
                *[sb.claim_conversation_if_unassigned(_CID, f"agent-{i}") for i in range(5)]
            )
        wins = [r for r in results if r["won"]]
        assert len(wins) == 1, "exactly one UPDATE matches"
        assert table.update_count == 1
        assert table.row["mode"] == "human"
        assert table.row["status"] == "active"

    @pytest.mark.asyncio
    async def test_closed_conversation_is_refused(self):
        table = _ConvTable({"id": _CID, "assigned_agent_id": None,
                            "status": "closed", "queued_at": None, "metadata": {}})
        ps = _wire(table)
        with ps[0], ps[1], ps[2], ps[3]:
            out = await sb.claim_conversation_if_unassigned(_CID, "a1")
        assert out["won"] is False

    @pytest.mark.asyncio
    async def test_needs_agent_becomes_active_and_queued_at_nulls_atomically(self):
        queued = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()
        table = _ConvTable({"id": _CID, "assigned_agent_id": None,
                            "status": "needs_agent", "queued_at": queued, "metadata": {}})
        ps = _wire(table)
        with ps[0], ps[1], ps[2], ps[3]:
            sb._queued_at_ok = None
            out = await sb.claim_conversation_if_unassigned(_CID, "a1")
        assert out["won"] is True
        assert table.row["status"] == "active", "status follows ownership in the SAME write"
        assert table.row["queued_at"] is None
        # The age was read BEFORE the NULL — that is the whole point.
        assert out["queued_age_seconds"] is not None and out["queued_age_seconds"] > 40

    @pytest.mark.asyncio
    async def test_score_gets_the_full_user_row_not_just_the_id(self):
        """increment_chats_served reads the counter OUT OF the dict it is
        given. {'id': ...} would make it write 1 forever."""
        table = _ConvTable()
        agent_row = {"id": "a1", "name": "Ana", "chats_served_today": 3,
                     "chats_served_date": "2026-08-19"}
        ps = _wire(table, agent_row)
        with ps[0], ps[1], ps[2], ps[3] as inc:
            await sb.claim_conversation_if_unassigned(_CID, "a1")
        inc.assert_awaited_once()
        passed = inc.await_args.args[0]
        assert passed.get("chats_served_today") == 3, "full row, not a bare id"

    @pytest.mark.asyncio
    async def test_db_error_means_won_false_and_no_exception(self):
        with patch.object(sb, "get_client", side_effect=RuntimeError("db down")):
            out = await sb.claim_conversation_if_unassigned(_CID, "a1")
        assert out == {"won": False, "queued_age_seconds": None}


# ══════════════════════════════════════════════════════════════
# Gate 2 — reassignment
# ══════════════════════════════════════════════════════════════

class TestReassign:
    @pytest.mark.asyncio
    async def test_a_to_b_once_then_never_again(self):
        table = _ConvTable({"id": _CID, "assigned_agent_id": "A",
                            "status": "active", "queued_at": None, "metadata": {}})
        ps = _wire(table)
        with ps[0], ps[1]:
            assert await sb.reassign_conversation(_CID, "A", "B") is True
            assert table.row["assigned_agent_id"] == "B"
            assert await sb.reassign_conversation(_CID, "A", "B") is False, \
                "it is not A's any more"


# ══════════════════════════════════════════════════════════════
# Gate 3 — release with a reason
# ══════════════════════════════════════════════════════════════

class TestReleaseReason:
    async def _release(self, reason, column_exists=True):
        table = _ConvTable({"id": _CID, "assigned_agent_id": "A",
                            "status": "active", "queued_at": None, "metadata": {}})
        with patch.object(handoff.db, "get_client", return_value=table), \
             patch.object(handoff.db, "_run_sync",
                          new=AsyncMock(side_effect=lambda fn, **k: fn())), \
             patch.object(handoff.db, "_queued_at_column_available",
                          return_value=column_exists):
            ok = await handoff._release_from_agent(_CID, "A", {}, reason=reason)
        return ok, table

    @pytest.mark.asyncio
    async def test_queue_reasons_stamp_queued_at(self):
        for reason in ("agent_offline", "released_by_agent", "supervisor"):
            ok, table = await self._release(reason)
            assert ok is True
            assert table.row["queued_at"] is not None, reason
            assert table.row["assigned_agent_id"] is None

    @pytest.mark.asyncio
    async def test_other_reasons_leave_queued_at_alone(self):
        ok, table = await self._release("ai_takeover")
        assert ok is True
        assert table.row["queued_at"] is None

    @pytest.mark.asyncio
    async def test_missing_column_releases_without_the_stamp(self):
        ok, table = await self._release("agent_offline", column_exists=False)
        assert ok is True
        assert table.row["queued_at"] is None
        assert table.row["assigned_agent_id"] is None

    def test_the_five_terminal_closes_never_touch_queued_at(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        for rel, needle in [
            ("app/api/chat.py", "post_sale"),
            ("app/api/cron.py", "closed_existing_crm"),
            ("app/pipeline/orchestrator.py", "AUTO-CLOSE"),
        ]:
            src = (root / rel).read_text(encoding="utf-8")
            # No terminal close writes queued_at anywhere in these files.
            assert '"queued_at"' not in src, f"{rel} must not touch queued_at"


# ══════════════════════════════════════════════════════════════
# The endpoint and the handoff behind it
# ══════════════════════════════════════════════════════════════

class TestClaimEndpoint:
    def test_reclaim_of_my_own_skips_the_gate(self):
        import inspect

        from app.api import conversations as capi

        src = inspect.getsource(capi.claim_conversation)
        own = src.index("current_agent == _me")
        gate = src.index("claim_conversation_if_unassigned")
        assert own < gate, "the own-reclaim branch returns before the gate runs"

    def test_the_409_names_the_winner(self):
        import inspect

        from app.api import conversations as capi

        src = inspect.getsource(capi.claim_conversation)
        assert '"winner"' in src
        assert '"already_claimed"' in src

    def test_claim_won_carries_the_queue_age(self):
        import inspect

        from app.api import conversations as capi

        src = inspect.getsource(capi.claim_conversation)
        assert '"claim_won"' in src
        assert "response_seconds=int(_age)" in src

    def test_health_exposes_claims_in_both_payloads(self):
        import pathlib

        src = (pathlib.Path(__file__).resolve().parent.parent / "app/api/health.py").read_text(encoding="utf-8")
        assert src.count('"claims": dict(CLAIM_HEALTH),') == 2


class TestGateFirstHandoff:
    @pytest.mark.asyncio
    async def test_losing_the_gate_produces_no_side_effects(self):
        """No metadata, no 'has joined', no notification: the client must never
        see two consultants introducing themselves two seconds apart."""
        conv = {"id": _CID, "assigned_agent_id": None, "metadata": {}}
        upd = AsyncMock()
        msg = AsyncMock(return_value={"id": "m"})
        with patch.object(handoff.db, "get_conversation_simple",
                          new=AsyncMock(return_value=conv)), \
             patch.object(handoff.db, "claim_conversation_if_unassigned",
                          new=AsyncMock(return_value={"won": False, "queued_age_seconds": None})), \
             patch.object(handoff.db, "update_conversation", upd), \
             patch.object(handoff.db, "get_user_by_id", new=AsyncMock(return_value={})), \
             patch.object(handoff, "_safe_system_msg", msg):
            out = await handoff.perform_handoff_to_agent(_CID, "agent-B")
        assert out == {}
        upd.assert_not_awaited()
        msg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_two_agents_one_owner_one_joined_message(self):
        """The mixed race: manual claim and dispatch landing together."""
        table = _ConvTable()
        msgs = []

        async def _msg(cid, content, **k):
            msgs.append(content)
            return {"id": f"m{len(msgs)}"}

        async def _gate(cid, agent_id):
            chain = table.table("conversations")
            res = (chain.update({"assigned_agent_id": agent_id, "mode": "human",
                                 "status": "active"})
                   .eq("id", cid).is_("assigned_agent_id", "null")
                   .in_("status", ["active", "needs_agent"]).execute())
            return {"won": bool(res.data), "queued_age_seconds": None}

        conv = {"id": _CID, "assigned_agent_id": None, "metadata": {}}
        with patch.object(handoff.db, "get_conversation_simple",
                          new=AsyncMock(return_value=conv)), \
             patch.object(handoff.db, "claim_conversation_if_unassigned",
                          new=AsyncMock(side_effect=_gate)), \
             patch.object(handoff.db, "update_conversation", new=AsyncMock()), \
             patch.object(handoff.db, "get_user_by_id", new=AsyncMock(return_value={})), \
             patch.object(handoff, "_safe_system_msg", new=AsyncMock(side_effect=_msg)), \
             patch.object(handoff.manager, "push", new=AsyncMock()), \
             patch("app.services.presence.log_activity", new=AsyncMock()), \
             patch("app.pipeline.orchestrator._fire_and_forget", new=MagicMock()) as fire:
            results = await asyncio.gather(
                handoff.perform_handoff_to_agent(_CID, "agent-A", emit_messages=True),
                handoff.perform_handoff_to_agent(_CID, "agent-B", emit_messages=True),
            )
        winners = [r for r in results if r != {}]
        assert len(winners) == 1, "one owner"
        joined = [m for m in msgs if "joined" in m.lower() or "specialist" in m.lower()]
        assert len(msgs) == 3, "one connecting + one joined + one welcome, once"
        assert fire.call_count == 1, "one 'assigned' activity row"

    @pytest.mark.asyncio
    async def test_gate_won_true_skips_the_second_ownership_write(self):
        conv = {"id": _CID, "assigned_agent_id": None, "metadata": {}}
        upd = AsyncMock()
        gate = AsyncMock()
        with patch.object(handoff.db, "get_conversation_simple",
                          new=AsyncMock(return_value=conv)), \
             patch.object(handoff.db, "claim_conversation_if_unassigned", gate), \
             patch.object(handoff.db, "update_conversation", upd), \
             patch.object(handoff.db, "get_user_by_id", new=AsyncMock(return_value={})), \
             patch("app.services.presence.log_activity", new=AsyncMock()), \
             patch("app.pipeline.orchestrator._fire_and_forget", new=MagicMock()):
            await handoff.perform_handoff_to_agent(
                _CID, "agent-A", emit_messages=False, _gate_won=True
            )
        gate.assert_not_awaited(), "the caller already won it"
        payload = upd.await_args.args[1]
        assert "assigned_agent_id" not in payload, "ownership was written by the gate"
        assert "metadata" in payload
