"""The casino and the 1:1 rule are gone — an ownerless conversation queues.

The owners asked for competition, so the system stops choosing: everyone
eligible sees the conversation, the first to press Take gets it, and a fast
operator holding several at once is intended, not an accident.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoieCJ9.sig")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from datetime import datetime, timezone

from app.db import supabase as sb
from app.services import routing
from app.services.routing import route_conversation


def _agent(i):
    return {"id": f"op-{i}", "name": f"Op {i}", "role": "sales",
            "chats_served_today": i, "chats_served_date": "2026-08-19",
            "last_seen_at": datetime.now(timezone.utc).isoformat()}


class _Visitor:
    name = "New Client"
    email = None
    phone = None


# ══════════════════════════════════════════════════════════════
# 1-2 · nobody is auto-picked any more
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_three_online_operators_and_none_receives_automatically():
    active_count = AsyncMock()
    with patch.object(routing.db, "get_available_agents",
                      new=AsyncMock(return_value=[_agent(1), _agent(2), _agent(3)])), \
         patch.object(routing.db, "get_agent_active_count", active_count), \
         patch.object(routing.db, "increment_chats_served", new=AsyncMock()) as inc:
        out = await route_conversation("sales", visitor=_Visitor, visitor_id=None)

    assert out == {"agent_id": None, "mode": "ai", "agent_name": None, "reason": "queued"}
    active_count.assert_not_awaited(), "one DB call per agent per conversation, removed"
    inc.assert_not_awaited(), "the score belongs to winners, not to the router"


@pytest.mark.asyncio
async def test_nobody_online_same_shape_no_error():
    with patch.object(routing.db, "get_available_agents", new=AsyncMock(return_value=[])):
        out = await route_conversation("sales", visitor=_Visitor, visitor_id=None)
    assert out.get("agent_id") is None
    assert out.get("mode") == "ai"


# ══════════════════════════════════════════════════════════════
# 3 · enqueue is idempotent — the first wait is the real wait
# ══════════════════════════════════════════════════════════════

class _Conv:
    def __init__(self, row):
        self.row = row

    def table(self, _n):
        return self

    def update(self, payload):
        self._payload = payload
        return self

    def eq(self, *a):
        return self

    def is_(self, col, _v):
        self._null_checks = getattr(self, "_null_checks", []) + [col]
        return self

    def execute(self):
        for col in getattr(self, "_null_checks", []):
            if self.row.get(col) is not None:
                self._null_checks = []
                return MagicMock(data=[])
        self._null_checks = []
        self.row.update(self._payload)
        return MagicMock(data=[dict(self.row)])


@pytest.mark.asyncio
async def test_double_enqueue_keeps_the_first_timestamp():
    table = _Conv({"id": "c1", "assigned_agent_id": None, "queued_at": None})
    with patch.object(sb, "get_client", return_value=table), \
         patch.object(sb, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())), \
         patch.object(sb, "_queued_at_column_available", return_value=True):
        assert await sb.enqueue_conversation("c1") is True
        first = table.row["queued_at"]
        assert await sb.enqueue_conversation("c1") is False, "already stamped"
        assert table.row["queued_at"] == first


@pytest.mark.asyncio
async def test_missing_034_is_a_silent_no_op():
    with patch.object(sb, "_queued_at_column_available", return_value=False):
        assert await sb.enqueue_conversation("c1") is False


# ══════════════════════════════════════════════════════════════
# 4 · sticky untouched
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_returning_client_still_goes_to_their_operator():
    # _find_sticky_operator returns the ROUTE shape, not the user row.
    sticky_hit = {"agent_id": "op-9", "agent_name": "Op 9",
                  "mode": "human", "reason": "affinity"}
    sticky_row = _agent(9) | {"is_active": True, "chat_enabled": True}
    with patch.object(routing, "_find_sticky_operator",
                      new=AsyncMock(return_value=sticky_hit)), \
         patch.object(routing.db, "get_user_by_id", new=AsyncMock(return_value=sticky_row)), \
         patch.object(routing.db, "increment_chats_served", new=AsyncMock()) as inc:
        out = await route_conversation("sales", visitor=_Visitor, visitor_id="v-1")
    assert out.get("agent_id") == "op-9"
    assert out.get("reason") == "affinity"
    inc.assert_awaited_once(), "sticky scores exactly once"


# ══════════════════════════════════════════════════════════════
# 5-7 · wiring
# ══════════════════════════════════════════════════════════════

def test_get_agent_active_count_gone_from_routing():
    import inspect

    src = inspect.getsource(route_conversation)
    assert "await db.get_agent_active_count" not in src, "no CALL — a comment may name it"
    assert "settings.max_concurrent_chats" not in src
    assert '"reason": "queued"' in src


def test_casino_symbols_are_gone():
    import inspect

    src = inspect.getsource(routing)
    assert "randbelow" not in src
    assert "Fisher-Yates" not in src
    assert "free_agents" not in src


def test_first_message_path_enqueues_exactly_once():
    import pathlib

    src = (pathlib.Path(__file__).resolve().parent.parent / "app/api/chat.py").read_text(encoding="utf-8")
    assert src.count("enqueue_conversation(") == 1
    assert "await db.enqueue_conversation(req.conversation_id)" in src
