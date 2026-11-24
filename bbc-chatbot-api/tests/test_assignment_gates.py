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
        # What the connection does to the next call (#3245: the UPDATE landed
        # and its answer never reached the API).
        self.lose_update_answer = False   # the UPDATE lands, then the call raises
        self.fail_update = False          # the call raises, nothing lands
        self.fail_owner_read = False      # the owner check's read raises

    def table(self, _name):
        return _Chain(self)


def _goaway():
    import httpx

    return httpx.RemoteProtocolError(
        "<ConnectionTerminated error_code:ErrorCodes.NO_ERROR, last_stream_id:1999, additional_data:None>"
    )


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
        if self._update is not None and self.t.fail_update:
            raise _goaway()
        if self._select == "assigned_agent_id, status" and self.t.fail_owner_read:
            raise _goaway()
        for kind, col, val in self._filters:
            if "->>" in col:  # a JSON key, as PostgREST reads metadata->>key
                base, key = col.split("->>", 1)
                have = (row.get(base) or {}).get(key)
            else:
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
            if self.t.lose_update_answer:
                raise _goaway()
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


class TestClaimGateLostAnswer:
    """#2678 (13 Oct), #3245 (4 Nov): the UPDATE landed, its answer was lost,
    and the gate said "lost" — the operator who won read that a colleague
    had taken the chat, and it never opened for them."""

    async def _claim(self, table, agent="a1"):
        ps = _wire(table)
        before = dict(sb.CLAIM_GATE_HEALTH)
        with ps[0], ps[1], ps[2], ps[3] as inc:
            sb._queued_at_ok = None
            out = await sb.claim_conversation_if_unassigned(_CID, agent)
        delta = {k: sb.CLAIM_GATE_HEALTH[k] - before[k]
                 for k in ("write_failed", "won_after_check", "owner_check_failed")}
        return out, delta, inc

    @pytest.mark.asyncio
    async def test_the_write_landed_and_its_answer_was_lost_is_a_win(self):
        table = _ConvTable()
        table.lose_update_answer = True
        out, delta, inc = await self._claim(table)
        assert out["won"] is True, "the chat is this agent's: the write landed"
        assert table.row["assigned_agent_id"] == "a1"
        assert table.update_count == 1, "the UPDATE is never sent twice"
        assert inc.await_count == 1, "one chat, one point on the score"
        assert delta == {"write_failed": 1, "won_after_check": 1, "owner_check_failed": 0}
        assert sb.CLAIM_GATE_HEALTH["last_won_after_check_at"]

    @pytest.mark.asyncio
    async def test_the_write_never_landed_stays_lost(self):
        table = _ConvTable()
        table.fail_update = True
        out, delta, inc = await self._claim(table)
        assert out == {"won": False, "queued_age_seconds": None}
        assert table.row["assigned_agent_id"] is None
        inc.assert_not_awaited()
        assert delta == {"write_failed": 1, "won_after_check": 0, "owner_check_failed": 0}

    @pytest.mark.asyncio
    async def test_answer_lost_and_a_colleague_holds_it_stays_lost(self):
        table = _ConvTable({"id": _CID, "assigned_agent_id": "B", "status": "active",
                            "queued_at": None, "metadata": {}})
        table.fail_update = True
        out, delta, inc = await self._claim(table)
        assert out["won"] is False
        assert table.row["assigned_agent_id"] == "B"
        inc.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_answer_lost_and_mine_but_closed_stays_lost(self):
        table = _ConvTable({"id": _CID, "assigned_agent_id": "a1", "status": "closed",
                            "queued_at": None, "metadata": {}})
        table.fail_update = True
        out, _delta, inc = await self._claim(table)
        assert out["won"] is False, "a closed chat is nobody's to take"
        inc.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_answer_lost_and_owner_unreadable_stays_lost_and_is_counted(self):
        table = _ConvTable()
        table.lose_update_answer = True
        table.fail_owner_read = True
        out, delta, inc = await self._claim(table)
        assert out["won"] is False, "unproven: the endpoint reads the owner again"
        inc.assert_not_awaited()
        assert delta == {"write_failed": 1, "won_after_check": 0, "owner_check_failed": 1}

    @pytest.mark.asyncio
    async def test_a_clean_empty_answer_is_not_a_win_even_when_mine(self):
        """A double click: the first call won, the second matched nothing.
        Counting the second would score the same chat twice."""
        table = _ConvTable({"id": _CID, "assigned_agent_id": "a1", "status": "active",
                            "queued_at": None, "metadata": {}})
        out, delta, inc = await self._claim(table)
        assert out == {"won": False, "queued_age_seconds": None}
        inc.assert_not_awaited()
        assert delta == {"write_failed": 0, "won_after_check": 0, "owner_check_failed": 0}

    @pytest.mark.asyncio
    async def test_double_click_one_win_one_point(self):
        table = _ConvTable()
        ps = _wire(table)
        with ps[0], ps[1], ps[2], ps[3] as inc:
            sb._queued_at_ok = None
            first, second = await asyncio.gather(
                sb.claim_conversation_if_unassigned(_CID, "a1"),
                sb.claim_conversation_if_unassigned(_CID, "a1"),
            )
        assert [first["won"], second["won"]].count(True) == 1
        assert inc.await_count == 1


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
        # The agent-initiated reason left with the Release button (owner's
        # decision) — only the sweeper and a supervisor re-queue.
        for reason in ("agent_offline", "supervisor"):
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
        assert src.count('"claim_gate": dict(CLAIM_GATE_HEALTH),') == 2


