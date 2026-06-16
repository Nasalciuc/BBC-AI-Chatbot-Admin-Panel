"""Tests for GET /conversations/{id}/operator-history."""

import pytest
from unittest.mock import AsyncMock, patch

from app.api.conversations import _parse_operator_events, get_operator_history


def test_parse_operator_events_assigned_and_timeout():
    msgs = [
        {
            "content": "You're now being assisted by Steve Holt.",
            "created_at": "2026-06-12T01:10:00+00:00",
        },
        {
            "content": "Sorry for the wait — I'm here and we can pick up right where we left off.",
            "created_at": "2026-06-12T01:18:00+00:00",
        },
    ]
    events = _parse_operator_events(msgs)
    actions = [e["action"] for e in events]
    assert "assigned" in actions
    assert "deadline_fired" in actions
    assigned = next(e for e in events if e["action"] == "assigned")
    assert assigned["agent_name"] == "Steve Holt"


@pytest.mark.asyncio
async def test_operator_history_activity_log_source():
    user = {"role": "admin", "id": "u1", "tunnel_scope": "sales"}
    events = [
        {
            "action": "assigned",
            "agent_name": "Steve",
            "user_id": "a1",
            "happened_at": "2026-06-12T19:41:00+00:00",
        },
        {
            "action": "first_response",
            "agent_name": "Steve",
            "response_seconds": 10,
            "happened_at": "2026-06-12T19:41:10+00:00",
        },
    ]
    with (
        patch(
            "app.api.conversations.db.get_conversation",
            new_callable=AsyncMock,
            return_value={"id": "c1", "tunnel": "sales"},
        ),
        patch(
            "app.api.conversations.db.get_activity_for_conversation",
            new_callable=AsyncMock,
            return_value=events,
        ),
    ):
        result = await get_operator_history("c1", user=user)
    assert result["source"] == "activity_log"
    assert "✅" in result["summary"]
    assert "10s" in result["summary"]


@pytest.mark.asyncio
async def test_operator_history_none_when_empty():
    user = {"role": "admin", "id": "u1", "tunnel_scope": "sales"}
    with (
        patch(
            "app.api.conversations.db.get_conversation",
            new_callable=AsyncMock,
            return_value={"id": "c1", "tunnel": "sales"},
        ),
        patch(
            "app.api.conversations.db.get_activity_for_conversation",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.api.conversations.db.get_system_messages_for_conversation",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await get_operator_history("c1", user=user)
    assert result["source"] == "none"
    assert result["summary"] == "🤖 AI handled"
