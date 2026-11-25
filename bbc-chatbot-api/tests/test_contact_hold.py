"""Lever 2 (25 Nov 2026): Take waits while Alex gets the client's contact (app.services.contact_hold).

17-24 Nov: 98 chats where an agent wrote before the contact got one in 22 (22%), against 59% of the chats Alex
answered alone; the agents came in a median 49 s after the first message. The hold sits on the take: two minutes from
the client's first message, unless the contact is in or the client asked for a person.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoieCJ9.sig")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import HTTPException

from app.db import supabase as sb
from app.services import contact_hold

_CID = "conv-hold"
NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)


def _chat(**over):
    """A fresh card chat Alex answers alone: in the line 30 s ago, no contact, nobody on it."""
    row = {
        "id": _CID, "tunnel": "sales", "status": "active", "mode": "ai", "assigned_agent_id": None,
        "queued_at": (NOW - timedelta(seconds=30)).isoformat(), "created_at": (NOW - timedelta(seconds=31)).isoformat(),
        "metadata": {}, "visitor_email": None, "visitor_phone": None,
    }
    row.update(over)
    return row


@pytest.fixture(autouse=True)
def _flags():
    with patch("app.flags._snapshot", {}):
        yield


# ── the rule ─────────────────────────────────────────────────────────────────────────────────────────────────────

class TestTheRule:
    def test_a_fresh_chat_without_contact_waits_two_minutes_from_the_first_message(self):
        until = contact_hold.hold_until(_chat(), has_contact=False, now=NOW)
        assert until == NOW - timedelta(seconds=30) + timedelta(seconds=120)
        assert contact_hold.seconds_left(until, NOW) == 90

    def test_the_contact_ends_the_hold(self):
        assert contact_hold.hold_until(_chat(), has_contact=True, now=NOW) is None

    def test_a_client_who_asked_for_a_person_is_never_held(self):
        # needs_agent: an ask in words, "I'll wait here", a callback, a number given (routing.queue_for_person).
        assert contact_hold.hold_until(_chat(status="needs_agent"), has_contact=False, now=NOW) is None

    def test_the_hold_runs_out(self):
        old = _chat(queued_at=(NOW - timedelta(seconds=121)).isoformat())
        assert contact_hold.hold_until(old, has_contact=False, now=NOW) is None

    @pytest.mark.parametrize("over", [
        {"tunnel": "support"},
        {"assigned_agent_id": "agent-1"},
        {"metadata": {"engaged_agent_id": "agent-1"}},
        {"engaged_agent_id": "agent-1"},
        {"mode": "human"},
        {"mode": "waiting_for_agent"},
        {"status": "closed"},
    ])
    def test_the_chats_it_is_not_for(self, over):
        assert contact_hold.hold_until(_chat(**over), has_contact=False, now=NOW) is None

    def test_without_the_line_time_it_counts_from_the_chat_creation(self):
        chat = _chat(queued_at=None, created_at=(NOW - timedelta(seconds=100)).isoformat())
        assert contact_hold.hold_until(chat, has_contact=False, now=NOW) == NOW + timedelta(seconds=20)

    def test_a_chat_with_no_time_at_all_is_not_held_and_not_counted(self):
        before = contact_hold.HOLD_HEALTH["unreadable_time"]
        assert contact_hold.hold_until(_chat(queued_at=None, created_at=None), has_contact=False, now=NOW) is None
        assert contact_hold.HOLD_HEALTH["unreadable_time"] == before

    def test_an_unreadable_time_is_not_held_and_is_counted(self):
        before = contact_hold.HOLD_HEALTH["unreadable_time"]
        assert contact_hold.hold_until(_chat(queued_at="not a time"), has_contact=False, now=NOW) is None
        assert contact_hold.HOLD_HEALTH["unreadable_time"] == before + 1

    def test_a_naive_time_reads_as_utc(self):
        naive = (NOW - timedelta(seconds=60)).replace(tzinfo=None).isoformat()
        assert contact_hold.hold_until(_chat(queued_at=naive), has_contact=False, now=NOW) == NOW + timedelta(seconds=60)


class TestTheSwitch:
    def test_zero_turns_it_off(self):
        with patch("app.flags._snapshot", {"CONTACT_HOLD_SECONDS": 0}):
            assert contact_hold.hold_until(_chat(), has_contact=False, now=NOW) is None

    def test_another_length_is_read(self):
        with patch("app.flags._snapshot", {"CONTACT_HOLD_SECONDS": 60}):
            assert contact_hold.hold_until(_chat(), has_contact=False, now=NOW) == NOW + timedelta(seconds=30)

    def test_a_typo_never_locks_a_chat_away_for_hours(self):
        with patch("app.flags._snapshot", {"CONTACT_HOLD_SECONDS": 12000}):
            assert contact_hold.hold_seconds() == contact_hold.MAX_HOLD_S

    def test_a_negative_value_is_off(self):
        with patch("app.flags._snapshot", {"CONTACT_HOLD_SECONDS": -5}):
            assert contact_hold.hold_seconds() == 0

    def test_a_value_that_is_not_a_number_keeps_two_minutes(self):
        with patch("app.flags._snapshot", {"CONTACT_HOLD_SECONDS": "two minutes"}):
            assert contact_hold.hold_seconds() == 120


class TestTheAgentsWait:
    def test_a_chat_held_the_whole_hold_is_measured_from_its_end(self):
        assert contact_hold.wait_after_hold(150, _chat(), has_contact=False) == 30

    def test_a_chat_with_a_contact_keeps_the_lines_wait(self):
        assert contact_hold.wait_after_hold(150, _chat(), has_contact=True) == 150

    def test_a_client_who_asked_for_a_person_keeps_the_lines_wait(self):
        assert contact_hold.wait_after_hold(150, _chat(status="needs_agent"), has_contact=False) == 150

    def test_no_age_stays_none(self):
        assert contact_hold.wait_after_hold(None, _chat(), has_contact=False) is None


# ── Take ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def _live_chat(seconds_ago: int, **over):
    """_chat with its first message `seconds_ago` before the real clock (the endpoint reads the real time)."""
    now = datetime.now(timezone.utc)
    return _chat(queued_at=(now - timedelta(seconds=seconds_ago)).isoformat(), **over)


class TestTake:
    _ME = {"id": "me", "role": "sales", "tunnel_scope": "sales"}

    async def _press_take(self, conv, *, won=True, age=None):
        from app.api import conversations as capi

        gate = AsyncMock(return_value={"won": won, "queued_age_seconds": age})
        handoff_call = AsyncMock(return_value={})
        activity = AsyncMock()
        before = contact_hold.HOLD_HEALTH["refused"]
        with patch.object(sb, "get_conversation", new=AsyncMock(return_value=conv)), \
             patch.object(sb, "get_user_by_id", new=AsyncMock(return_value={"id": "me", "name": "Ana", "role": "sales"})), \
             patch.object(sb, "claim_conversation_if_unassigned", gate), \
             patch("app.services.handoff.perform_handoff_to_agent", handoff_call), \
             patch("app.services.presence.log_activity", activity):
            try:
                out = await capi.claim_conversation(conversation_id=_CID, user=dict(self._ME))
            except HTTPException as e:
                out = e
        return out, gate, handoff_call, activity, contact_hold.HOLD_HEALTH["refused"] - before

    @pytest.mark.asyncio
    async def test_a_take_while_alex_gets_the_contact_waits_and_says_how_long(self):
        chat = _live_chat(30)
        out, gate, handoff_call, activity, refused = await self._press_take(chat)
        assert isinstance(out, HTTPException) and out.status_code == 409
        assert out.detail["detail"] == "contact_hold"
        assert 89 <= out.detail["retry_after"] <= 91
        assert datetime.fromisoformat(out.detail["hold_until"]) == (
            datetime.fromisoformat(chat["queued_at"]) + timedelta(seconds=120))
        assert refused == 1
        gate.assert_not_awaited()
        handoff_call.assert_not_awaited()
        activity.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_client_who_asked_for_a_person_is_taken_at_once(self):
        out, gate, handoff_call, activity, refused = await self._press_take(_live_chat(30, status="needs_agent"), age=20)
        assert out == {"success": True, "data": {"conversation_id": _CID, "assigned_to": "me"}}
        assert refused == 0
        gate.assert_awaited_once()
        handoff_call.assert_awaited_once()
        assert activity.await_args.kwargs["response_seconds"] == 20

    @pytest.mark.asyncio
    async def test_a_chat_with_the_contact_in_is_taken_at_once(self):
        out, gate, _h, activity, refused = await self._press_take(_live_chat(30, visitor_email="x@example.com"), age=40)
        assert out["success"] is True and refused == 0
        gate.assert_awaited_once()
        assert activity.await_args.kwargs["response_seconds"] == 40

    @pytest.mark.asyncio
    async def test_after_the_hold_the_take_goes_and_the_wait_starts_at_its_end(self):
        out, gate, _h, activity, refused = await self._press_take(_live_chat(150), age=150)
        assert out["success"] is True and refused == 0
        gate.assert_awaited_once()
        assert activity.await_args.kwargs["response_seconds"] == 30

    @pytest.mark.asyncio
    async def test_my_own_chat_is_never_held(self):
        out, gate, handoff_call, _a, refused = await self._press_take(_live_chat(30, assigned_agent_id="me"))
        assert out["success"] is True and refused == 0
        gate.assert_not_awaited()
        handoff_call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_with_the_switch_off_the_take_goes_as_before(self):
        with patch("app.flags._snapshot", {"CONTACT_HOLD_SECONDS": 0}):
            out, gate, _h, activity, refused = await self._press_take(_live_chat(30), age=30)
        assert out["success"] is True and refused == 0
        assert activity.await_args.kwargs["response_seconds"] == 30


# ── what the panel reads ─────────────────────────────────────────────────────────────────────────────────────────

class TestThePanelReadsTheSameRule:
    @pytest.mark.asyncio
    async def test_a_waiting_row_carries_its_hold_even_though_its_contact_is_blinded(self):
        from app.api.conversations import list_conversations

        rows = [
            {**_chat(), "has_contact": False},
            {**_chat(id="c2", status="needs_agent"), "has_contact": False},
        ]
        until = (datetime.now(timezone.utc) + timedelta(seconds=60))
        rows[0]["queued_at"] = (until - timedelta(seconds=120)).isoformat()
        with patch("app.api.conversations.db.get_conversations", new=AsyncMock(return_value=(rows, 2))), \
             patch("app.api.conversations.db.get_last_message_previews", new=AsyncMock(return_value={})):
            res = await list_conversations(
                tunnel=None, status="active", assigned_to="none", search=None, handled_by=None, tag=None,
                limit=50, offset=0, user={"role": "sales", "id": "me", "tunnel_scope": "sales"},
            )
        assert res["success"] is True
        first, second = res["data"]
        assert first["contact_hold_until"] == until.isoformat()
        assert second["contact_hold_until"] is None

    @pytest.mark.asyncio
    async def test_the_detail_carries_it_through_the_blind_view(self):
        from app.api.conversations import get_conversation

        until = (datetime.now(timezone.utc) + timedelta(seconds=45))
        conv = _chat(queued_at=(until - timedelta(seconds=120)).isoformat())
        with patch("app.api.conversations.db.get_conversation", new=AsyncMock(return_value=conv)), \
             patch("app.services.recent_chats.remember", new=AsyncMock()):
            res = await get_conversation(conversation_id=_CID, user={"role": "sales", "id": "me", "tunnel_scope": "sales"})
        assert res["data"]["contact_hold_until"] == until.isoformat()
        assert res["data"]["has_contact"] is False

    def test_health_shows_it_on_both_pages(self):
        import pathlib

        src = (pathlib.Path(__file__).resolve().parent.parent / "app/api/health.py").read_text(encoding="utf-8")
        assert src.count('"contact_hold": dict(CONTACT_HOLD_HEALTH),') == 2
