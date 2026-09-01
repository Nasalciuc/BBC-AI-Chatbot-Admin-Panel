"""Tests for 30s first-response timeout and stale ready reset."""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from config.settings import settings


_CONV_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
# Past the FIX-B first-response deadline (90s) but under the 480s engaged timeout.
_ASSIGNED_AT = (datetime.now(timezone.utc) - timedelta(seconds=95)).isoformat()


def _conv_no_agent_msg():
    return {
        "id": _CONV_ID,
        "assigned_agent_id": "agent-1",
        "metadata": {"agent_assigned_at": _ASSIGNED_AT},
    }


@pytest.mark.asyncio
async def test_first_response_timeout_30s():
    from app.api.agent import _enforce_response_deadline

    with (
        patch(
            "app.api.agent.db.get_active_human_conversations",
            new_callable=AsyncMock,
            return_value=[_conv_no_agent_msg()],
        ),
        patch(
            "app.services.handoff.fall_back_to_ai",
            new_callable=AsyncMock,
        ) as fallback_mock,
    ):
        count = await _enforce_response_deadline()

    assert count == 1
    fallback_mock.assert_awaited_once_with(_CONV_ID)


def _conv_engaged(assigned_ago: float):
    """Agent replied AFTER assignment. Engagement is now read from the row
    (last_agent_message_at, migration 024) — the per-row query the sweep used
    to issue for every open conversation is what took the panel down on 31 Aug.
    """
    assigned = datetime.now(timezone.utc) - timedelta(seconds=assigned_ago)
    return {
        "id": _CONV_ID,
        "assigned_agent_id": "agent-1",
        "metadata": {"agent_assigned_at": assigned.isoformat()},
        "last_agent_message_at": (assigned + timedelta(seconds=5)).isoformat(),
    }


@pytest.mark.asyncio
async def test_engaged_agent_not_timed_out_at_30s():
    from app.api.agent import _enforce_response_deadline

    with (
        patch(
            "app.api.agent.db.get_active_human_conversations",
            new_callable=AsyncMock,
            return_value=[_conv_engaged(95)],
        ),
        patch(
            "app.services.handoff.fall_back_to_ai",
            new_callable=AsyncMock,
        ) as fallback_mock,
    ):
        count = await _enforce_response_deadline()

    assert count == 0
    fallback_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_engaged_agent_skipped_by_enforce_even_at_500s():
    from app.api.agent import _enforce_response_deadline

    with (
        patch(
            "app.api.agent.db.get_active_human_conversations",
            new_callable=AsyncMock,
            return_value=[_conv_engaged(500)],
        ),
        patch(
            "app.services.handoff.fall_back_to_ai",
            new_callable=AsyncMock,
        ) as fallback_mock,
    ):
        count = await _enforce_response_deadline()

    assert count == 0
    fallback_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_ready_reset():
    from app.db import supabase as db

    stale_user = {"id": "u1", "name": "Nasalciuc"}

    with patch.object(db, "_run_sync", new_callable=AsyncMock) as run_mock:
        select_result = type("R", (), {"data": [stale_user]})()
        update_result = type("R", (), {"data": [{"id": "u1"}]})()
        run_mock.side_effect = [select_result, update_result, update_result]

        result = await db.reset_stale_ready_users(timeout_seconds=600)

    assert len(result) == 1
    assert result[0]["name"] == "Nasalciuc"


@pytest.mark.asyncio
async def test_fresh_user_not_reset():
    from app.db import supabase as db

    with patch.object(db, "_run_sync", new_callable=AsyncMock) as run_mock:
        select_result = type("R", (), {"data": []})()
        run_mock.return_value = select_result

        result = await db.reset_stale_ready_users(timeout_seconds=600)

    assert result == []


def test_first_response_setting_is_90():
    # FIX-B: 30s was impossible for humans (p75≈480s per 30d audit).
    # With FIX-A, AI serves the visitor during this window anyway.
    assert settings.agent_first_response_timeout_seconds == 90
