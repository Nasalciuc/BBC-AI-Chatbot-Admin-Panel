"""Operator presence — a sent reply proves the operator is here.

Bug: an operator who was actively writing had their conversation handed back to
the AI because only the 5s heartbeat wrote last_seen_at. These tests pin the two
halves of the fix: send_agent_message refreshes last_seen_at, and a recent agent
message keeps the operator "online" even when the heartbeat lapsed.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.security.auth import get_current_user

AGENT_ID = "11111111-1111-1111-1111-111111111111"
CONV_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _ago(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _client(role: str = "sales") -> TestClient:
    from app.api.conversations import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": AGENT_ID,
        "email": f"{role}@bbc.com",
        "name": "Op",
        "role": role,
        "tunnel_scope": "sales",
    }
    return TestClient(app)


def _conv(**over) -> dict:
    base = {
        "id": CONV_ID,
        "tunnel": "sales",
        "mode": "human",
        "status": "active",
        "assigned_agent_id": AGENT_ID,
        "metadata": {"engaged_agent_id": AGENT_ID},
    }
    base.update(over)
    return base


def _send(last_seen_side_effect=None):
    """POST an agent message with everything but presence mocked out."""
    from app.api import conversations as mod

    with (
        patch.object(mod.db, "get_user_by_id", new_callable=AsyncMock,
                     return_value={"id": AGENT_ID, "role": "sales", "name": "Op"}),
        patch.object(mod.db, "get_conversation", new_callable=AsyncMock,
                     return_value=_conv()),
        patch.object(mod, "add_message", new_callable=AsyncMock,
                     return_value={"id": "m-1", "role": "agent", "content": "hi"}),
        patch.object(mod.db, "count_agent_messages_since", new_callable=AsyncMock,
                     return_value=2),
        patch.object(mod.db, "update_conversation", new_callable=AsyncMock,
                     return_value=_conv()),
        patch.object(mod.manager, "push", new_callable=AsyncMock),
        patch.object(mod.db, "update_user_last_seen", new_callable=AsyncMock,
                     side_effect=last_seen_side_effect) as last_seen,
    ):
        resp = _client().post(
            f"/api/conversations/{CONV_ID}/messages", json={"content": "hi there"}
        )
    return resp, last_seen


# ── 1-2. A sent message writes presence, and never blocks the send ──


def test_agent_message_updates_last_seen():
    resp, last_seen = _send()

    assert resp.status_code == 200, resp.text
    last_seen.assert_awaited_once_with(AGENT_ID)


def test_last_seen_failure_does_not_block_send():
    resp, last_seen = _send(last_seen_side_effect=RuntimeError("supabase down"))

    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
    last_seen.assert_awaited_once_with(AGENT_ID)


# ── 3-6. is_agent_effectively_offline ────────────────────────────────


async def _offline(*, last_seen: str | None, last_msg_seconds: int | None,
                   assigned_seconds: int = 30) -> bool:
    from app.services import handoff

    last_msg = (
        None if last_msg_seconds is None
        else datetime.now(timezone.utc) - timedelta(seconds=last_msg_seconds)
    )
    with (
        patch.object(handoff.db, "get_conversation_simple", new_callable=AsyncMock,
                     return_value=_conv(metadata={"agent_assigned_at": _ago(assigned_seconds)})),
        patch.object(handoff.db, "get_user_by_id", new_callable=AsyncMock,
                     return_value={"id": AGENT_ID, "last_seen_at": last_seen}),
        patch.object(handoff.db, "get_last_agent_message_time", new_callable=AsyncMock,
                     return_value=last_msg),
    ):
        return await handoff.is_agent_effectively_offline(CONV_ID)


@pytest.mark.asyncio
async def test_recent_message_and_fresh_heartbeat_is_online():
    assert await _offline(last_seen=_ago(10), last_msg_seconds=60) is False


@pytest.mark.asyncio
async def test_recent_message_rescues_lapsed_heartbeat():
    """The core bug: heartbeat missed a cycle while the operator was replying."""
    assert await _offline(last_seen=_ago(900), last_msg_seconds=60) is False


@pytest.mark.asyncio
async def test_old_message_and_stale_heartbeat_is_offline():
    assert await _offline(last_seen=_ago(3600), last_msg_seconds=3600) is True


@pytest.mark.asyncio
async def test_never_spoke_within_silent_window_is_online():
    assert await _offline(
        last_seen=_ago(10), last_msg_seconds=None, assigned_seconds=120
    ) is False


@pytest.mark.asyncio
async def test_never_spoke_past_silent_window_is_offline():
    from config.settings import settings

    assert await _offline(
        last_seen=_ago(10),
        last_msg_seconds=None,
        assigned_seconds=settings.agent_silent_timeout_seconds + 60,
    ) is True


@pytest.mark.asyncio
async def test_silent_observer_at_ten_minutes_is_not_offline():
    """Reading a long thread before replying must not trigger AI fallback."""
    from config.settings import settings

    assert settings.agent_silent_timeout_seconds >= 900
    assert await _offline(
        last_seen=_ago(10), last_msg_seconds=None, assigned_seconds=600
    ) is False