class TestClaimEndpointAfterTheGate:
    """What the operator sees when the gate answers False."""

    _ME = {"id": "me", "role": "sales", "tunnel_scope": "sales"}

    async def _press_take(self, owner_after, status_after="active"):
        from app.api import conversations as capi

        conv = {"id": _CID, "assigned_agent_id": None, "status": "needs_agent",
                "tunnel": "sales", "metadata": {}}
        users = {"me": {"id": "me", "name": "Ana", "role": "sales"},
                 "B": {"id": "B", "name": "Bogdan", "role": "sales"}}
        handoff_call = AsyncMock(return_value={})
        activity = AsyncMock()
        before = dict(capi.CLAIM_HEALTH)
        with patch.object(sb, "get_conversation", new=AsyncMock(return_value=conv)), \
             patch.object(sb, "get_user_by_id", new=AsyncMock(side_effect=lambda uid: users.get(uid))), \
             patch.object(sb, "claim_conversation_if_unassigned",
                          new=AsyncMock(return_value={"won": False, "queued_age_seconds": None})), \
             patch.object(sb, "get_conversation_simple",
                          new=AsyncMock(return_value={"id": _CID, "assigned_agent_id": owner_after,
                                                      "status": status_after})), \
             patch("app.services.handoff.perform_handoff_to_agent", handoff_call), \
             patch("app.services.presence.log_activity", activity), \
             patch("app.pipeline.orchestrator._fire_and_forget",
                   new=MagicMock(side_effect=lambda coro: coro.close())):
            try:
                out = await capi.claim_conversation(conversation_id=_CID, user=dict(self._ME))
            except Exception as e:  # noqa: silent — the HTTPException IS the answer under test
                out = e
        delta = {k: capi.CLAIM_HEALTH[k] - before[k] for k in ("won", "lost", "already_mine")}
        return out, delta, handoff_call, activity

    @pytest.mark.asyncio
    async def test_mine_after_all_opens_the_chat_without_a_second_win(self):
        out, delta, handoff_call, activity = await self._press_take(owner_after="me")
        assert out == {"success": True, "data": {"conversation_id": _CID, "assigned_to": "me"}}
        assert delta == {"won": 0, "lost": 0, "already_mine": 1}
        handoff_call.assert_awaited_once()
        assert handoff_call.await_args.kwargs.get("_gate_won") is True, "a take of mine: the clock starts"
        activity.assert_not_awaited()  # no claim_won (the winning call logs it) and no claim_lost

    @pytest.mark.asyncio
    async def test_a_colleague_won_it_409_names_them(self):
        from fastapi import HTTPException

        out, delta, handoff_call, _activity = await self._press_take(owner_after="B")
        assert isinstance(out, HTTPException) and out.status_code == 409
        assert out.detail["winner"] == "Bogdan"
        assert delta == {"won": 0, "lost": 1, "already_mine": 0}
        handoff_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mine_but_closed_is_still_a_409(self):
        from fastapi import HTTPException

        out, delta, handoff_call, _activity = await self._press_take(owner_after="me", status_after="closed")
        assert isinstance(out, HTTPException) and out.status_code == 409
        assert delta["already_mine"] == 0
        handoff_call.assert_not_awaited()


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


# ══════════════════════════════════════════════════════════════
# A Take starts the first-reply clock
# ══════════════════════════════════════════════════════════════

