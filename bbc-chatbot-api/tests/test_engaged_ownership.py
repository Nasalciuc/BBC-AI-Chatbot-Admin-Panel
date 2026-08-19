"""Engaged ownership — "My conversations" must include engaged-then-fallback chats.

After AI fallback, assigned_agent_id is NULL but metadata.engaged_agent_id
survives. The list filter must OR those two so the operator who engaged can
still find and re-claim the conversation.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.db import supabase as db

STEVE = "steve-uuid-1111"
OTHER = "other-uuid-2222"


class _FakeQuery:
    """Records supabase-py filter chain calls for assertion."""

    def __init__(self):
        self.calls: list[tuple] = []

    def select(self, *a, **k):
        self.calls.append(("select", a, k))
        return self

    def order(self, *a, **k):
        self.calls.append(("order", a, k))
        return self

    def eq(self, col, val):
        self.calls.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.calls.append(("in_", col, vals))
        return self

    def is_(self, col, val):
        self.calls.append(("is_", col, val))
        return self

    def or_(self, filters: str):
        self.calls.append(("or_", filters))
        return self

    def range(self, start, end):
        self.calls.append(("range", start, end))
        return self

    def execute(self):
        res = MagicMock()
        res.data = []
        res.count = 0
        return res


def _agent_ors(calls: list[tuple]) -> list[str]:
    return [c[1] for c in calls if c[0] == "or_" and "assigned_agent_id" in c[1]]


def _eq_calls(calls: list[tuple], col: str) -> list:
    return [c[2] for c in calls if c[0] == "eq" and c[1] == col]


@pytest.mark.asyncio
async def test_my_filter_ors_assigned_and_engaged():
    """Core fix: agent_id path uses OR(assigned, engaged), not eq(assigned) alone."""
    fq = _FakeQuery()
    fake_db = MagicMock()
    fake_db.table.return_value = fq

    with (
        patch.object(db, "get_client", return_value=fake_db),
        patch.object(db, "enrich_conversations_agent_info", new_callable=AsyncMock, side_effect=lambda rows: rows),
    ):
        await db.get_conversations(agent_id=STEVE, limit=50, offset=0)

    agent_ors = _agent_ors(fq.calls)
    assert len(agent_ors) == 1
    filt = agent_ors[0]
    assert f"assigned_agent_id.eq.{STEVE}" in filt
    assert f"metadata->>engaged_agent_id.eq.{STEVE}" in filt
    # Must not be a plain assigned-only eq
    assert ("eq", "assigned_agent_id", STEVE) not in fq.calls


@pytest.mark.asyncio
async def test_still_assigned_covered_by_or():
    """Still-assigned conversations match the assigned_agent_id arm of the OR."""
    fq = _FakeQuery()
    fake_db = MagicMock()
    fake_db.table.return_value = fq

    with (
        patch.object(db, "get_client", return_value=fake_db),
        patch.object(db, "enrich_conversations_agent_info", new_callable=AsyncMock, side_effect=lambda rows: rows),
    ):
        await db.get_conversations(agent_id=STEVE)

    filt = _agent_ors(fq.calls)[0]
    assert f"assigned_agent_id.eq.{STEVE}" in filt


@pytest.mark.asyncio
async def test_other_agent_engaged_does_not_leak_into_filter():
    """Leak-guard: OR only references the requesting agent id, never OTHER."""
    fq = _FakeQuery()
    fake_db = MagicMock()
    fake_db.table.return_value = fq

    with (
        patch.object(db, "get_client", return_value=fake_db),
        patch.object(db, "enrich_conversations_agent_info", new_callable=AsyncMock, side_effect=lambda rows: rows),
    ):
        await db.get_conversations(agent_id=STEVE)

    filt = _agent_ors(fq.calls)[0]
    assert OTHER not in filt
    assert STEVE in filt


@pytest.mark.asyncio
async def test_tunnel_and_status_still_anded_with_or():
    """Precedence: tunnel/status .eq() remain on the chain → AND with the OR."""
    fq = _FakeQuery()
    fake_db = MagicMock()
    fake_db.table.return_value = fq

    with (
        patch.object(db, "get_client", return_value=fake_db),
        patch.object(db, "enrich_conversations_agent_info", new_callable=AsyncMock, side_effect=lambda rows: rows),
    ):
        await db.get_conversations(
            tunnel="sales",
            status="closed",
            agent_id=STEVE,
        )

    assert _eq_calls(fq.calls, "tunnel") == ["sales"]
    assert _eq_calls(fq.calls, "status") == ["closed"]
    assert len(_agent_ors(fq.calls)) == 1


@pytest.mark.asyncio
async def test_assigned_to_all_no_agent_filter():
    """assigned_to=all / no agent_id → no assigned/engaged agent filter."""
    fq = _FakeQuery()
    fake_db = MagicMock()
    fake_db.table.return_value = fq

    with (
        patch.object(db, "get_client", return_value=fake_db),
        patch.object(db, "enrich_conversations_agent_info", new_callable=AsyncMock, side_effect=lambda rows: rows),
    ):
        await db.get_conversations(tunnel="sales", status="active")

    assert _agent_ors(fq.calls) == []
    assert not any(c[0] == "eq" and c[1] == "assigned_agent_id" for c in fq.calls)
    assert not any(c[0] == "is_" and c[1] == "assigned_agent_id" for c in fq.calls)


@pytest.mark.asyncio
async def test_assigned_to_none_still_null_assigned_only():
    """Unassigned queue: is_(assigned_agent_id, null) — engaged-fallback stays out."""
    fq = _FakeQuery()
    fake_db = MagicMock()
    fake_db.table.return_value = fq

    with (
        patch.object(db, "get_client", return_value=fake_db),
        patch.object(db, "enrich_conversations_agent_info", new_callable=AsyncMock, side_effect=lambda rows: rows),
    ):
        await db.get_conversations(agent_id_is_null=True, status="active")

    assert ("is_", "assigned_agent_id", "null") in fq.calls
    assert _agent_ors(fq.calls) == []


@pytest.mark.asyncio
async def test_list_endpoint_me_passes_user_id_as_agent_filter():
    """conversations.list_conversations(assigned_to=me) → get_conversations(agent_id=user)."""
    user = {"id": STEVE, "role": "sales", "tunnel_scope": "sales"}
    with (
        patch(
            "app.api.conversations.db.get_conversations",
            new_callable=AsyncMock,
            return_value=([], 0),
        ) as gc,
        patch("app.api.conversations.get_current_user", return_value=user),
    ):
        from app.api.conversations import list_conversations

        await list_conversations(
            tunnel=None,
            status="active",
            assigned_to="me",
            search=None,
            handled_by=None,
            limit=50,
            offset=0,
            user=user,
        )

    kwargs = gc.await_args.kwargs
    assert kwargs["agent_id"] == STEVE
    assert kwargs["agent_id_is_null"] is False
    # my+active expands to needs_agent as well (pre-existing; unchanged)
    assert kwargs["status_in"] == ["active", "needs_agent"]


@pytest.mark.asyncio
async def test_fall_back_preserves_engaged_agent_id():
    """§3.0 guard: live fall_back_to_ai writes metadata back with engaged intact."""
    from app.services.handoff import fall_back_to_ai

    conv = {
        "id": "c1",
        "assigned_agent_id": STEVE,
        "metadata": {
            "engaged_agent_id": STEVE,
            "announce_pending": False,
            "agent_assigned_at": "2026-07-17T10:00:00+00:00",
        },
    }
    updates: list[dict] = []

    async def _capture_update(_id, payload):
        updates.append(payload)

    async def _capture_release(_id, _agent, metadata):
        # The release is CONDITIONAL now — `.eq("assigned_agent_id", …)`, so
        # only one of the many sweeps can hand the conversation back (21
        # duplicate Timeout rows on 18 Aug). It writes the same three fields
        # the plain update used to; this stands in for that statement.
        updates.append(
            {"mode": "ai", "assigned_agent_id": None, "metadata": metadata}
        )
        return True

    with (
        patch("app.services.handoff.db.get_conversation_simple", new_callable=AsyncMock, return_value=conv),
        patch("app.services.handoff.db.update_conversation", new_callable=AsyncMock, side_effect=_capture_update),
        patch("app.services.handoff._release_from_agent", new_callable=AsyncMock, side_effect=_capture_release),
        patch("app.services.handoff.db.get_recent_messages", new_callable=AsyncMock, return_value=[]),
        patch("app.services.handoff._safe_system_msg", new_callable=AsyncMock, return_value=None),
        patch("app.services.handoff._handoff_phrase_recently_sent", new_callable=AsyncMock, return_value=False),
        patch("app.services.handoff.manager.push", new_callable=AsyncMock),
    ):
        await fall_back_to_ai("c1")

    assert updates, "fall_back_to_ai must update the conversation"
    meta = updates[0].get("metadata") or {}
    assert meta.get("engaged_agent_id") == STEVE
    assert updates[0].get("assigned_agent_id") is None
    assert updates[0].get("mode") == "ai"
