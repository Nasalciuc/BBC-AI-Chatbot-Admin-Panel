"""CRM push truth — state is written only on proof; buttons do what they
say; gold never rots outside the CRM.

E1 #1259 pessaint: malformed email → CRM rejected at validation (a 200
   with no id) → our flag went true anyway. Now: success REQUIRES the id;
   and the gate refuses the malformed email before the call is even made.
E2 #1372 Jia: the panel button PATCHed created_in_crm=true — zero "CRM OK"
   log, no CRM row. Now: the endpoint performs the real push and returns
   its truth (422 on failure, flag untouched).
E3 #1364 Paulette: human takeover halted the confirm-flow push; nothing
   retried — 24/617 gold orphans. Now: a 30-min backstop auto-pushes only
   FRESH orphans (1h–48h old, max 3 attempts); older history goes to the
   human-gated "CRM pending" work-list.
"""

import os
import sys
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
    crm_push_gate,
    push_lead_to_crm,
    submit_to_crm,
)

APP = os.path.join(os.path.dirname(__file__), "..", "app")


def _read(*parts: str) -> str:
    with open(os.path.join(APP, *parts), encoding="utf-8") as f:
        return f.read()


GOOD_LEAD = {
    "id": "lead-1",
    "score": 80,
    "origin_code": "LAX",
    "destination_code": "SYD",
    "departure_date": "2026-10-28",
    "conversation_id": "conv-1",
}
GOOD_VISITOR = SimpleNamespace(name="Catherine", email="catherine@gmail.com", phone="+12105550100")


@pytest.fixture(autouse=True)
def _reset_counters():
    for k in ("ok", "failed", "refused"):
        CRM_PUSH_HEALTH[k] = 0
    CRM_PUSH_HEALTH["last_error_at"] = None
    CRM_PUSH_HEALTH["last_refusal_reason"] = None
    yield