def _iso_age_s(value: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds()


class TestTakeStartsTheClock:
    """The gate writes assigned_agent_id before perform_handoff reads the row,
    so the handoff read "same agent" and wrote no agent_assigned_at: no
    countdown in the panel and nothing for the sweep, after every Take."""

    _ME = {"id": "me", "role": "sales", "tunnel_scope": "sales"}

    async def _take(self, meta_before: dict, **row_extra):
        """The claim endpoint as production runs it: the gate wins, then the
        real perform_handoff reads the row the gate has just written. The
        chat is the agent's already, so its write is the conditional one."""
        from app.api import conversations as capi

        conv = {"id": _CID, "assigned_agent_id": None, "status": "needs_agent",
                "tunnel": "sales", "metadata": dict(meta_before), **row_extra}
        after_gate = {"id": _CID, "assigned_agent_id": "me", "mode": "human",
                      "status": "active", "metadata": dict(meta_before), **row_extra}
        upd = AsyncMock(return_value=("written", ""))
        activity = AsyncMock()
        with patch.object(sb, "get_conversation", new=AsyncMock(return_value=conv)), \
             patch.object(sb, "get_user_by_id",
                          new=AsyncMock(return_value={"id": "me", "name": "Ana", "role": "sales"})), \
             patch.object(sb, "claim_conversation_if_unassigned",
                          new=AsyncMock(return_value={"won": True, "queued_age_seconds": 12})), \
             patch.object(sb, "get_conversation_simple", new=AsyncMock(return_value=after_gate)), \
             patch.object(sb, "update_conversation_if_clock_unchanged", upd), \
             patch.object(sb, "update_conversation", new=AsyncMock()), \
             patch("app.services.presence.log_activity", activity), \
             patch("app.pipeline.orchestrator._fire_and_forget",
                   new=MagicMock(side_effect=lambda coro: coro.close())):
            out = await capi.claim_conversation(conversation_id=_CID, user=dict(self._ME))
        return out, upd, activity

    @pytest.mark.asyncio
    async def test_take_writes_the_clock_and_only_the_clock(self):
        before = {"engaged_agent_id": "agent-old", "handoff_reason": "affinity",
                  "announce_pending": True, "agent_assign_count": 2}
        out, upd, activity = await self._take(before)
        assert out == {"success": True, "data": {"conversation_id": _CID, "assigned_to": "me"}}
        upd.assert_awaited_once()
        payload = upd.await_args.args[1]
        assert "assigned_agent_id" not in payload and "mode" not in payload, "the gate wrote ownership"
        meta = payload["metadata"]
        assert 0 <= _iso_age_s(meta["agent_assigned_at"]) < 5, "the clock starts at the Take"
        assert meta["handoff_reason"] == "manual_claim", "a take after an affinity fallback is a take"
        assert meta["engaged_agent_id"] == "agent-old", "engaged waits for the agent's first message"
        assert meta["announce_pending"] is True, "left as it was"
        assert "agent_assign_count" not in meta, "the claim still resets the loop guard"
        actions = [c.args[3] for c in activity.call_args_list]
        assert actions == ["claim_won"], "claim_won is the take's row; no second 'assigned'"

    @pytest.mark.asyncio
    async def test_same_agent_without_a_gate_keeps_the_running_clock(self):
        """Not a Take: re-claiming my own chat, a second tab, a message on a chat
        I hold. Restarting the clock here evicted engaged agents (F1)."""
        started = (datetime.now(timezone.utc) - timedelta(seconds=40)).isoformat()
        row = {"id": _CID, "assigned_agent_id": "agent-A", "mode": "human", "status": "active",
               "metadata": {"agent_assigned_at": started, "handoff_reason": "first_message"}}
        upd = AsyncMock()
        fire = MagicMock(side_effect=lambda coro: coro.close())
        with patch.object(handoff.db, "get_conversation_simple", new=AsyncMock(return_value=row)), \
             patch.object(handoff.db, "update_conversation", upd), \
             patch.object(handoff.db, "get_user_by_id", new=AsyncMock(return_value={})), \
             patch("app.services.presence.log_activity", new=AsyncMock()), \
             patch("app.pipeline.orchestrator._fire_and_forget", fire):
            await handoff.perform_handoff_to_agent(
                _CID, "agent-A", emit_messages=False, handoff_reason="manual_claim"
            )
        payload = upd.await_args.args[1]
        assert "metadata" not in payload, "nothing in it changed, so the running clock is not even rewritten"
        assert payload["assigned_agent_id"] == "agent-A" and payload["mode"] == "human"
        fire.assert_not_called()

    @staticmethod
    def _store_with_cas(store: dict):
        """The row as the database holds it: the conditional write lands only
        while metadata->>agent_assigned_at is still what the caller read."""
        async def _cas(cid, payload, *, clock, last_agent_message_at=None, check_last_agent=False,
                       absent_keys=(), owner=None):
            if (store.get("metadata") or {}).get("agent_assigned_at") != clock:
                return ("changed", "")
            if check_last_agent and store.get("last_agent_message_at") != last_agent_message_at:
                return ("changed", "")
            if any((store.get("metadata") or {}).get(k) is not None for k in absent_keys):
                return ("changed", "")
            if owner is not None and store.get("assigned_agent_id") != owner:
                return ("changed", "")
            store.update(payload)  # the column write: metadata replaces the blob
            return ("written", "")

        async def _plain(cid, payload):
            store.update(payload)

        return AsyncMock(side_effect=_cas), AsyncMock(side_effect=_plain)

    @pytest.mark.asyncio
    async def test_a_stale_copy_from_a_second_tab_cannot_erase_the_clock(self):
        """CodeRabbit #280: the second tab read the row before the Take's write
        and writes after it. update_conversation replaces the whole metadata
        blob, so writing that copy back used to erase the clock. Round 2: the
        copy here still has a loop guard to reset, so it does want to write."""
        store = {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
                 "metadata": {"handoff_reason": "affinity", "agent_assign_count": 2}}
        stale = {**store, "metadata": dict(store["metadata"])}
        # The winning Take lands between the second tab's read and its write.
        clock = datetime.now(timezone.utc).isoformat()
        store["metadata"] = {"handoff_reason": "manual_claim", "agent_assigned_at": clock}
        cas, plain = self._store_with_cas(store)
        raced = handoff.HANDOFF_HEALTH["same_cycle_write_raced"]
        reads = iter([stale, {**store, "metadata": dict(store["metadata"])}])

        with patch.object(handoff.db, "get_conversation_simple",
                          new=AsyncMock(side_effect=lambda cid: next(reads))), \
             patch.object(handoff.db, "update_conversation_if_clock_unchanged", cas), \
             patch.object(handoff.db, "update_conversation", plain), \
             patch.object(handoff.db, "get_user_by_id", new=AsyncMock(return_value={})), \
             patch("app.services.presence.log_activity", new=AsyncMock()), \
             patch("app.pipeline.orchestrator._fire_and_forget",
                   new=MagicMock(side_effect=lambda coro: coro.close())):
            await handoff.perform_handoff_to_agent(
                _CID, "me", emit_messages=False, handoff_reason="manual_claim",
                _pop_meta_keys=["agent_assign_count", "agent_cooldown_until"],
            )
        assert store["metadata"] == {"handoff_reason": "manual_claim", "agent_assigned_at": clock}, \
            "the Take's clock survives, and the stale copy is not written back"
        assert handoff.HANDOFF_HEALTH["same_cycle_write_raced"] == raced + 1

    @pytest.mark.asyncio
    async def test_a_same_agent_handoff_that_changes_metadata_still_writes_it(self):
        """The other side of the guard: a loop guard to reset is still written."""
        row = {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
               "metadata": {"agent_assign_count": 3, "handoff_reason": "auto_assign"}}
        upd = AsyncMock(return_value=("written", ""))
        with patch.object(handoff.db, "get_conversation_simple", new=AsyncMock(return_value=row)), \
             patch.object(handoff.db, "update_conversation_if_clock_unchanged", upd), \
             patch.object(handoff.db, "get_user_by_id", new=AsyncMock(return_value={})), \
             patch("app.services.presence.log_activity", new=AsyncMock()), \
             patch("app.pipeline.orchestrator._fire_and_forget",
                   new=MagicMock(side_effect=lambda coro: coro.close())):
            await handoff.perform_handoff_to_agent(
                _CID, "me", emit_messages=False, handoff_reason="manual_claim",
                _pop_meta_keys=["agent_assign_count", "agent_cooldown_until"],
            )
        meta = upd.await_args.args[1]["metadata"]
        assert "agent_assign_count" not in meta
        assert meta["handoff_reason"] == "auto_assign", "not a Take: the reason stays"

    @pytest.mark.asyncio
    async def test_a_take_whose_answer_was_lost_still_gets_the_clock(self):
        """The gate's write landed but its answer did not: the endpoint finds
        the chat is mine after all ("already mine"). That is my Take."""
        from app.api import conversations as capi

        conv = {"id": _CID, "assigned_agent_id": None, "status": "needs_agent",
                "tunnel": "sales", "metadata": {}}
        mine = {"id": _CID, "assigned_agent_id": "me", "mode": "human",
                "status": "active", "metadata": {}}
        upd = AsyncMock(return_value=("written", ""))
        activity = AsyncMock()
        with patch.object(sb, "get_conversation", new=AsyncMock(return_value=conv)), \
             patch.object(sb, "get_user_by_id",
                          new=AsyncMock(return_value={"id": "me", "name": "Ana", "role": "sales"})), \
             patch.object(sb, "claim_conversation_if_unassigned",
                          new=AsyncMock(return_value={"won": False, "queued_age_seconds": None})), \
             patch.object(sb, "get_conversation_simple", new=AsyncMock(return_value=mine)), \
             patch.object(sb, "update_conversation_if_clock_unchanged", upd), \
             patch.object(sb, "update_conversation", new=AsyncMock()), \
             patch("app.services.presence.log_activity", activity), \
             patch("app.pipeline.orchestrator._fire_and_forget",
                   new=MagicMock(side_effect=lambda coro: coro.close())):
            out = await capi.claim_conversation(conversation_id=_CID, user=dict(self._ME))
        assert out["data"]["assigned_to"] == "me"
        payload = upd.await_args.args[1]
        assert "assigned_agent_id" not in payload, "ownership is the gate's"
        assert 0 <= _iso_age_s(payload["metadata"]["agent_assigned_at"]) < 5
        activity.assert_not_called()  # no claim_won twice, no 'assigned'

    @pytest.mark.asyncio
    async def test_a_new_owner_is_still_the_full_new_cycle(self):
        """The other branch, unchanged: the row names someone else (or nobody),
        so the cycle is new: engaged, the clock, one 'assigned' row."""
        row = {"id": _CID, "assigned_agent_id": None, "metadata": {}}
        upd = AsyncMock()
        activity = AsyncMock()
        with patch.object(handoff.db, "get_conversation_simple", new=AsyncMock(return_value=row)), \
             patch.object(handoff.db, "claim_conversation_if_unassigned",
                          new=AsyncMock(return_value={"won": True, "queued_age_seconds": None})), \
             patch.object(handoff.db, "update_conversation", upd), \
             patch.object(handoff.db, "get_user_by_id", new=AsyncMock(return_value={})), \
             patch("app.services.presence.log_activity", activity), \
             patch("app.pipeline.orchestrator._fire_and_forget",
                   new=MagicMock(side_effect=lambda coro: coro.close())):
            await handoff.perform_handoff_to_agent(
                _CID, "agent-A", emit_messages=False, handoff_reason="first_message"
            )
        meta = upd.await_args.args[1]["metadata"]
        assert meta["engaged_agent_id"] == "agent-A"
        assert meta["announce_pending"] is True
        assert 0 <= _iso_age_s(meta["agent_assigned_at"]) < 5
        assert [c.args[3] for c in activity.call_args_list] == ["assigned"]

    async def _sweep_after_take(self, *, agent_wrote_after_s=None):
        """The metadata a Take writes, read by the real deadline sweep once the
        deadline has passed."""
        from app.api.agent import _enforce_response_deadline
        from config.settings import settings

        ago = settings.agent_first_response_timeout_seconds + 5

        class _Past(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) - timedelta(seconds=ago)

        row = {"id": _CID, "assigned_agent_id": "agent-A", "mode": "human",
               "status": "active", "metadata": {}}
        upd = AsyncMock(return_value=("written", ""))
        with patch.object(handoff, "datetime", _Past), \
             patch.object(handoff.db, "get_conversation_simple", new=AsyncMock(return_value=row)), \
             patch.object(handoff.db, "update_conversation_if_clock_unchanged", upd), \
             patch.object(handoff.db, "get_user_by_id", new=AsyncMock(return_value={})), \
             patch("app.services.presence.log_activity", new=AsyncMock()), \
             patch("app.pipeline.orchestrator._fire_and_forget",
                   new=MagicMock(side_effect=lambda coro: coro.close())):
            await handoff.perform_handoff_to_agent(
                _CID, "agent-A", emit_messages=False, handoff_reason="manual_claim", _gate_won=True
            )
        meta = upd.await_args.args[1]["metadata"]
        swept = {**row, "metadata": meta, "last_agent_message_at": None}
        if agent_wrote_after_s is not None:
            taken = datetime.fromisoformat(meta["agent_assigned_at"])
            swept["last_agent_message_at"] = (taken + timedelta(seconds=agent_wrote_after_s)).isoformat()
        with patch("app.api.agent.db.get_active_human_conversations",
                   new=AsyncMock(return_value=[swept])), \
             patch("app.services.handoff.fall_back_to_ai", new=AsyncMock()) as fallback:
            count = await _enforce_response_deadline()
        return count, fallback

    @pytest.mark.asyncio
    async def test_a_take_nobody_answers_goes_back_to_alex(self):
        count, fallback = await self._sweep_after_take()
        assert count == 1
        fallback.assert_awaited_once_with(_CID)

    @pytest.mark.asyncio
    async def test_a_take_answered_in_time_stays_with_the_agent(self):
        count, fallback = await self._sweep_after_take(agent_wrote_after_s=20)
        assert count == 0
        fallback.assert_not_awaited()

    async def _recovered_take(self, read_row):
        """The endpoint's "already mine" path: the gate lost, the re-read finds
        the chat is mine. read_row() builds the row each time it is read, that
        is after the endpoint noted when it saw the chat unassigned."""
        from app.api import conversations as capi

        conv = {"id": _CID, "assigned_agent_id": None, "status": "needs_agent",
                "tunnel": "sales", "metadata": {}}
        cas = AsyncMock(return_value=("written", ""))
        plain = AsyncMock()
        with patch.object(sb, "get_conversation", new=AsyncMock(return_value=conv)), \
             patch.object(sb, "get_user_by_id",
                          new=AsyncMock(return_value={"id": "me", "name": "Ana", "role": "sales"})), \
             patch.object(sb, "claim_conversation_if_unassigned",
                          new=AsyncMock(return_value={"won": False, "queued_age_seconds": None})), \
             patch.object(sb, "get_conversation_simple",
                          new=AsyncMock(side_effect=lambda cid: read_row())), \
             patch.object(sb, "update_conversation_if_clock_unchanged", cas), \
             patch.object(sb, "update_conversation", plain), \
             patch("app.services.presence.log_activity", new=AsyncMock()), \
             patch("app.pipeline.orchestrator._fire_and_forget",
                   new=MagicMock(side_effect=lambda coro: coro.close())):
            out = await capi.claim_conversation(conversation_id=_CID, user=dict(self._ME))
        assert out["data"]["assigned_to"] == "me"
        return cas, plain

    @pytest.mark.asyncio
    async def test_a_second_request_keeps_the_clock_the_first_one_started(self):
        """CodeRabbit #280, round 2: the request that won wrote the clock, and
        the agent answered before the "already mine" request reached its write.
        A clock started again would come after the answer, and the sweep counts
        only an answer at or after agent_assigned_at: an answered chat would go
        back to Alex. The clock is this cycle's, so it is kept, untouched."""
        def _row():
            now = datetime.now(timezone.utc).isoformat()
            return {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
                    "metadata": {"agent_assigned_at": now, "handoff_reason": "manual_claim",
                                 "clock_agent_id": "me", "engaged_agent_id": "me"},
                    "last_agent_message_at": now}

        cas, plain = await self._recovered_take(_row)
        cas.assert_not_awaited()
        plain.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_running_clock_is_kept_before_any_answer_too(self):
        """The usual double click: the winner's clock is there, nobody has
        written yet. The second request does not move it a moment later."""
        def _row():
            return {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
                    "metadata": {"agent_assigned_at": datetime.now(timezone.utc).isoformat(),
                                 "handoff_reason": "manual_claim", "clock_agent_id": "me"},
                    "last_agent_message_at": None}

        cas, plain = await self._recovered_take(_row)
        cas.assert_not_awaited()
        plain.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_answer_in_this_cycle_is_never_put_before_a_new_clock(self):
        """The request that won died before writing the clock, and the agent has
        already answered: a clock started now would postdate the answer."""
        def _row():
            # The send marks the agent who wrote as engaged (conversations.py).
            return {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
                    "metadata": {"engaged_agent_id": "me"},
                    "last_agent_message_at": datetime.now(timezone.utc).isoformat()}

        cas, plain = await self._recovered_take(_row)
        cas.assert_not_awaited()
        plain.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_take_after_an_older_cycle_starts_a_fresh_clock(self):
        """The branch the fix does not take: the clock and the answer the row
        still carries are older than the moment this Take saw the chat
        unassigned, so they belong to an earlier cycle and the take starts its
        own clock, as before. The write still checks that nothing moved."""
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _out, upd, _activity = await self._take({"agent_assigned_at": old}, last_agent_message_at=old)
        upd.assert_awaited_once()
        assert upd.await_args.kwargs["clock"] == old
        assert upd.await_args.kwargs["last_agent_message_at"] == old
        assert upd.await_args.kwargs["check_last_agent"] is True
        assert 0 <= _iso_age_s(upd.await_args.args[1]["metadata"]["agent_assigned_at"]) < 5

    @pytest.mark.asyncio
    async def test_the_clock_is_written_only_if_nobody_wrote_it_meanwhile(self):
        """The condition holds where the write lands, not on the copy read: the
        winner's clock arriving between this request's read and its write stays
        exactly as the winner wrote it."""
        floor = datetime.now(timezone.utc) - timedelta(seconds=1)
        store = {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
                 "metadata": {}, "last_agent_message_at": None}
        first_read = {**store, "metadata": {}}
        winner_clock = datetime.now(timezone.utc).isoformat()
        store["metadata"] = {"agent_assigned_at": winner_clock, "handoff_reason": "manual_claim",
                             "clock_agent_id": "me"}
        cas, plain = self._store_with_cas(store)
        reads = iter([first_read, {**store, "metadata": dict(store["metadata"])}])
        with patch.object(handoff.db, "get_conversation_simple",
                          new=AsyncMock(side_effect=lambda cid: next(reads))), \
             patch.object(handoff.db, "update_conversation_if_clock_unchanged", cas), \
             patch.object(handoff.db, "update_conversation", plain), \
             patch.object(handoff.db, "get_user_by_id", new=AsyncMock(return_value={})), \
             patch("app.services.presence.log_activity", new=AsyncMock()), \
             patch("app.pipeline.orchestrator._fire_and_forget",
                   new=MagicMock(side_effect=lambda coro: coro.close())):
            await handoff.perform_handoff_to_agent(
                _CID, "me", emit_messages=False, handoff_reason="manual_claim",
                _gate_won=True, _seen_unassigned_at=floor,
            )
        assert store["metadata"]["agent_assigned_at"] == winner_clock, "not restarted"
        assert cas.await_count == 1, "one conditional write, which found the clock moved"
        plain.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_conditional_write_that_breaks_is_counted(self):
        """Money path (CLAUDE.md rule 2): a take whose clock did not land is
        counted in /health, not only logged."""
        row = {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
               "metadata": {}}
        failed = handoff.HANDOFF_HEALTH["same_cycle_write_failed"]
        with patch.object(handoff.db, "get_conversation_simple", new=AsyncMock(return_value=row)), \
             patch.object(handoff.db, "update_conversation_if_clock_unchanged",
                          new=AsyncMock(return_value=("failed", "57014"))), \
             patch.object(handoff.db, "update_conversation", new=AsyncMock()), \
             patch.object(handoff.db, "get_user_by_id", new=AsyncMock(return_value={})), \
             patch("app.services.presence.log_activity", new=AsyncMock()), \
             patch("app.pipeline.orchestrator._fire_and_forget",
                   new=MagicMock(side_effect=lambda coro: coro.close())):
            await handoff.perform_handoff_to_agent(
                _CID, "me", emit_messages=False, handoff_reason="manual_claim", _gate_won=True,
            )
        assert handoff.HANDOFF_HEALTH["same_cycle_write_failed"] == failed + 1


    @staticmethod
    def _handoff_patches(read, cas, plain=None):
        return [
            patch.object(handoff.db, "get_conversation_simple", new=AsyncMock(side_effect=read)),
            patch.object(handoff.db, "update_conversation_if_clock_unchanged", cas),
            patch.object(handoff.db, "update_conversation", plain or AsyncMock()),
            patch.object(handoff.db, "get_user_by_id", new=AsyncMock(return_value={})),
            patch("app.services.presence.log_activity", new=AsyncMock()),
            patch("app.pipeline.orchestrator._fire_and_forget",
                  new=MagicMock(side_effect=lambda coro: coro.close())),
        ]

    async def _gate_won_take(self, read, cas, plain=None, floor=None):
        ps = self._handoff_patches(read, cas, plain)
        for p_ in ps:
            p_.start()
        try:
            await handoff.perform_handoff_to_agent(
                _CID, "me", emit_messages=False, handoff_reason="manual_claim",
                _gate_won=True, _seen_unassigned_at=floor,
            )
        finally:
            for p_ in reversed(ps):
                p_.stop()

    @pytest.mark.asyncio
    async def test_another_agents_answer_since_the_read_does_not_stand_for_this_take(self):
        """CodeRabbit #280, round 4: between this Take's read and its gate, agent B
        was given the chat, answered and let it go. The answer stays on the row
        (and B as engaged), newer than the read but not this take's: the take
        starts its own clock, or the chat would keep no deadline at all."""
        floor = datetime.now(timezone.utc) - timedelta(seconds=5)
        b_answer = (floor + timedelta(seconds=2)).isoformat()
        row = {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
               "metadata": {"engaged_agent_id": "agent-B"}, "last_agent_message_at": b_answer}
        cas = AsyncMock(return_value=("written", ""))
        await self._gate_won_take(lambda cid: dict(row, metadata=dict(row["metadata"])), cas, floor=floor)
        cas.assert_awaited_once()
        meta = cas.await_args.args[1]["metadata"]
        assert 0 <= _iso_age_s(meta["agent_assigned_at"]) < 5
        assert meta["clock_agent_id"] == "me" and meta["engaged_agent_id"] == "agent-B"
        assert cas.await_args.kwargs["last_agent_message_at"] == b_answer, "the write still checks nothing moved"

    @pytest.mark.asyncio
    async def test_another_agents_clock_since_the_read_does_not_stand_for_this_take(self):
        floor = datetime.now(timezone.utc) - timedelta(seconds=5)
        b_clock = (floor + timedelta(seconds=1)).isoformat()
        for meta in ({"agent_assigned_at": b_clock, "clock_agent_id": "agent-B"},
                     {"agent_assigned_at": b_clock, "engaged_agent_id": "agent-B"}):
            row = {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
                   "metadata": meta, "last_agent_message_at": None}
            cas = AsyncMock(return_value=("written", ""))
            await self._gate_won_take(lambda cid, r=row: dict(r, metadata=dict(r["metadata"])), cas, floor=floor)
            written = cas.await_args.args[1]["metadata"]
            assert written["agent_assigned_at"] != b_clock and written["clock_agent_id"] == "me"

    @pytest.mark.asyncio
    async def test_a_clock_written_before_clock_agent_id_still_counts_by_engaged(self):
        """The other side: a clock from before this change names its agent only
        through engaged_agent_id, and this agent's is kept."""
        floor = datetime.now(timezone.utc) - timedelta(seconds=5)
        row = {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
               "metadata": {"agent_assigned_at": datetime.now(timezone.utc).isoformat(),
                            "engaged_agent_id": "me"},
               "last_agent_message_at": None}
        cas = AsyncMock(return_value=("written", ""))
        await self._gate_won_take(lambda cid: dict(row, metadata=dict(row["metadata"])), cas, floor=floor)
        cas.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_crm_receipt_saved_since_the_read_is_never_erased(self):
        """CodeRabbit #280, round 4: the button's receipt (metadata.crm_lead) is
        merged in server-side. A handoff that read the chat before it and wrote
        its whole copy after it erased it: the panel offered the button again
        and the sweep could send a second lead. The write now waits for the
        receipt to be read, and carries it."""
        floor = datetime.now(timezone.utc) - timedelta(seconds=1)
        store = {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
                 "metadata": {}, "last_agent_message_at": None}
        first_read = {**store, "metadata": {}}
        receipt = {"flight_request_id": 98765, "url": "https://crm.example.test/requests/98765"}
        store["metadata"] = {"crm_lead": receipt}  # saved between the read and the write
        cas, plain = self._store_with_cas(store)
        reads = iter([first_read, {**store, "metadata": dict(store["metadata"])}])
        await self._gate_won_take(lambda cid: next(reads), cas, plain, floor=floor)
        assert store["metadata"]["crm_lead"] == receipt, "the receipt survives"
        assert 0 <= _iso_age_s(store["metadata"]["agent_assigned_at"]) < 5, "and the take still gets its clock"
        assert cas.await_count == 2, "the first write found the receipt and wrote nothing"
        assert cas.await_args_list[0].kwargs["absent_keys"] == ("crm_lead", "crm")
        assert cas.await_args_list[1].kwargs["absent_keys"] == ("crm",), "a receipt in the copy needs no guard"

    @pytest.mark.asyncio
    async def test_a_write_that_raised_but_landed_is_not_called_a_failure(self):
        """CodeRabbit #280, round 4: the UPDATE landed and its answer was lost.
        The row is read back first: the clock is there, so nothing is counted
        and nothing is written twice."""
        store = {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
                 "metadata": {}, "last_agent_message_at": None}

        async def _landed_then_raised(cid, payload, **kw):
            store.update(payload)
            return ("failed", "none")

        cas = AsyncMock(side_effect=_landed_then_raised)
        failed = handoff.HANDOFF_HEALTH["same_cycle_write_failed"]
        unknown = handoff.HANDOFF_HEALTH["same_cycle_write_unknown"]
        await self._gate_won_take(lambda cid: {**store, "metadata": dict(store["metadata"])}, cas)
        cas.assert_awaited_once()
        assert 0 <= _iso_age_s(store["metadata"]["agent_assigned_at"]) < 5
        assert handoff.HANDOFF_HEALTH["same_cycle_write_failed"] == failed
        assert handoff.HANDOFF_HEALTH["same_cycle_write_unknown"] == unknown

    @pytest.mark.asyncio
    async def test_a_write_that_raised_with_no_read_back_is_unknown_not_failed(self):
        row = {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
               "metadata": {}, "last_agent_message_at": None}
        reads = iter([dict(row), None])
        cas = AsyncMock(return_value=("failed", "none"))
        failed = handoff.HANDOFF_HEALTH["same_cycle_write_failed"]
        unknown = handoff.HANDOFF_HEALTH["same_cycle_write_unknown"]
        await self._gate_won_take(lambda cid: next(reads), cas)
        cas.assert_awaited_once()
        assert handoff.HANDOFF_HEALTH["same_cycle_write_unknown"] == unknown + 1
        assert handoff.HANDOFF_HEALTH["same_cycle_write_failed"] == failed

    @pytest.mark.asyncio
    async def test_a_write_that_failed_is_tried_once_more_and_the_clock_lands(self):
        """CodeRabbit #280, round 4: a failed clock write left the Take with no
        deadline. Read back and absent, it is written once more from the row."""
        store = {"id": _CID, "assigned_agent_id": "me", "mode": "human", "status": "active",
                 "metadata": {}, "last_agent_message_at": None}
        outcomes = iter([("failed", "57014"), None])

        async def _cas(cid, payload, **kw):
            out = next(outcomes)
            if out:
                return out
            store.update(payload)
            return ("written", "")

        cas = AsyncMock(side_effect=_cas)
        failed = handoff.HANDOFF_HEALTH["same_cycle_write_failed"]
        await self._gate_won_take(lambda cid: {**store, "metadata": dict(store["metadata"])}, cas)
        assert cas.await_count == 2
        assert 0 <= _iso_age_s(store["metadata"]["agent_assigned_at"]) < 5
        assert store["metadata"]["clock_agent_id"] == "me"
        assert handoff.HANDOFF_HEALTH["same_cycle_write_failed"] == failed, "it landed: not a failure"

    @pytest.mark.asyncio
    async def test_the_release_takes_the_clocks_agent_with_the_clock(self):
        captured = []

        async def _release(_id, _agent, metadata, reason="agent_offline"):
            captured.append(metadata)
            return False  # lost the race: nothing else happens

        conv = {"id": _CID, "assigned_agent_id": "me", "mode": "human",
                "metadata": {"agent_assigned_at": "T", "clock_agent_id": "me", "engaged_agent_id": "me"}}
        with patch.object(handoff.db, "get_conversation_simple", new=AsyncMock(return_value=conv)), \
             patch.object(handoff, "_release_from_agent", new=AsyncMock(side_effect=_release)):
            await handoff.fall_back_to_ai(_CID)
        assert "agent_assigned_at" not in captured[0] and "clock_agent_id" not in captured[0]
        assert captured[0]["engaged_agent_id"] == "me", "the engaged agent stays (stickiness)"


class TestClockConditionalWrite:
    """update_conversation_if_clock_unchanged against a row that honours its WHERE clause."""

    async def _write(self, row, **kw):
        table = _ConvTable(row)
        with patch.object(sb, "get_client", return_value=table), \
             patch.object(sb, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())):
            out = await sb.update_conversation_if_clock_unchanged(_CID, {"metadata": {"x": 1}}, **kw)
        return out, table

    @pytest.mark.asyncio
    async def test_writes_while_the_clock_is_what_was_read(self):
        out, table = await self._write({"id": _CID, "metadata": {"agent_assigned_at": "T1"}}, clock="T1")
        assert out == ("written", "")
        assert table.row["metadata"] == {"x": 1}

    @pytest.mark.asyncio
    async def test_a_clock_read_as_absent_must_still_be_absent(self):
        out, table = await self._write({"id": _CID, "metadata": {"agent_assigned_at": "T2"}}, clock=None)
        assert out == ("changed", "")
        assert table.row["metadata"] == {"agent_assigned_at": "T2"} and table.update_count == 0

    @pytest.mark.asyncio
    async def test_a_moved_clock_writes_nothing(self):
        out, table = await self._write({"id": _CID, "metadata": {"agent_assigned_at": "T2"}}, clock="T1")
        assert out == ("changed", "")
        assert table.update_count == 0

    @pytest.mark.asyncio
    async def test_an_answer_since_the_read_blocks_it_only_when_asked(self):
        answered = {"id": _CID, "metadata": {}, "last_agent_message_at": "R1"}
        out, _ = await self._write(dict(answered), clock=None, last_agent_message_at=None,
                                   check_last_agent=True)
        assert out == ("changed", "")
        out, _ = await self._write(dict(answered), clock=None)
        assert out == ("written", ""), "not asked: the clock alone decides"

    @pytest.mark.asyncio
    async def test_a_broken_write_says_failed_not_changed(self):
        table = _ConvTable({"id": _CID, "metadata": {}})
        table.fail_update = True
        with patch.object(sb, "get_client", return_value=table), \
             patch.object(sb, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())):
            out = await sb.update_conversation_if_clock_unchanged(_CID, {"metadata": {}}, clock=None)
        assert out[0] == "failed"


    @pytest.mark.asyncio
    async def test_a_receipt_absent_in_the_read_must_still_be_absent(self):
        row = {"id": _CID, "metadata": {"crm_lead": {"flight_request_id": 1}}}
        out, table = await self._write(row, clock=None, absent_keys=("crm_lead", "crm"))
        assert out == ("changed", "")
        assert table.update_count == 0 and row["metadata"] == {"crm_lead": {"flight_request_id": 1}}
        out, _ = await self._write({"id": _CID, "metadata": {}}, clock=None, absent_keys=("crm_lead", "crm"))
        assert out == ("written", ""), "still absent: written"

    @pytest.mark.asyncio
    async def test_the_owner_must_still_hold_the_chat_when_asked(self):
        row = {"id": _CID, "assigned_agent_id": "agent-B", "metadata": {}}
        out, _ = await self._write(dict(row), clock=None, owner="me")
        assert out == ("changed", "")
        out, _ = await self._write(dict(row), clock=None, owner="agent-B")
        assert out == ("written", "")
