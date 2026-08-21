"""queue_oldest on the heartbeat — which conversation, how long, which route.

The CRM shows this one in its desktop notification. Three rules pinned here:
oldest is by queued_at and NOT by list order (the queue sorts needs_agent
first, so _rows[0] is the loudest, not the longest-waiting); the route comes
from the LEADS table, never from the client's own words; and an ineligible
operator gets None, at the source.
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

from app.api import agent as agent_api

_USER = {"id": "op-1"}


def _op_row(**kw):
    base = {"id": "op-1", "role": "sales", "is_active": True, "is_ready": False,
            "chat_enabled": True, "tunnel_scope": "sales", "team_id": None,
            "chats_served_today": 0, "chats_served_date": ""}
    base.update(kw)
    return base


def _qrow(cid, status, seconds_ago):
    return {
        "id": cid, "status": status, "tunnel": "sales",
        "queued_at": (datetime.now(timezone.utc)
                      - timedelta(seconds=seconds_ago)).isoformat(),
        "metadata": {},
    }


class _LeadTable:
    def __init__(self, row=None, raise_err=False):
        self.row = row
        self.raise_err = raise_err

    def table(self, _n):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self.raise_err:
            raise RuntimeError("leads read failed")
        return MagicMock(data=[self.row] if self.row else [])


def _patches(op_row, queue_rows, lead_table=None):
    return [
        patch.object(agent_api.db, "update_user_last_seen", new=AsyncMock()),
        patch("app.services.presence.record_presence_tick", new=AsyncMock()),
        patch.object(agent_api.db, "get_user_by_id", new=AsyncMock(return_value=op_row)),
        patch.object(agent_api, "_cleanup_stale_conversations",
                     new=AsyncMock(return_value=0)),
        patch.object(agent_api.db, "get_agent_active_count", new=AsyncMock(return_value=0)),
        patch.object(agent_api.db, "get_queue_for_operator",
                     new=AsyncMock(return_value=queue_rows)),
        patch.object(agent_api.db, "get_client",
                     return_value=lead_table or _LeadTable()),
        patch.object(agent_api.db, "_run_sync",
                     new=AsyncMock(side_effect=lambda fn, **k: fn())),
    ]


async def _beat(op_row, queue_rows, lead_table=None):
    ps = _patches(op_row, queue_rows, lead_table)
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in ps:
            stack.enter_context(p)
        return await agent_api.heartbeat(user=dict(_USER))


# ══════════════════════════════════════════════════════════════
# 11 — oldest by queued_at, not by list order
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_oldest_is_by_queued_at_not_list_position():
    """The queue sorts needs_agent first, so the FIRST row is the loudest.
    A recent shout must not eclipse the client who has waited longest."""
    rows = [
        _qrow("c-shouted", "needs_agent", seconds_ago=10),   # first in list
        _qrow("c-patient", "active", seconds_ago=300),       # longest wait
    ]
    out = await _beat(_op_row(), rows)
    assert out["queue_oldest"] is not None
    assert out["queue_oldest"]["id"] == "c-patient", "queued_at decides, not position"
    assert out["queue_oldest"]["waiting_seconds"] >= 295


# ══════════════════════════════════════════════════════════════
# 12 — the route: from leads, absent when not extracted, never a 500
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_route_comes_from_the_lead():
    lead = {"origin_code": "HNL", "destination_code": "AKL"}
    out = await _beat(_op_row(), [_qrow("c1", "active", 60)], _LeadTable(lead))
    assert out["queue_oldest"]["route"] == "HNL → AKL"


@pytest.mark.asyncio
async def test_route_is_none_when_not_extracted_yet():
    out = await _beat(_op_row(), [_qrow("c1", "active", 60)],
                      _LeadTable({"origin_code": None, "destination_code": "AKL"}))
    assert out["queue_oldest"]["route"] is None, "half a route is no route"
    assert out["queue_oldest"]["id"] == "c1"


@pytest.mark.asyncio
async def test_a_failed_lead_read_still_reports_the_conversation():
    out = await _beat(_op_row(), [_qrow("c1", "active", 60)],
                      _LeadTable(raise_err=True))
    assert out["queue_oldest"] is not None
    assert out["queue_oldest"]["route"] is None
    assert out["success"] is True, "a leads hiccup must never break the heartbeat"


# ══════════════════════════════════════════════════════════════
# 13 — ineligible operator: None at the source
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_chat_disabled_operator_gets_no_queue_oldest():
    out = await _beat(_op_row(chat_enabled=False), [_qrow("c1", "active", 60)])
    assert out["queue_count"] == 0
    assert out["queue_ids"] == []
    assert out["queue_oldest"] is None


@pytest.mark.asyncio
async def test_empty_queue_means_none_not_a_crash():
    out = await _beat(_op_row(), [])
    assert out["queue_oldest"] is None
    assert out["queue_count"] == 0