def _resp(status: int, body: dict | None = None, text: str = ""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.text = text or str(body or "")
    return r


def _client_returning(resp):
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    return client


# ════════════════════════════════════════════════════════════
# E1 — success requires 2xx AND an id
# ════════════════════════════════════════════════════════════

class TestSubmitProof:
    @pytest.mark.asyncio
    async def test_200_with_id_is_success(self):
        client = _client_returning(_resp(200, {"data": {"id": "R-123"}}))
        with patch.object(crm, "_get_crm_client", return_value=client), \
             patch.object(crm.settings, "crm_api_url", "https://crm.test"):
            res = await submit_to_crm(GOOD_LEAD, GOOD_VISITOR, "conv-1")
        assert res.success and res.request_id == "R-123"

    @pytest.mark.asyncio
    async def test_200_without_id_is_failure(self):
        """The CRM's validation-rejection shape (#1259 pessaint)."""
        client = _client_returning(_resp(200, {"errors": {"email": "invalid"}}))
        with patch.object(crm, "_get_crm_client", return_value=client), \
             patch.object(crm.settings, "crm_api_url", "https://crm.test"):
            res = await submit_to_crm(GOOD_LEAD, GOOD_VISITOR, "conv-1")
        assert not res.success
        assert res.error == "no_id_in_response"

    @pytest.mark.asyncio
    async def test_4xx_is_failure(self):
        client = _client_returning(_resp(422, text="validation failed"))
        with patch.object(crm, "_get_crm_client", return_value=client), \
             patch.object(crm.settings, "crm_api_url", "https://crm.test"):
            res = await submit_to_crm(GOOD_LEAD, GOOD_VISITOR, "conv-1")
        assert not res.success and "422" in res.error


# ════════════════════════════════════════════════════════════
# Gate matrix — junk never reaches the CRM, refusals are visible
# ════════════════════════════════════════════════════════════

class TestGate:
    @pytest.mark.parametrize("email,expected", [
        ("thanks..jean the flight was", "email_invalid"),   # E1 pessaint
        ("", "email_invalid"),
        ("test@example.com", "test_email"),
        ("jean@yahool.com", "email_typo_domain"),
        ("jean@fomcast.net", "email_typo_domain"),
        ("contest@gmail.com", None),  # prefix rule — substring would refuse this
        ("catherine@gmail.com", None),
    ])
    def test_email_rules(self, email, expected):
        assert crm_push_gate(GOOD_LEAD, email) == expected

    def test_low_score_refused(self):
        assert crm_push_gate({**GOOD_LEAD, "score": 0}, "a@b.com") == "low_score"
        assert crm_push_gate({**GOOD_LEAD, "score": 39}, "a@b.com") == "low_score"

    def test_same_route_refused(self):
        lead = {**GOOD_LEAD, "origin_code": "LHR", "destination_code": "LHR"}
        assert crm_push_gate(lead, "a@b.com") == "same_route"

    def test_typo_domains_never_auto_corrected(self):
        src = _read("services", "crm.py")
        assert "REFUSED, never auto-corrected" in src


class TestPushPath:
    @pytest.mark.asyncio
    async def test_gate_refusal_skips_call_and_is_visible(self):
        from app.db import supabase as sb

        with patch.object(crm, "submit_to_crm", new_callable=AsyncMock) as submit, \
             patch.object(sb, "update_lead_crm_push_state", new_callable=AsyncMock) as state:
            res = await push_lead_to_crm(
                {**GOOD_LEAD, "score": 0}, GOOD_VISITOR, "conv-1"
            )

        assert not res.success and res.error == "gate:low_score"
        submit.assert_not_awaited()
        state.assert_awaited_once_with("lead-1", gate_reason="low_score")
        assert CRM_PUSH_HEALTH["refused"] == 1
        assert CRM_PUSH_HEALTH["last_refusal_reason"] == "low_score"

    @pytest.mark.asyncio
    async def test_success_writes_flag_with_proof(self):
        from app.db import supabase as sb

        with patch.object(crm, "submit_to_crm", new_callable=AsyncMock) as submit, \
             patch.object(sb, "mark_lead_created_in_crm", new_callable=AsyncMock) as mark:
            submit.return_value = CRMResult(success=True, request_id="R-9")
            res = await push_lead_to_crm(GOOD_LEAD, GOOD_VISITOR, "conv-1")

        assert res.success
        mark.assert_awaited_once_with("lead-1", crm_lead_id="R-9")
        assert CRM_PUSH_HEALTH["ok"] == 1

    @pytest.mark.asyncio
    async def test_flag_write_failure_reports_failure_so_retries_stay_capped(self):
        """CRM accepted, DB flag write died: reporting success would let the
        backstop re-push a fresh CRM duplicate every tick — report failure."""
        from app.db import supabase as sb

        with patch.object(crm, "submit_to_crm", new_callable=AsyncMock) as submit, \
             patch.object(sb, "mark_lead_created_in_crm", new_callable=AsyncMock) as mark:
            submit.return_value = CRMResult(success=True, request_id="R-9")
            mark.return_value = None  # write failed (mark never raises)
            res = await push_lead_to_crm(GOOD_LEAD, GOOD_VISITOR, "conv-1")

        assert not res.success
        assert res.error == "flag_write_failed"
        assert res.request_id == "R-9"  # the receipt survives in the result
        assert CRM_PUSH_HEALTH["failed"] == 1

    @pytest.mark.asyncio
    async def test_failure_leaves_flag_false(self):
        from app.db import supabase as sb

        with patch.object(crm, "submit_to_crm", new_callable=AsyncMock) as submit, \
             patch.object(sb, "mark_lead_created_in_crm", new_callable=AsyncMock) as mark:
            submit.return_value = CRMResult(success=False, error="HTTP 500")
            res = await push_lead_to_crm(GOOD_LEAD, GOOD_VISITOR, "conv-1")

        assert not res.success
        mark.assert_not_awaited()
        assert CRM_PUSH_HEALTH["failed"] == 1
        assert CRM_PUSH_HEALTH["last_error_at"] is not None


# ════════════════════════════════════════════════════════════
# E2 — the button performs the push
# ════════════════════════════════════════════════════════════

class TestButtonTruth:
    @pytest.mark.asyncio
    async def test_endpoint_runs_real_push_and_fails_loud(self):
        from fastapi import HTTPException

        from app.api import leads as leads_api
        from app.db import supabase as sb

        with patch.object(sb, "get_lead_full", new_callable=AsyncMock) as get_lead, \
             patch.object(sb, "get_conversation_simple", new_callable=AsyncMock) as get_conv, \
             patch.object(sb, "derive_conversation_tag", return_value="active"), \
             patch.object(crm, "push_lead_to_crm", new_callable=AsyncMock) as push:
            get_lead.return_value = dict(GOOD_LEAD, created_in_crm=False)
            get_conv.return_value = {
                "visitor_name": "Jia", "visitor_email": "jia@gmail.com",
                "visitor_phone": "+1", "metadata": {}, "visitor_id": "v-1",
            }
            push.return_value = CRMResult(success=False, error="HTTP 422")

            with pytest.raises(HTTPException) as exc:
                await leads_api.mark_lead_created_in_crm(
                    "lead-1", user={"role": "admin"}
                )

        assert exc.value.status_code == 422
        assert "HTTP 422" in exc.value.detail
        push.assert_awaited_once()  # the REAL push ran — no silent flag flip

    @pytest.mark.asyncio
    async def test_endpoint_success_returns_updated_lead(self):
        from app.api import leads as leads_api
        from app.db import supabase as sb

        updated = dict(GOOD_LEAD, created_in_crm=True, crm_lead_id="R-77")
        with patch.object(sb, "get_lead_full", new_callable=AsyncMock) as get_lead, \
             patch.object(sb, "get_conversation_simple", new_callable=AsyncMock) as get_conv, \
             patch.object(sb, "derive_conversation_tag", return_value="active"), \
             patch.object(crm, "push_lead_to_crm", new_callable=AsyncMock) as push:
            get_lead.side_effect = [dict(GOOD_LEAD, created_in_crm=False), updated]
            get_conv.return_value = {"visitor_email": "jia@gmail.com", "metadata": {}}
            push.return_value = CRMResult(success=True, request_id="R-77")

            out = await leads_api.mark_lead_created_in_crm("lead-1", user={"role": "admin"})

        assert out["success"] and out["data"]["crm_lead_id"] == "R-77"

    @pytest.mark.asyncio
    async def test_manual_crm_id_rejects_whitespace(self):
        from fastapi import HTTPException

        from app.api import leads as leads_api

        with pytest.raises(HTTPException) as exc:
            await leads_api.set_manual_crm_id("lead-1", crm_id="   ", user={"role": "admin"})
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_manual_crm_id_is_a_separate_declaring_action(self):
        from app.api import leads as leads_api
        from app.db import supabase as sb

        with patch.object(sb, "mark_lead_created_in_crm", new_callable=AsyncMock) as mark:
            mark.return_value = dict(GOOD_LEAD, created_in_crm=True, crm_lead_id="MAN-1")
            out = await leads_api.set_manual_crm_id("lead-1", crm_id="MAN-1", user={"role": "admin"})

        assert out["success"]
        mark.assert_awaited_once_with("lead-1", crm_lead_id="MAN-1")


# ════════════════════════════════════════════════════════════
# E3 — the backstop: fresh orphans auto, history human-gated
# ════════════════════════════════════════════════════════════

class TestOrphanBackstop:
    @pytest.mark.asyncio
    async def test_window_is_1h_to_48h_and_pushes(self):
        from app.api import cron
        from app.db import supabase as sb

        orphan = dict(GOOD_LEAD, crm_push_attempts=0)
        with patch.object(sb, "get_crm_orphan_leads", new_callable=AsyncMock) as get_orphans, \
             patch.object(sb, "get_conversation_simple", new_callable=AsyncMock) as get_conv, \
             patch.object(crm, "push_lead_to_crm", new_callable=AsyncMock) as push:
            get_orphans.return_value = [orphan]
            get_conv.return_value = {"visitor_email": "paulette@gmail.com", "metadata": {}}
            push.return_value = CRMResult(success=True, request_id="R-24")

            out = await cron.run_crm_orphan_backstop()

        kwargs = get_orphans.await_args.kwargs
        assert kwargs["min_score"] == 70 and kwargs["max_attempts"] == 3
        # younger_than (48h ago) must be OLDER than older_than (1h ago):
        # the auto window covers ONLY fresh failures, never deep history.
        assert kwargs["younger_than_iso"] < kwargs["older_than_iso"]
        assert out["results"][0]["status"] == "pushed"

    @pytest.mark.asyncio
    async def test_failure_spends_an_attempt(self):
        from app.api import cron
        from app.db import supabase as sb

        orphan = dict(GOOD_LEAD, crm_push_attempts=2)
        with patch.object(sb, "get_crm_orphan_leads", new_callable=AsyncMock) as get_orphans, \
             patch.object(sb, "get_conversation_simple", new_callable=AsyncMock) as get_conv, \
             patch.object(sb, "update_lead_crm_push_state", new_callable=AsyncMock) as state, \
             patch.object(crm, "push_lead_to_crm", new_callable=AsyncMock) as push:
            get_orphans.return_value = [orphan]
            get_conv.return_value = {"metadata": {}}
            push.return_value = CRMResult(success=False, error="HTTP 500")

            out = await cron.run_crm_orphan_backstop()

        state.assert_awaited_once_with("lead-1", bump_attempts_from=2)
        assert out["results"][0]["status"] == "failed"

    def test_scheduler_runs_backstop_every_30_min(self):
        src = _read("services", "scheduler.py")
        assert '("crm_orphan_backstop", run_crm_orphan_backstop, 1800)' in src


# ════════════════════════════════════════════════════════════
# Wiring contracts
# ════════════════════════════════════════════════════════════

class TestWiring:
    def test_health_carries_crm_pushes_in_both_payloads(self):
        src = _read("api", "health.py")
        assert src.count('"crm_pushes"') == 2

    def test_work_list_filter_includes_refusals_gold_and_failed(self):
        src = _read("db", "supabase.py")
        assert (
            '"crm_push_gate_reason.not.is.null,"\n'
            '                        "score.gte.70,"\n'
            '                        "crm_push_attempts.gt.0"'
        ) in src
        assert "created_in_crm.is.null,created_in_crm.eq.false" in src

    def test_migration_030_contract(self):
        with open(
            os.path.join(APP, "..", "migrations", "030_crm_lead_id.sql"),
            encoding="utf-8",
        ) as f:
            sql = f.read()
        assert "crm_lead_id" in sql
        assert "crm_push_attempts" in sql
        assert "crm_push_gate_reason" in sql

    def test_panel_button_pushes_not_declares(self):
        with open(
            os.path.join(
                APP, "..", "..", "bbc-admin-app", "src", "features", "chats", "detail.tsx"
            ),
            encoding="utf-8",
        ) as f:
            src = f.read()
        assert "Push to CRM" in src
        assert "Submit to CRM" not in src
