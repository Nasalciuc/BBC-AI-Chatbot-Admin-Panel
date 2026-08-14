"""Supervisor visibility suite — chat numbers, derived tags, Inactive gate, emails."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.supabase import derive_conversation_tag, attach_derived_tag


NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)


def _conv(**kwargs):
    base = {
        "status": "active",
        "mode": "ai",
        "assigned_agent_id": None,
        "metadata": {},
        "last_user_message_at": None,
        "last_agent_message_at": None,
        "last_reply_at": None,
        "visitor_name": None,
        "visitor_phone": None,
        "visitor_email": None,
    }
    base.update(kwargs)
    return base


# A lead dict that satisfies get_missing_fields(for_crm=True): route,
# passengers, departure date. Contact comes from conv (visitor_* fields).
COMPLETE_LEAD = {
    "id": "lead-complete",
    "conversation_id": "C1",
    "origin_code": "JFK",
    "destination_code": "LAX",
    "departure_date": "2026-08-01",
    "passengers": 2,
}
FULL_CONTACT = {
    "visitor_name": "Ada Lovelace",
    "visitor_phone": "+15551234567",
    "visitor_email": "ada@example.com",
}


class TestDeriveConversationTag:
    def test_fresh_no_operator(self):
        tag = derive_conversation_tag(
            _conv(
                last_user_message_at=(NOW - timedelta(minutes=2)).isoformat(),
                last_reply_at=(NOW - timedelta(minutes=1)).isoformat(),
            ),
            quiet_minutes=30,
            now=NOW,
        )
        assert tag == "fresh"

    def test_active_live_with_sticky(self):
        tag = derive_conversation_tag(
            _conv(
                mode="human",
                assigned_agent_id="agent-1",
                metadata={"engaged_agent_id": "agent-1"},
                last_user_message_at=(NOW - timedelta(minutes=2)).isoformat(),
                last_agent_message_at=(NOW - timedelta(minutes=1)).isoformat(),
                last_reply_at=(NOW - timedelta(minutes=1)).isoformat(),
            ),
            quiet_minutes=30,
            now=NOW,
        )
        assert tag == "active"

    def test_main_queue_customer_returned_to_sticky(self):
        tag = derive_conversation_tag(
            _conv(
                mode="ai",
                assigned_agent_id=None,
                metadata={"engaged_agent_id": "agent-1"},
                last_agent_message_at=(NOW - timedelta(hours=2)).isoformat(),
                last_user_message_at=(NOW - timedelta(minutes=5)).isoformat(),
                last_reply_at=(NOW - timedelta(hours=2)).isoformat(),
            ),
            quiet_minutes=30,
            now=NOW,
        )
        assert tag == "main_queue"

    def test_main_queue_needs_agent(self):
        tag = derive_conversation_tag(
            _conv(
                status="needs_agent",
                mode="ai",
                metadata={"engaged_agent_id": "agent-1"},
                last_agent_message_at=(NOW - timedelta(minutes=10)).isoformat(),
                last_user_message_at=(NOW - timedelta(minutes=3)).isoformat(),
                last_reply_at=(NOW - timedelta(minutes=10)).isoformat(),
            ),
            quiet_minutes=30,
            now=NOW,
        )
        assert tag == "main_queue"

    def test_abandoned_customer_went_quiet(self):
        """Renamed from #158's `inactive` — same condition, new name."""
        tag = derive_conversation_tag(
            _conv(
                mode="human",
                assigned_agent_id="agent-1",
                metadata={"engaged_agent_id": "agent-1"},
                last_user_message_at=(NOW - timedelta(minutes=45)).isoformat(),
                last_agent_message_at=(NOW - timedelta(minutes=40)).isoformat(),
                last_reply_at=(NOW - timedelta(minutes=40)).isoformat(),
            ),
            quiet_minutes=30,
            now=NOW,
        )
        assert tag == "abandoned"

    def test_abandoned_not_when_customer_still_speaking(self):
        tag = derive_conversation_tag(
            _conv(
                mode="human",
                assigned_agent_id="agent-1",
                metadata={"engaged_agent_id": "agent-1"},
                last_user_message_at=(NOW - timedelta(minutes=5)).isoformat(),
                last_agent_message_at=(NOW - timedelta(minutes=40)).isoformat(),
                last_reply_at=(NOW - timedelta(minutes=40)).isoformat(),
            ),
            quiet_minutes=30,
            now=NOW,
        )
        # Customer spoke last recently while live with operator → Active, not Abandoned
        assert tag == "active"

    def test_fresh_ai_quiet_does_not_become_abandoned(self):
        """AI-only quiet chats stay Fresh — Abandoned requires operator involvement."""
        tag = derive_conversation_tag(
            _conv(
                last_user_message_at=(NOW - timedelta(hours=2)).isoformat(),
                last_reply_at=(NOW - timedelta(hours=2) + timedelta(minutes=1)).isoformat(),
            ),
            quiet_minutes=30,
            now=NOW,
        )
        assert tag == "fresh"

    def test_sticky_agent_id_alias(self):
        tag = derive_conversation_tag(
            _conv(
                mode="human",
                assigned_agent_id="agent-1",
                metadata={"sticky_agent_id": "agent-1"},
                last_agent_message_at=(NOW - timedelta(minutes=1)).isoformat(),
                last_user_message_at=(NOW - timedelta(minutes=2)).isoformat(),
                last_reply_at=(NOW - timedelta(minutes=1)).isoformat(),
            ),
            quiet_minutes=30,
            now=NOW,
        )
        assert tag == "active"


