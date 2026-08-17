"""AAA-to-CRM restored — every captured contact is dialable.

BUSINESS RULE (owner, explicit): silent leads (form filled, zero client
messages) GO to CRM — the sales team dials every contact, 30-day window.
Three layers had strangled the original AAA design: the button's 409, the
blanket score<40 gate, and the cron select skipping zero-message convs.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.services import crm
from app.services.crm import (
    CRM_PUSH_HEALTH,
    CRMResult,
    crm_hygiene_gate,
    crm_push_gate,
    submit_abandoned_to_crm,
)

APP = os.path.join(os.path.dirname(__file__), "..", "app")

LEAD = {"id": "lead-1", "conversation_id": "conv-1", "score": 0}
SILENT_CONV = {
    "id": "conv-1",
    "visitor_name": "",
    "visitor_email": "silent@gmail.com",
    "visitor_phone": "+12105550100",
    "metadata": {"utm_source": "google", "page_url": "https://buybusinessclass.com/flight/lax-syd"},
    "visitor_id": "v-1",
    "_no_engagement": True,
}


@pytest.fixture(autouse=True)
def _reset():
    for k in ("ok", "failed", "refused"):
        CRM_PUSH_HEALTH[k] = 0
    yield


def _crm_client_ok(crm_id: str = "R-AAA"):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": {"id": crm_id}}
    resp.text = ""
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    return client


# ════════════════════════════════════════════════════════════
# The gate is SCOPED: score blocks only the strict path
# ════════════════════════════════════════════════════════════

class TestScopedGate:
    def test_strict_path_still_refuses_low_score(self):
        assert crm_push_gate({"score": 0}, "a@gmail.com") == "low_score"
        assert crm_push_gate({"score": 39}, "a@gmail.com") == "low_score"

    def test_hygiene_path_ignores_score(self):
        assert crm_hygiene_gate({"score": 0}, "a@gmail.com", require_email=False) is None

    @pytest.mark.parametrize("email,expected", [
        ("test@example.com", "test_email"),
        ("not-an-email", "email_invalid"),
        ("jean@yahool.com", "email_typo_domain"),
    ])
    def test_hygiene_holds_on_both_paths(self, email, expected):
        assert crm_hygiene_gate({}, email, require_email=False) == expected
        assert crm_push_gate({"score": 90}, email) == expected

    def test_same_route_refused_on_both_paths(self):
        lead = {"score": 90, "origin_code": "LHR", "destination_code": "LHR"}
        assert crm_hygiene_gate(lead, "a@gmail.com", require_email=False) == "same_route"
        assert crm_push_gate(lead, "a@gmail.com") == "same_route"

    def test_missing_email_ok_when_phone_bearing(self):
        # Abandoned path: email optional; strict path: still required.
        assert crm_hygiene_gate({}, "", require_email=False) is None
        assert crm_push_gate({"score": 90}, "") == "email_invalid"


# ════════════════════════════════════════════════════════════
# submit_abandoned_to_crm: hygiene + the "form only" note
# ════════════════════════════════════════════════════════════

class TestAbandonedPath:
    @pytest.mark.asyncio
    async def test_test_email_refused_and_counted(self):
        conv = dict(SILENT_CONV, visitor_email="test@x.com")
        with patch.object(crm.settings, "crm_api_url", "https://crm.test"):
            res = await submit_abandoned_to_crm(conv, dict(LEAD))
        assert not res.success and res.error == "gate:test_email"
        assert CRM_PUSH_HEALTH["refused"] == 1

    @pytest.mark.asyncio
    async def test_silent_lead_pushes_with_note(self):
        client = _crm_client_ok()
        with patch.object(crm, "_get_crm_client", return_value=client), \
             patch.object(crm.settings, "crm_api_url", "https://crm.test"):
            res = await submit_abandoned_to_crm(dict(SILENT_CONV), dict(LEAD))

        assert res.success and res.request_id == "R-AAA"
        payload = client.post.call_args.kwargs["json"]
        note = payload.get("chat_context") or ""
        assert "No engagement — form only" in note
        assert "source: google" in note
        assert "landing: https://buybusinessclass.com/flight/lax-syd" in note
        # AAA defaults make the rows recognizable.
        assert payload["flights"][0]["from"] == "AAA"

    @pytest.mark.asyncio
    async def test_engaged_conv_has_no_form_only_note(self):
        conv = {k: v for k, v in SILENT_CONV.items() if k != "_no_engagement"}
        client = _crm_client_ok()
        with patch.object(crm, "_get_crm_client", return_value=client), \
             patch.object(crm.settings, "crm_api_url", "https://crm.test"):
            await submit_abandoned_to_crm(conv, dict(LEAD))
        note = client.post.call_args.kwargs["json"].get("chat_context") or ""
        # The intent: an ENGAGED conversation is never labeled no-engagement.
        # (Wave 6 adds a separate "form only / data incomplete" line for
        # DEFAULTED fields — this lead has neither route nor date, so that
        # honest line is expected and correct.)
        assert "No engagement" not in note


# ════════════════════════════════════════════════════════════
# The button pushes instead of refusing (no 409)
# ════════════════════════════════════════════════════════════

class TestButtonUnblocked:
    @pytest.mark.asyncio
    async def test_no_engagement_lead_pushes_with_defaults(self):
        from app.api import leads as leads_api
        from app.db import supabase as sb

        with patch.object(sb, "get_lead_full", new_callable=AsyncMock) as get_lead, \
             patch.object(sb, "get_conversation_simple", new_callable=AsyncMock) as get_conv, \
             patch.object(sb, "derive_conversation_tag", return_value="no_engagement"), \
             patch.object(sb, "mark_lead_created_in_crm", new_callable=AsyncMock) as mark, \
             patch.object(crm, "submit_abandoned_to_crm", new_callable=AsyncMock) as push:
            get_lead.side_effect = [
                dict(LEAD, created_in_crm=False),
                dict(LEAD, created_in_crm=True, crm_lead_id="R-AAA"),
            ]
            get_conv.return_value = dict(SILENT_CONV)
            push.return_value = CRMResult(success=True, request_id="R-AAA")

            out = await leads_api.mark_lead_created_in_crm("lead-1", user={"role": "admin"})

        assert out["success"] and out.get("pushed_with_defaults") is True
        push.assert_awaited_once()
        mark.assert_awaited_once_with("lead-1", crm_lead_id="R-AAA")
        # The no_engagement flag rides into the payload conv.
        assert push.await_args.args[0].get("_no_engagement") is True

    @pytest.mark.asyncio
    async def test_failure_is_a_422_not_a_silent_flag(self):
        from fastapi import HTTPException

        from app.api import leads as leads_api
        from app.db import supabase as sb

        with patch.object(sb, "get_lead_full", new_callable=AsyncMock) as get_lead, \
             patch.object(sb, "get_conversation_simple", new_callable=AsyncMock) as get_conv, \
             patch.object(sb, "derive_conversation_tag", return_value="abandoned"), \
             patch.object(crm, "submit_abandoned_to_crm", new_callable=AsyncMock) as push:
            get_lead.return_value = dict(LEAD, created_in_crm=False)
            get_conv.return_value = dict(SILENT_CONV)
            push.return_value = CRMResult(success=False, error="gate:test_email")

            with pytest.raises(HTTPException) as exc:
                await leads_api.mark_lead_created_in_crm("lead-1", user={"role": "admin"})
        assert exc.value.status_code == 422

    def test_the_409_is_gone(self):
        with open(os.path.join(APP, "api", "leads.py"), encoding="utf-8") as f:
            src = f.read()
        assert "It can't be submitted to the CRM" not in src


# ════════════════════════════════════════════════════════════
# The cron sees silent leads — 30-day window
# ════════════════════════════════════════════════════════════

class TestCronSelect:
    @pytest.mark.asyncio
    async def test_zero_message_conv_included_and_flagged(self):
        from app.db import supabase as sb

        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        conv = dict(SILENT_CONV, created_at=old)
        conv.pop("_no_engagement", None)
        calls = {"n": 0}

        async def fake_run_sync(fn, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return SimpleNamespace(data=[dict(conv)])
            return SimpleNamespace(data=[])  # zero messages

        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", fake_run_sync):
            out = await sb.get_abandoned_conversations(30)

        assert len(out) == 1
        assert out[0]["_no_engagement"] is True

    @pytest.mark.asyncio
    async def test_fresh_silent_conv_not_yet_abandoned(self):
        from app.db import supabase as sb

        fresh = datetime.now(timezone.utc).isoformat()
        conv = dict(SILENT_CONV, created_at=fresh)
        conv.pop("_no_engagement", None)
        calls = {"n": 0}

        async def fake_run_sync(fn, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return SimpleNamespace(data=[dict(conv)])
            return SimpleNamespace(data=[])

        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", fake_run_sync):
            out = await sb.get_abandoned_conversations(30)
        assert out == []

    def test_window_and_contact_or_in_the_query(self):
        with open(os.path.join(APP, "db", "supabase.py"), encoding="utf-8") as f:
            src = f.read()
        assert 'timedelta(days=30)' in src
        assert (
            'and(visitor_phone.not.is.null,visitor_phone.neq.),"\n'
            '                "and(visitor_email.not.is.null,visitor_email.neq.)'
        ) in src

    def test_cron_no_longer_requires_name_or_email(self):
        with open(os.path.join(APP, "api", "cron.py"), encoding="utf-8") as f:
            src = f.read()
        assert 'reason": "name_invalid' not in src
        assert 'or "Customer"' in src


# ════════════════════════════════════════════════════════════
# One-time backfill — idempotent by the flag
# ════════════════════════════════════════════════════════════

class TestBackfill:
    @pytest.mark.asyncio
    async def test_pushes_blocked_ones_including_closed(self):
        from app.api import cron
        from app.db import supabase as sb

        closed_conv = dict(
            SILENT_CONV, id="conv-closed", status="closed",
            last_user_message_at=None,
        )
        closed_conv.pop("_no_engagement", None)
        with patch.object(sb, "get_recent_contact_conversations", new_callable=AsyncMock) as sel, \
             patch.object(cron, "get_or_create_lead", new_callable=AsyncMock) as get_lead, \
             patch.object(sb, "mark_lead_created_in_crm", new_callable=AsyncMock) as mark, \
             patch.object(cron, "submit_abandoned_to_crm", new_callable=AsyncMock) as push:
            sel.return_value = [closed_conv]
            get_lead.return_value = dict(LEAD, created_in_crm=False)
            push.return_value = CRMResult(success=True, request_id="R-BF")

            out = await cron.run_aaa_backfill()

        assert out["pushed"] == 1
        mark.assert_awaited_once_with("lead-1", crm_lead_id="R-BF")
        # Zero user messages → the "form only" flag rides along.
        assert push.await_args.args[0].get("_no_engagement") is True

    @pytest.mark.asyncio
    async def test_second_run_pushes_nothing(self):
        from app.api import cron
        from app.db import supabase as sb

        with patch.object(sb, "get_recent_contact_conversations", new_callable=AsyncMock) as sel, \
             patch.object(cron, "get_or_create_lead", new_callable=AsyncMock) as get_lead, \
             patch.object(cron, "submit_abandoned_to_crm", new_callable=AsyncMock) as push:
            sel.return_value = [dict(SILENT_CONV)]
            get_lead.return_value = dict(LEAD, created_in_crm=True)  # already pushed

            out = await cron.run_aaa_backfill()

        assert out["pushed"] == 0
        push.assert_not_awaited()

    def test_backfill_select_has_no_status_filter(self):
        with open(os.path.join(APP, "db", "supabase.py"), encoding="utf-8") as f:
            src = f.read()
        start = src.index("def get_recent_contact_conversations")
        body = src[start:start + 1600]
        assert '.eq("status"' not in body, "closed conversations must be included"

    def test_summary_line_logged(self):
        with open(os.path.join(APP, "api", "cron.py"), encoding="utf-8") as f:
            src = f.read()
        # Three counters since the 30-day backfill: refusals are a
        # work-list, not a loss.
        assert "AAA backfill: {pushed} pushed, {work_list} work-list, {skipped} skipped" in src
