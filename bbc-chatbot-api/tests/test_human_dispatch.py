"""Human dispatch — chats actually reach ready operators.

The three leaks this pins shut:
1. needs_agent fired only on the SECOND explicit request — "Agent, please"
   got a phone number and the demand signal died.
2. needs_agent was SET in two places and CONSUMED in zero — a shelf label.
3. fall_back_to_ai silently erased missed assignments (Oslo's
   chats_served_today=1 with zero messages).
"""

import inspect
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.services.handoff import HANDOFF_HEALTH, fall_back_to_ai, get_handoff_response
from app.services.routing import dispatch_needs_agent

_CID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_AGENT = "11111111-2222-3333-4444-555555555555"

_QUEUED_CONV = {
    "id": _CID, "status": "needs_agent", "assigned_agent_id": None,
    "tunnel": "sales", "visitor_name": "Costa", "visitor_email": "c@x.com",
    "visitor_phone": "+15551234567", "visitor_id": "vis-1", "chat_number": 1042,
    "metadata": {},
}


def _update_result(rows):
    chain = MagicMock()
    chain.update.return_value = chain
    chain.eq.return_value = chain
    chain.execute.return_value = MagicMock(data=rows)
    client = MagicMock()
    client.table.return_value = chain
    return client


async def _fake_run_sync(fn):
    return fn()


# ── Leak 1: the first request counts, and the reply collects ─────────────

class TestFirstRequestCounts:
    def test_threshold_is_first_request(self):
        from app.pipeline import orchestrator

        src = inspect.getsource(orchestrator._pipeline)
        assert "agent_request_count >= 0" in src
        assert "agent_request_count >= 1" not in src
        # Setting the flag now also fires the dispatcher.
        assert "dispatch_needs_agent" in src

    @pytest.mark.asyncio
    async def test_queue_reply_collects_instead_of_phone_deflection(self):
        visitor = MagicMock(name="v")
        visitor.name = "Costa"; visitor.email = None; visitor.phone = None
        with patch("app.services.routing.route_conversation",
                   new=AsyncMock(return_value={"agent_id": None, "mode": "ai", "agent_name": None})), \
             patch("app.services.handoff._handoff_phrase_recently_sent",
                   new=AsyncMock(return_value=False)), \
             patch("app.services.handoff.db.update_conversation", new=AsyncMock()), \
             patch("app.services.handoff.db.get_conversation_simple",
                   new=AsyncMock(return_value={"id": _CID, "visitor_name": "Costa"})), \
             patch("app.services.lead_service.get_or_create_lead",
                   new=AsyncMock(return_value={"origin_code": "JFK", "destination_code": "LHR"})), \
             patch("app.pipeline.orchestrator._fire_and_forget", lambda coro: coro.close()):
            text, model = await get_handoff_response(_CID, "sales", visitor)

        assert "flagging a teammate" in text
        assert "888" not in text            # no bare phone deflection
        assert model == "template"
        assert "?" in text                  # keeps collecting — asks something


# ── Leak 2: the dispatcher drains the queue ──────────────────────────────