class TestAiOutcomeTags:
    """#159 — the three cases #158 conflated/missed: completed, abandoned
    (renamed from inactive), no_engagement (new, GLOBAL)."""

    def test_no_engagement_contact_left_zero_messages(self):
        tag = derive_conversation_tag(
            _conv(**FULL_CONTACT, last_user_message_at=None),
            now=NOW,
        )
        assert tag == "no_engagement"

    def test_no_engagement_requires_full_contact(self):
        """Partial contact (e.g. only a name) is not enough — stays Fresh."""
        tag = derive_conversation_tag(
            _conv(visitor_name="Ada Lovelace", last_user_message_at=None),
            now=NOW,
        )
        assert tag == "fresh"

    def test_no_engagement_not_when_customer_wrote(self):
        tag = derive_conversation_tag(
            _conv(**FULL_CONTACT, last_user_message_at=(NOW - timedelta(minutes=1)).isoformat()),
            now=NOW,
        )
        assert tag != "no_engagement"

    def test_completed_full_data_captured(self):
        tag = derive_conversation_tag(
            _conv(**FULL_CONTACT, last_user_message_at=(NOW - timedelta(minutes=10)).isoformat()),
            lead=COMPLETE_LEAD,
            now=NOW,
        )
        assert tag == "completed"

    def test_completed_outranks_abandoned(self):
        """A fully-captured lead is `completed` even if the operator chat
        also meets the quiet-window abandoned condition."""
        tag = derive_conversation_tag(
            _conv(
                **FULL_CONTACT,
                mode="human",
                assigned_agent_id="agent-1",
                metadata={"engaged_agent_id": "agent-1"},
                last_user_message_at=(NOW - timedelta(minutes=45)).isoformat(),
                last_agent_message_at=(NOW - timedelta(minutes=40)).isoformat(),
                last_reply_at=(NOW - timedelta(minutes=40)).isoformat(),
            ),
            lead=COMPLETE_LEAD,
            quiet_minutes=30,
            now=NOW,
        )
        assert tag == "completed"

    def test_completed_requires_customer_message(self):
        """A lead can't be `completed` if the customer never wrote — that's
        no_engagement's territory even if a lead row happens to exist."""
        tag = derive_conversation_tag(
            _conv(**FULL_CONTACT, last_user_message_at=None),
            lead=COMPLETE_LEAD,
            now=NOW,
        )
        assert tag == "no_engagement"

    def test_abandoned_partial_data_not_completed(self):
        incomplete_lead = {"id": "L2", "origin_code": "JFK"}  # no destination/dates/pax
        tag = derive_conversation_tag(
            _conv(
                mode="human",
                assigned_agent_id="agent-1",
                metadata={"engaged_agent_id": "agent-1"},
                last_user_message_at=(NOW - timedelta(minutes=45)).isoformat(),
                last_agent_message_at=(NOW - timedelta(minutes=40)).isoformat(),
                last_reply_at=(NOW - timedelta(minutes=40)).isoformat(),
            ),
            lead=incomplete_lead,
            quiet_minutes=30,
            now=NOW,
        )
        assert tag == "abandoned"

    def test_operator_tags_unaffected_by_lead_param(self):
        """fresh/active/main_queue derive exactly as in #158 when there's no
        complete lead and no no_engagement condition."""
        tag = derive_conversation_tag(_conv(mode="ai"), now=NOW)
        assert tag == "fresh"


class TestAttachDerivedTag:
    def test_request_id_from_lead(self):
        row = attach_derived_tag(
            _conv(lead={"id": "lead-abc"}, metadata={}, mode="ai")
        )
        assert row["request_id"] == "lead-abc"
        assert row["tag"] == "fresh"


