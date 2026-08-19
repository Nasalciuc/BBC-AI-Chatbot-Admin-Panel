"""The shared queue — everyone sees it, the first click wins.

The filter deliberately does NOT test status='needs_agent' alone: a brand-new
conversation is 'active', and a needs_agent-only queue would be permanently
empty for exactly the visitors it exists for — a bug that looks like a working
system.
"""

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


def _row(cid, status="active", queued_seconds_ago=30, team_id=None, engaged=None):
    return {
        "id": cid, "chat_number": 1, "tunnel": "sales", "status": status,
        "created_at": "2026-08-19T00:00:00+00:00",
        "queued_at": (datetime.now(timezone.utc)
                      - timedelta(seconds=queued_seconds_ago)).isoformat(),
        "team_id": team_id, "last_user_message_at": None, "last_reply_at": None,
        "message_count": 3,
        "metadata": {"engaged_agent_id": engaged} if engaged else {},
    }


class _QueueTable:
    def __init__(self, rows):
        self.rows = rows
        self.queries = 0

    def table(self, _n):
        return self

    def select(self, *_a, **_k):
        return self

    def not_(self, *_a):
        return self

    @property
    def not_(self):  # noqa: F811 — supabase-py exposes .not_ as a property
        return _NotChain(self)

    def is_(self, *_a):
        return self

    def in_(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def or_(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        self.queries += 1
        return MagicMock(data=list(self.rows))


class _NotChain:
    def __init__(self, t):
        self.t = t

    def is_(self, *_a):
        return self.t


def _fresh_cache():
    sb._queue_cache.clear()
    sb._queued_at_ok = None
    sb._queued_at_downgraded_at = None


def _wire(table):
    return [
        patch.object(sb, "get_client", return_value=table),
        patch.object(sb, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())),
    ]


# ══════════════════════════════════════════════════════════════
# V5 — the anti-mystery: active conversations ARE in the queue
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_new_active_conversation_appears():
    _fresh_cache()
    table = _QueueTable([_row("c1", status="active")])
    ps = _wire(table)
    with ps[0], ps[1]:
        rows = await sb.get_queue_for_operator("sales", viewer_agent_id="me")
    assert [r["id"] for r in rows] == ["c1"]


@pytest.mark.asyncio
async def test_needs_agent_sorts_first():
    _fresh_cache()
    table = _QueueTable([
        _row("c-active", status="active", queued_seconds_ago=300),
        _row("c-shouted", status="needs_agent", queued_seconds_ago=10),
    ])
    ps = _wire(table)
    with ps[0], ps[1]:
        rows = await sb.get_queue_for_operator("sales", viewer_agent_id="me")
    assert rows[0]["id"] == "c-shouted", "the visitor who asked out loud goes first"


# ══════════════════════════════════════════════════════════════
# The 60s silent reservation, applied per viewer AFTER the cache
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_reservation_hides_from_others_shows_to_the_engaged_operator():
    _fresh_cache()
    table = _QueueTable([_row("c1", engaged="op-X", queued_seconds_ago=10)])
    ps = _wire(table)
    with ps[0], ps[1]:
        for_y = await sb.get_queue_for_operator("sales", viewer_agent_id="op-Y")
        for_x = await sb.get_queue_for_operator("sales", viewer_agent_id="op-X")
    assert for_y == [], "still reserved for the operator who knows the client"
    assert [r["id"] for r in for_x] == ["c1"]


@pytest.mark.asyncio
async def test_reservation_expires_at_61s():
    _fresh_cache()
    table = _QueueTable([_row("c1", engaged="op-X", queued_seconds_ago=61)])
    ps = _wire(table)
    with ps[0], ps[1]:
        for_y = await sb.get_queue_for_operator("sales", viewer_agent_id="op-Y")
    assert [r["id"] for r in for_y] == ["c1"], "after 60s the line is everyone's"


# ══════════════════════════════════════════════════════════════
# Cache: one raw query per (tunnel, team) per 5s
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_two_calls_in_five_seconds_one_query():
    _fresh_cache()
    table = _QueueTable([_row("c1", engaged="op-X", queued_seconds_ago=10)])
    ps = _wire(table)
    with ps[0], ps[1]:
        a = await sb.get_queue_for_operator("sales", viewer_agent_id="op-X")
        b = await sb.get_queue_for_operator("sales", viewer_agent_id="op-Y")
    assert table.queries == 1, "raw rows cached; ~120 q/min on the hottest table otherwise"
    # …but the per-viewer reservation is still applied to both, correctly.
    assert [r["id"] for r in a] == ["c1"]
    assert b == []


@pytest.mark.asyncio
async def test_missing_034_returns_empty_without_error():
    _fresh_cache()
    with patch.object(sb, "_queued_at_column_available", return_value=False):
        rows = await sb.get_queue_for_operator("sales", viewer_agent_id="me")
    assert rows == []


# ══════════════════════════════════════════════════════════════
# The heartbeat carries it — and refuses at the source
# ══════════════════════════════════════════════════════════════

def test_heartbeat_gates_the_queue_at_the_source():
    import inspect

    from app.api import agent as agent_api

    src = inspect.getsource(agent_api)
    assert 'user_db.get("chat_enabled", True)' in src
    assert '"queue_count": _queue_count' in src
    assert '"queue_ids": _queue_ids' in src
    # The queue must never break the heartbeat.
    assert "queue fetch failed" in src


def test_the_release_endpoint_is_gone():
    """Release left with its button (owner's decision): a client who just
    reached a human must not be thrown back into the line with the wait reset,
    and in a competitive system giving back what you took defeats the point."""
    import inspect

    from app.api import conversations as capi

    assert not hasattr(capi, "release_conversation_endpoint")
    assert "/release" not in inspect.getsource(capi)


def test_three_windows_three_names():
    from config.settings import settings

    assert settings.agent_timeout_seconds == 600
    assert settings.crm_presence_window_seconds == 90
    assert settings.queue_presence_window_seconds == 90


# ══════════════════════════════════════════════════════════════
# The full queued_at lifecycle
# ══════════════════════════════════════════════════════════════

def test_lifecycle_is_documented_in_034():
    import pathlib

    sql = (pathlib.Path(__file__).resolve().parent.parent
           / "migrations/034_queued_at.sql").read_text(encoding="utf-8")
    assert "CONDITIONALLY" in sql and "UNCONDITIONALLY" in sql
    assert "claim gate" in sql
    assert "terminal" in sql


def test_sticky_client_never_reaches_the_queue():
    """Sticky returns an agent_id, so the chat.py else-branch (the single
    enqueue point) is never taken for a returning client."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parent.parent
           / "app/api/chat.py").read_text(encoding="utf-8")
    i = src.index("await db.enqueue_conversation(req.conversation_id)")
    j = src.rindex('if _route and _route.get("agent_id"):', 0, i)
    branch = src[j:i]
    assert "else:" in branch, "enqueue lives only in the no-operator branch"
