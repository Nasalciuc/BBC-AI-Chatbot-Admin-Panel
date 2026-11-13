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


# ── The one-line summary in the chat header (restored 13 Nov 2026) ──
#
# Since late October a take is logged as claim_won, and assigned/first_response
# are not: 188 of the 219 chats with operator events in the 30 days to 13 Nov
# had a take and no `assigned`, and their line said "AI handled".

from app.api.conversations import _operator_summary  # noqa: E402


def _ev(action, at, name="Ana", **kw):
    return {"action": action, "happened_at": at, "agent_name": name, "user_id": f"u-{name}", **kw}


def _msg(role, at, text="hi"):
    return {"role": role, "created_at": at, "content": text}


TOOK = "2026-11-10T14:00:00+00:00"


def test_a_claim_names_the_agent_and_the_reply_comes_from_the_messages():
    conv = {"status": "closed", "messages": [_msg("user", "2026-11-10T13:59:00+00:00"), _msg("agent", "2026-11-10T14:00:35+00:00")]}
    assert _operator_summary([_ev("claim_won", TOOK), _ev("closed", "2026-11-10T14:20:00+00:00")], conv) == "👤 Ana → ✅ 35s"


def test_no_reply_after_the_take_reads_timeout_no_reply_or_active():
    msgs = [_msg("user", "2026-11-10T13:59:00+00:00")]
    fired = [_ev("claim_won", TOOK), _ev("deadline_fired", "2026-11-10T14:01:30+00:00")]
    assert _operator_summary(fired, {"status": "active", "messages": msgs}) == "👤 Ana → ❌ timeout"
    assert _operator_summary([_ev("claim_won", TOOK)], {"status": "closed", "messages": msgs}) == "👤 Ana → ❌ no reply"
    assert _operator_summary([_ev("claim_won", TOOK)], {"status": "active", "messages": msgs}) == "👤 Ana → ⏳ active"


def test_the_last_take_counts_and_a_reply_before_it_does_not():
    events = [
        _ev("claim_won", "2026-11-10T13:00:00+00:00", name="Bogdan"),
        _ev("deadline_fired", "2026-11-10T13:01:30+00:00", name="Bogdan"),
        _ev("claim_won", TOOK),
    ]
    # Bogdan wrote before Ana's take; Ana has not written yet.
    msgs = [_msg("agent", "2026-11-10T13:00:20+00:00")]
    assert _operator_summary(events, {"status": "active", "messages": msgs}) == "👤 Ana → ⏳ active"
    msgs.append(_msg("agent", "2026-11-10T14:00:12+00:00"))
    assert _operator_summary(events, {"status": "active", "messages": msgs}) == "👤 Ana → ✅ 12s"


def test_a_timeout_before_the_last_take_is_not_this_takes_timeout():
    events = [_ev("deadline_fired", "2026-11-10T13:30:00+00:00"), _ev("claim_won", TOOK)]
    assert _operator_summary(events, {"status": "closed", "messages": []}) == "👤 Ana → ❌ no reply"


def test_an_agent_who_wrote_with_no_take_logged_is_still_named():
    conv = {"status": "closed", "assigned_agent_name": "Ana Test", "messages": [_msg("agent", TOOK)]}
    assert _operator_summary([], conv) == "👤 Ana Test"
    assert _operator_summary([_ev("closed", TOOK)], conv) == "👤 Ana Test"
    fallback = {"status": "active", "engaged_agent_name": "Bogdan Test", "messages": [_msg("agent", TOOK)]}
    assert _operator_summary([], fallback) == "👤 Bogdan Test"


def test_the_assistant_alone_still_reads_ai_handled():
    conv = {"status": "closed", "messages": [_msg("user", TOOK), _msg("ai", TOOK)]}
    assert _operator_summary([], conv) == "🤖 AI handled"
    assert _operator_summary([_ev("claim_lost", TOOK)], conv) == "🤖 AI handled"


def test_a_take_time_that_cannot_be_read_still_names_the_agent():
    conv = {"status": "active", "messages": [_msg("agent", TOOK)]}
    assert _operator_summary([_ev("claim_won", "not a time")], conv) == "👤 Ana → ✅"


@pytest.mark.asyncio
async def test_the_endpoint_sends_the_summary_of_a_claim():
    user = {"role": "qa", "id": "u1", "tunnel_scope": "sales"}
    conv = {"id": "c1", "tunnel": "sales", "status": "closed", "messages": [_msg("agent", "2026-11-10T14:00:41+00:00")]}
    with (
        patch("app.api.conversations.db.get_conversation", new_callable=AsyncMock, return_value=conv),
        patch("app.api.conversations.db.get_activity_for_conversation", new_callable=AsyncMock, return_value=[_ev("claim_won", TOOK)]),
    ):
        result = await get_operator_history("c1", user=user)
    assert result["source"] == "activity_log"
    assert result["summary"] == "👤 Ana → ✅ 41s"


@pytest.mark.asyncio
async def test_the_endpoint_names_a_writer_with_no_events_at_all():
    user = {"role": "admin", "id": "u1", "tunnel_scope": "sales"}
    conv = {"id": "c1", "tunnel": "sales", "status": "closed", "assigned_agent_name": "Ana Test", "messages": [_msg("agent", TOOK)]}
    with (
        patch("app.api.conversations.db.get_conversation", new_callable=AsyncMock, return_value=conv),
        patch("app.api.conversations.db.get_activity_for_conversation", new_callable=AsyncMock, return_value=[]),
        patch("app.api.conversations.db.get_system_messages_for_conversation", new_callable=AsyncMock, return_value=[]),
    ):
        result = await get_operator_history("c1", user=user)
    assert result["source"] == "none"
    assert result["summary"] == "👤 Ana Test"