class TestCrmOutcomeTagBlock:
    @pytest.mark.asyncio
    async def test_abandoned_pushes_with_defaults(self):
        """BUSINESS RULE (feat/crm-aaa-restore, owner explicit): every
        captured contact is dialable — the old 409 became the defaults
        push through submit_abandoned_to_crm."""
        from app.api.leads import mark_lead_created_in_crm
        from app.services.crm import CRMResult

        now = datetime.now(timezone.utc)
        quiet_user = (now - timedelta(minutes=45)).isoformat()
        quiet_reply = (now - timedelta(minutes=40)).isoformat()
        lead = {"id": "L1", "conversation_id": "C1"}
        conv = _conv(
            mode="human",
            assigned_agent_id="a1",
            metadata={"engaged_agent_id": "a1"},
            last_user_message_at=quiet_user,
            last_agent_message_at=quiet_reply,
            last_reply_at=quiet_reply,
        )
        assert derive_conversation_tag(conv, lead=lead) == "abandoned"

        with (
            patch("app.api.leads.db.get_lead_full", new=AsyncMock(return_value=lead)),
            patch("app.api.leads.db.get_conversation_simple", new=AsyncMock(return_value=conv)),
            patch("app.api.leads.db.mark_lead_created_in_crm", new=AsyncMock()) as mark,
            patch(
                "app.services.crm.submit_abandoned_to_crm",
                new=AsyncMock(return_value=CRMResult(success=True, request_id="R-1")),
            ) as push,
        ):
            out = await mark_lead_created_in_crm("L1", user={"role": "sales", "id": "u1"})
            assert out["success"] is True
            assert out.get("pushed_with_defaults") is True
            push.assert_awaited_once()
            mark.assert_awaited_once_with("L1", crm_lead_id="R-1")

    @pytest.mark.asyncio
    async def test_no_engagement_pushes_with_defaults_and_flag(self):
        from app.api.leads import mark_lead_created_in_crm
        from app.services.crm import CRMResult

        lead = {"id": "L1", "conversation_id": "C1"}
        conv = _conv(**FULL_CONTACT, last_user_message_at=None)
        assert derive_conversation_tag(conv, lead=lead) == "no_engagement"

        with (
            patch("app.api.leads.db.get_lead_full", new=AsyncMock(return_value=lead)),
            patch("app.api.leads.db.get_conversation_simple", new=AsyncMock(return_value=conv)),
            patch("app.api.leads.db.mark_lead_created_in_crm", new=AsyncMock()) as mark,
            patch(
                "app.services.crm.submit_abandoned_to_crm",
                new=AsyncMock(return_value=CRMResult(success=True, request_id="R-2")),
            ) as push,
        ):
            out = await mark_lead_created_in_crm("L1", user={"role": "sales", "id": "u1"})
            assert out["success"] is True
            # The silent-lead marker rides into the payload conv so the
            # consultant sees "No engagement — form only" on the CRM row.
            assert push.await_args.args[0].get("_no_engagement") is True
            mark.assert_awaited_once_with("L1", crm_lead_id="R-2")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["fresh", "active", "main_queue", "completed"])
    async def test_non_blocked_tags_allowed(self, kind):
        from app.api.leads import mark_lead_created_in_crm

        now = datetime.now(timezone.utc)
        setups = {
            "fresh": dict(mode="ai", metadata={}),
            "active": dict(
                mode="human",
                assigned_agent_id="a1",
                metadata={"engaged_agent_id": "a1"},
                last_user_message_at=(now - timedelta(minutes=2)).isoformat(),
                last_agent_message_at=(now - timedelta(minutes=1)).isoformat(),
                last_reply_at=(now - timedelta(minutes=1)).isoformat(),
            ),
            "main_queue": dict(
                mode="ai",
                metadata={"engaged_agent_id": "a1"},
                last_agent_message_at=(now - timedelta(hours=1)).isoformat(),
                last_user_message_at=(now - timedelta(minutes=5)).isoformat(),
                last_reply_at=(now - timedelta(hours=1)).isoformat(),
            ),
            "completed": dict(
                **FULL_CONTACT,
                last_user_message_at=(now - timedelta(minutes=10)).isoformat(),
            ),
        }
        lead = COMPLETE_LEAD if kind == "completed" else {"id": "L1", "conversation_id": "C1"}
        conv = _conv(**setups[kind])
        assert derive_conversation_tag(conv, lead=lead) == kind
        marked = {"id": "L1", "created_in_crm": True}

        # The endpoint now performs the REAL push (crm-push-truth): non-blocked
        # tags reach push_lead_to_crm; the flag is written inside that path.
        from app.services.crm import CRMResult

        with (
            patch("app.api.leads.db.get_lead_full", new=AsyncMock(side_effect=[lead, marked])),
            patch("app.api.leads.db.get_conversation_simple", new=AsyncMock(return_value=conv)),
            patch(
                "app.services.crm.push_lead_to_crm",
                new=AsyncMock(return_value=CRMResult(success=True, request_id="R-1")),
            ) as push,
        ):
            result = await mark_lead_created_in_crm(
                "L1", user={"role": "sales", "id": "u1"}
            )
            assert result["success"] is True
            push.assert_called_once()