class TestDispatcher:
    @pytest.mark.asyncio
    async def test_ready_agent_gets_assigned_via_unified_handoff(self):
        client = _update_result([{"id": _CID}])  # claim wins
        perform = AsyncMock()
        with patch("app.services.routing.db.get_conversation_simple",
                   new=AsyncMock(return_value=dict(_QUEUED_CONV))), \
             patch("app.services.routing.route_conversation",
                   new=AsyncMock(return_value={"agent_id": _AGENT, "mode": "human", "agent_name": "Oslo"})), \
             patch("app.services.routing.db.get_client", return_value=client), \
             patch("app.services.routing.db._run_sync", side_effect=_fake_run_sync), \
             patch("app.services.handoff.perform_handoff_to_agent", perform):
            assigned = await dispatch_needs_agent(_CID)

        assert assigned is True
        perform.assert_awaited_once()
        kwargs = perform.await_args.kwargs
        assert perform.await_args.args[0] == _CID
        assert kwargs["agent_id"] == _AGENT
        assert kwargs["handoff_reason"] == "auto_assign"

    @pytest.mark.asyncio
    async def test_double_dispatch_race_single_assignment(self):
        client = _update_result([])  # claim LOST — someone else flipped status
        perform = AsyncMock()
        with patch("app.services.routing.db.get_conversation_simple",
                   new=AsyncMock(return_value=dict(_QUEUED_CONV))), \
             patch("app.services.routing.route_conversation",
                   new=AsyncMock(return_value={"agent_id": _AGENT, "mode": "human", "agent_name": "Oslo"})), \
             patch("app.services.routing.db.get_client", return_value=client), \
             patch("app.services.routing.db._run_sync", side_effect=_fake_run_sync), \
             patch("app.services.handoff.perform_handoff_to_agent", perform):
            assigned = await dispatch_needs_agent(_CID)

        assert assigned is False
        perform.assert_not_awaited()  # the loser never touches the conversation

    @pytest.mark.asyncio
    async def test_zero_ready_fires_super_alert_and_stays_queued(self):
        update_conv = AsyncMock()
        send_alert = AsyncMock()
        with patch("app.services.routing.db.get_conversation_simple",
                   new=AsyncMock(return_value=dict(_QUEUED_CONV))), \
             patch("app.services.routing.route_conversation",
                   new=AsyncMock(return_value={"agent_id": None, "mode": "ai", "agent_name": None})), \
             patch("app.services.routing.db.update_conversation", update_conv), \
             patch("app.services.closing.claim_super_alert", new=AsyncMock(return_value=True)), \
             patch("app.services.email.send_super_alert_email", send_alert):
            assigned = await dispatch_needs_agent(_CID)

        assert assigned is False
        send_alert.assert_awaited_once()
        assert send_alert.await_args.kwargs["chat_number"] == 1042
        update_conv.assert_not_awaited()  # status stays needs_agent

    @pytest.mark.asyncio
    async def test_already_assigned_or_not_queued_is_noop(self):
        for conv in (
            {**_QUEUED_CONV, "status": "active"},
            {**_QUEUED_CONV, "assigned_agent_id": _AGENT},
            None,
        ):
            with patch("app.services.routing.db.get_conversation_simple",
                       new=AsyncMock(return_value=conv)):
                assert await dispatch_needs_agent(_CID) is False

    def test_open_endpoint_self_heals_stale_queue(self):
        from app.api.chat import mark_chat_session_open

        src = inspect.getsource(mark_chat_session_open)
        assert 'conv.get("status") == "needs_agent"' in src
        assert "dispatch_needs_agent" in src

    def test_both_set_sites_fire_the_dispatcher(self):
        from app.pipeline import orchestrator
        from app.services import handoff

        assert "dispatch_needs_agent" in inspect.getsource(orchestrator._pipeline)
        assert "dispatch_needs_agent" in inspect.getsource(handoff.get_handoff_response)


# ── Leak 3: fall_back_to_ai leaves a trace ───────────────────────────────

class TestFallbackTrace:
    @pytest.mark.asyncio
    async def test_missed_handoff_screams_counts_and_marks(self, caplog):
        import logging

        before = HANDOFF_HEALTH["expired_since_boot"]
        conv = {"id": _CID, "assigned_agent_id": _AGENT,
                "metadata": {"announce_pending": True}}
        upd = AsyncMock()
        # The write is conditional now (one sweep of many may release), so the
        # payload this test protects travels through _release_from_agent.
        rel = AsyncMock(return_value=True)
        with patch("app.services.handoff.db.get_conversation_simple",
                   new=AsyncMock(return_value=conv)), \
             patch("app.services.handoff.db.update_conversation", upd), \
             patch("app.services.handoff._release_from_agent", rel), \
             patch("app.services.handoff.add_message", new=AsyncMock()), \
             caplog.at_level(logging.ERROR):
            await fall_back_to_ai(_CID)

        assert HANDOFF_HEALTH["expired_since_boot"] == before + 1
        assert HANDOFF_HEALTH["last_at"] is not None
        assert any("HANDOFF EXPIRED" in r.message and _AGENT in r.message
                   for r in caplog.records)
        meta = rel.await_args.args[2]
        assert meta["missed_by_human_at"]  # the panel/audits can count losses

    @pytest.mark.asyncio
    async def test_health_surfaces_handoffs_expired(self):
        from app.api.health import health

        with patch("app.api.health.get_scheduler_health", return_value={}), \
             patch("app.api.health.supervisor_columns_status", return_value=True), \
             patch("app.api.health.settings") as s:
            s.debug = False
            payload = await health()
        assert "handoffs_expired" in payload
        assert "expired_since_boot" in payload["handoffs_expired"]
