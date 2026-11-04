"""CodeRabbit review of #227 — the operator card's team scope, the learning
endpoints' envelope, and the SSE stream releasing its queue.get() tasks."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import agent as agent_api  # noqa: E402
from app.api import conversations as conv_api  # noqa: E402
from app.api import lessons as lessons_api  # noqa: E402

_CONV = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_SUPERVISOR = {"id": "u2", "role": "supervisor"}
_SALES = {"id": "u1", "role": "sales", "tunnel_scope": "sales"}
_OWNER = {"id": "u0", "role": "owner"}


# ── /conversations/{id}/details: same team scope as the messages ──────────

async def _details(user, conv, team_ids=None):
    with patch.object(conv_api.db, "get_conversation_simple", new=AsyncMock(return_value=conv)), \
         patch.object(conv_api, "_resolve_team_scope", new=AsyncMock(return_value=team_ids or [])), \
         patch("app.services.lead_service.get_or_create_lead",
               new=AsyncMock(return_value={"origin_code": "JFK", "destination_code": "LHR"})):
        try:
            return await conv_api.get_conversation_details(_CONV, user=user), None
        except HTTPException as e:
            return None, e


@pytest.mark.asyncio
async def test_details_refused_to_a_supervisor_outside_the_team():
    res, err = await _details(_SUPERVISOR, {"id": _CONV, "tunnel": "sales", "team_id": "team-b"}, ["team-a"])
    assert res is None and err.status_code == 403


@pytest.mark.asyncio
async def test_details_fail_closed_for_a_supervisor_with_no_team():
    res, err = await _details(_SUPERVISOR, {"id": _CONV, "tunnel": "sales", "team_id": "team-a"}, [])
    assert res is None and err.status_code == 403


@pytest.mark.asyncio
async def test_details_served_inside_the_team():
    res, err = await _details(_SUPERVISOR, {"id": _CONV, "tunnel": "sales", "team_id": "team-a"}, ["team-a"])
    assert err is None and res["success"] is True and res["data"]["route"] == "JFK → LHR"


@pytest.mark.asyncio
async def test_details_for_an_operator_skip_the_team_lookup():
    with patch.object(conv_api, "_resolve_team_scope",
                      new=AsyncMock(side_effect=AssertionError("team lookup for an operator"))):
        with patch.object(conv_api.db, "get_conversation_simple",
                          new=AsyncMock(return_value={"id": _CONV, "tunnel": "sales"})), \
             patch("app.services.lead_service.get_or_create_lead", new=AsyncMock(return_value={})):
            res = await conv_api.get_conversation_details(_CONV, user=_SALES)
    assert res["success"] is True


# ── learning endpoints: { success, data, count, error? } ──────────────────

@pytest.mark.asyncio
async def test_variants_endpoint_uses_the_envelope():
    with patch("app.services.learning.get_question_variants", new=AsyncMock(return_value=[{"id": "v1"}])):
        res = await lessons_api.list_question_variants(slot=None, user=_OWNER)
    assert res == {"success": True, "data": [{"id": "v1"}], "count": 1}


@pytest.mark.asyncio
async def test_variants_endpoint_read_failure_is_502_not_empty():
    with patch("app.services.learning.get_question_variants", new=AsyncMock(side_effect=RuntimeError("down"))):
        with pytest.raises(HTTPException) as ei:
            await lessons_api.list_question_variants(slot=None, user=_OWNER)
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_report_endpoint_uses_the_envelope():
    rep = {"treatment_answer_rate": 0.5, "control_answer_rate": 0.4, "treatment_n": 10,
           "control_n": 2, "by_slot": {}}
    with patch("app.services.learning.build_learning_report", new=AsyncMock(return_value=rep)):
        res = await lessons_api.learning_report(user=_OWNER)
    assert res == {"success": True, "data": rep, "count": 1}


@pytest.mark.asyncio
async def test_report_endpoint_failure_says_so():
    rep = {"treatment_answer_rate": None, "control_answer_rate": None, "treatment_n": 0,
           "control_n": 0, "by_slot": {}, "error": "fetch_failed"}
    with patch("app.services.learning.build_learning_report", new=AsyncMock(return_value=rep)):
        res = await lessons_api.learning_report(user=_OWNER)
    assert res["success"] is False and res["error"] == "fetch_failed" and res["count"] == 0


# ── the agent stream: a disconnect leaves no pending queue.get() ──────────

@pytest.mark.asyncio
async def test_cancelled_stream_leaves_no_pending_queue_gets():
    from app.realtime.manager import manager

    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=False)
    with patch.object(agent_api.db, "get_conversation_simple",
                      new=AsyncMock(return_value={"id": _CONV, "tunnel": "sales"})):
        res = await agent_api.agent_stream(_CONV, request, user=_SALES)

    async def _drain():
        async for _chunk in res.body_iterator:
            pass

    before = set(asyncio.all_tasks())
    task = asyncio.create_task(_drain())
    await asyncio.sleep(0.05)  # the generator is now parked in asyncio.wait
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    leftover = [t for t in asyncio.all_tasks() - before if not t.done()]
    assert leftover == [], "queue.get() tasks survived the disconnect"
    assert manager.subscriber_count(_CONV) == 0
    assert manager.subscriber_count(f"op:{_CONV}") == 0