class TestAttentionEmail:
    @pytest.mark.asyncio
    async def test_email_has_four_fields(self):
        from app.services.email import send_attention_email

        captured = {}

        class FakeResp:
            status_code = 200
            text = "ok"

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None):
                captured["json"] = json
                return FakeResp()

        with (
            patch("app.services.email.settings") as s,
            patch("app.services.email.httpx.AsyncClient", return_value=FakeClient()),
        ):
            s.postmark_token = "tok"
            s.attention_email_to = "super@example.com"
            s.super_alert_email = "super@example.com"
            s.email_from = "noreply@example.com"
            s.admin_panel_url = "https://chat.example.com"

            ok = await send_attention_email(
                tag="main_queue",
                chat_number=1042,
                created_at="2026-07-28T10:00:00Z",
                customer_name="Ada Lovelace",
                conversation_id="C1",
            )
        assert ok is True
        body = captured["json"]["HtmlBody"]
        assert "Main Queue" in body
        assert "#1042" in body
        assert "2026-07-28T10:00:00Z" in body
        assert "Ada Lovelace" in body

    @pytest.mark.asyncio
    async def test_run_skips_plain_fresh_ai(self):
        from app.api.cron import run_attention_emails

        fresh = _conv(
            id="C-fresh",
            chat_number=1,
            created_at=NOW.isoformat(),
            visitor_name="AI Only",
            status="active",
            mode="ai",
            metadata={},
        )
        with (
            patch("app.api.cron.settings") as s,
            patch(
                "app.api.cron.db.get_attention_email_candidates",
                new=AsyncMock(return_value=[fresh]),
            ),
            patch(
                "app.api.cron.db.claim_attention_email", new=AsyncMock()
            ) as claim,
            patch(
                "app.services.email.send_attention_email", new=AsyncMock()
            ) as send,
        ):
            s.attention_email_enabled = True
            result = await run_attention_emails()
        assert result["sent"] == 0
        claim.assert_not_called()
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_sends_main_queue_once(self):
        from app.api.cron import run_attention_emails

        mq = _conv(
            id="C-mq",
            chat_number=99,
            created_at=NOW.isoformat(),
            visitor_name="Returnee",
            status="active",
            mode="ai",
            metadata={"engaged_agent_id": "a1"},
            last_agent_message_at=(NOW - timedelta(hours=1)).isoformat(),
            last_user_message_at=(NOW - timedelta(minutes=5)).isoformat(),
            last_reply_at=(NOW - timedelta(hours=1)).isoformat(),
        )
        # Use wall-clock-relative stamps so derive_conversation_tag agrees.
        now = datetime.now(timezone.utc)
        mq["last_agent_message_at"] = (now - timedelta(hours=1)).isoformat()
        mq["last_user_message_at"] = (now - timedelta(minutes=5)).isoformat()
        mq["last_reply_at"] = (now - timedelta(hours=1)).isoformat()
        assert derive_conversation_tag(mq) == "main_queue"

        with (
            patch("app.api.cron.settings") as s,
            patch(
                "app.api.cron.db.get_attention_email_candidates",
                new=AsyncMock(return_value=[mq]),
            ),
            patch(
                "app.api.cron.db.claim_attention_email",
                new=AsyncMock(return_value=True),
            ) as claim,
            patch(
                "app.services.email.send_attention_email",
                new=AsyncMock(return_value=True),
            ) as send,
        ):
            s.attention_email_enabled = True
            result = await run_attention_emails()
        assert result["sent"] == 1
        claim.assert_called_once_with("C-mq")
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        assert kwargs["tag"] == "main_queue"
        assert kwargs["chat_number"] == 99
        assert kwargs["customer_name"] == "Returnee"


class TestAiOutcomeTagRoleGate:
    def test_list_route_has_tag_param(self):
        from app.api.conversations import list_conversations
        import inspect

        sig = inspect.signature(list_conversations)
        assert "tag" in sig.parameters

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tag", ["abandoned", "completed", "no_engagement"])
    async def test_outcome_tags_forbidden_for_sales(self, tag):
        from fastapi import HTTPException
        from app.api.conversations import list_conversations

        with pytest.raises(HTTPException) as ei:
            await list_conversations(
                tunnel=None, status=None, assigned_to="all", search=None,
                handled_by=None, tag=tag, limit=50, offset=0,
                user={"role": "sales", "id": "u1", "tunnel_scope": "sales"},
            )
        assert ei.value.status_code == 403

    @pytest.mark.asyncio
    async def test_abandoned_allowed_for_supervisor_team_scoped(self):
        from app.api.conversations import list_conversations

        with (
            patch(
                "app.api.conversations.db.get_team_ids_for_supervisor",
                new=AsyncMock(return_value=["team-1"]),
            ),
            patch(
                "app.api.conversations.db.get_conversations",
                new=AsyncMock(return_value=([], 0)),
            ) as list_q,
        ):
            res = await list_conversations(
                tunnel=None, status=None, assigned_to="all", search=None,
                handled_by=None, tag="abandoned", limit=50, offset=0,
                user={"role": "supervisor", "id": "s1"},
            )
        assert res["success"] is True
        kwargs = list_q.call_args.kwargs
        assert kwargs["tag"] == "abandoned"
        assert kwargs["team_ids"] == ["team-1"]
        assert kwargs["quiet_before"] is not None
        assert kwargs["no_engagement_only"] is False

    @pytest.mark.asyncio
    async def test_no_engagement_is_global_even_for_team_scoped_supervisor(self):
        """A supervisor scoped to team-1 still sees the GLOBAL no_engagement
        pool — team_ids must be overridden to None, not ["team-1"]."""
        from app.api.conversations import list_conversations

        with (
            patch(
                "app.api.conversations.db.get_team_ids_for_supervisor",
                new=AsyncMock(return_value=["team-1"]),
            ),
            patch(
                "app.api.conversations.db.get_conversations",
                new=AsyncMock(return_value=([], 0)),
            ) as list_q,
        ):
            res = await list_conversations(
                tunnel=None, status=None, assigned_to="all", search=None,
                handled_by=None, tag="no_engagement", limit=50, offset=0,
                user={"role": "supervisor", "id": "s1"},
            )
        assert res["success"] is True
        kwargs = list_q.call_args.kwargs
        assert kwargs["tag"] == "no_engagement"
        assert kwargs["team_ids"] is None
        # No N+1 / no full-table Python filter: the predicate must be pushed
        # to the DB query.
        assert kwargs["no_engagement_only"] is True

    @pytest.mark.asyncio
    async def test_completed_stays_team_scoped(self):
        from app.api.conversations import list_conversations

        with (
            patch(
                "app.api.conversations.db.get_team_ids_for_supervisor",
                new=AsyncMock(return_value=["team-1"]),
            ),
            patch(
                "app.api.conversations.db.get_conversations",
                new=AsyncMock(return_value=([], 0)),
            ) as list_q,
        ):
            await list_conversations(
                tunnel=None, status=None, assigned_to="all", search=None,
                handled_by=None, tag="completed", limit=50, offset=0,
                user={"role": "supervisor", "id": "s1"},
            )
        kwargs = list_q.call_args.kwargs
        assert kwargs["tag"] == "completed"
        assert kwargs["team_ids"] == ["team-1"]
        assert kwargs["no_engagement_only"] is False


class TestPreMigrationDegradation:
    """The app can deploy before migrations 023/024 run — the list must survive."""

    def test_legacy_column_set_excludes_new_columns(self):
        from app.db.supabase import _LIST_COLUMNS_BASE, _LIST_COLUMNS

        for col in ("chat_number", "last_user_message_at", "last_reply_at"):
            assert col not in _LIST_COLUMNS_BASE
            assert col in _LIST_COLUMNS

    def test_downgrade_disables_supervisor_columns(self):
        import app.db.supabase as sb

        original = sb._supervisor_columns_ok
        original_at = sb._supervisor_columns_downgraded_at
        try:
            sb._supervisor_columns_ok = None
            sb._supervisor_columns_downgraded_at = None
            assert sb._supervisor_columns_available() is True
            sb._downgrade_supervisor_columns(Exception("column chat_number does not exist"))
            assert sb._supervisor_columns_available() is False
        finally:
            sb._supervisor_columns_ok = original
            sb._supervisor_columns_downgraded_at = original_at
