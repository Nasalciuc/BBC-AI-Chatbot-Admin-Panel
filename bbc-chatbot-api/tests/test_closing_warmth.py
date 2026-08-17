"""The last thing a paying client reads is written for THEM.

#188 took confirmation away from the LLM (correctly — the state machine
owns YES) and accidentally took the CLOSING with it: since then every
confirmation ended on the same static brand line (the Donna transcript —
a client who had just entrusted us with a $9k trip got the same sentence
as everyone else). The state stays deterministic; the words come back.

Pinned here: a YES streams a real opus closing that references THEIR
trip; slow/failed generation falls back to the brand template and is
COUNTED (reason=closing); the promise never outruns the truth (CRM push
failed → no 30-minute promise; outside team hours → "first thing");
double-YES still acks without regenerating; a thin AAA lead invents
nothing.
"""

import inspect
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.models.chat import VisitorInfo
from app.pipeline.generator import GENERATION_HEALTH
from app.pipeline.orchestrator import _pipeline
from app.services import closing as closing_svc
from app.services.closing import (
    build_closing_prompt,
    closing_promise,
    generate_closing,
    is_thin_lead,
    is_within_team_hours,
)

_CID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

# Donna: TPA→JFK, confirmed, everything captured.
DONNA_LEAD = {
    "id": "lead-donna",
    "conversation_id": _CID,
    "origin_code": "TPA",
    "destination_code": "JFK",
    "departure_date": "2026-11-12",
    "return_date": "2026-11-17",
    "passengers": 2,
    "cabin_class": "business",
    "score": 90,
    "intent_signals": {"occasion": "anniversary"},
}
THIN_LEAD = {"id": "lead-thin", "conversation_id": _CID, "score": 30}
# The realistic thin case that CAN reach a closing: an AAA-defaulted lead
# (placeholders satisfy the missing-fields check) that the client confirms.
AAA_LEAD = {
    "id": "lead-aaa", "conversation_id": _CID, "score": 30,
    "origin_code": "AAA", "destination_code": "AAA",
    "departure_date": "2026-11-12", "passengers": 1, "trip_type": "one_way",
}

DONNA = VisitorInfo(name="Donna", email="donna@gmail.com", phone="+12105550100")

_CONFIRMED_CONV = {
    "id": _CID, "mode": "ai", "status": "active", "tunnel": "sales",
    "metadata": {"summary_shown_at": "2026-08-13T10:00:00+00:00"},
}


# ════════════════════════════════════════════════════════════
# The promise never outruns the truth
# ════════════════════════════════════════════════════════════

class TestPromiseTruth:
    def test_crm_ok_within_hours_promises_thirty_minutes(self):
        noon_est = datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc)  # 12:00 EST, Thu
        assert "~30 minutes" in closing_promise(True, noon_est)

    def test_outside_hours_promises_the_morning_instead(self):
        night_est = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)  # 00:00 EST
        promise = closing_promise(True, night_est)
        assert "first thing" in promise
        assert "30 minutes" not in promise

    def test_failed_push_never_promises_a_time(self):
        """The lead is sitting in the work-list — a 30-minute promise would
        be a lie a human has to walk back."""
        noon_est = datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc)
        promise = closing_promise(False, noon_est)
        assert "30 minutes" not in promise and "first thing" not in promise
        assert "personally" in promise

    def test_weekend_is_outside_team_hours(self):
        saturday_noon = datetime(2026, 8, 15, 16, 0, tzinfo=timezone.utc)
        assert is_within_team_hours(saturday_noon) is False

    def test_hours_check_never_raises_without_tzdata(self):
        """python:3.11-slim ships no tzdata — the promise must degrade, not
        explode."""
        import builtins

        real_import = builtins.__import__

        def _no_zoneinfo(name, *a, **kw):
            if name == "zoneinfo":
                raise ImportError("no tzdata")
            return real_import(name, *a, **kw)

        with patch.object(builtins, "__import__", _no_zoneinfo):
            assert isinstance(is_within_team_hours(), bool)


# ════════════════════════════════════════════════════════════
# The prompt: THEIR detail mandatory, thin leads invent nothing
# ════════════════════════════════════════════════════════════

class TestClosingPrompt:
    def test_rich_lead_demands_a_concrete_detail(self):
        system, user = build_closing_prompt(DONNA_LEAD, DONNA, "promise", "+1 888")
        assert "MANDATORY" in system and "CONCRETE detail" in system
        assert "TPA → JFK" in user and "2026-11-12" in user
        assert "anniversary" in user

    def test_thin_lead_is_forbidden_from_inventing(self):
        system, user = build_closing_prompt(THIN_LEAD, DONNA, "promise", "+1 888")
        assert "Do NOT invent" in system
        assert "MANDATORY" not in system
        # The name is known and may be used; no trip fact may appear.
        assert "Client name: Donna" in user
        for leaked in ("Route:", "Departure:", "Return:", "Cabin:"):
            assert leaked not in user

    def test_context_is_empty_when_nothing_at_all_is_known(self):
        _system, user = build_closing_prompt(
            THIN_LEAD, VisitorInfo(), "promise", "+1 888"
        )
        assert "No trip details captured." in user

    def test_aaa_placeholders_count_as_thin(self):
        assert is_thin_lead({"origin_code": "AAA", "destination_code": "AAA",
                             "departure_date": "2026-11-12"}) is True
        assert is_thin_lead(DONNA_LEAD) is False

    def test_operational_details_and_questions_are_banned(self):
        system, _ = build_closing_prompt(DONNA_LEAD, DONNA, "promise", "+1 888")
        for banned in ("connections", "hubs", "airlines", "aircraft"):
            assert banned in system  # named in the ban list
        assert "Ask NO questions" in system
        assert "SAFETY NET only" in system

    def test_the_promise_is_pinned_verbatim_into_the_prompt(self):
        system, _ = build_closing_prompt(
            DONNA_LEAD, DONNA, "your consultant will pick this up personally", "+1"
        )
        assert "EXACTLY this and nothing stronger: your consultant will pick this up personally" in system


# ════════════════════════════════════════════════════════════
# generate_closing: warmth is worth six seconds, not sixteen
# ════════════════════════════════════════════════════════════

class TestGenerateClosing:
    @pytest.mark.asyncio
    async def test_success_returns_model_and_cost(self):
        def _stream(system, user, on_chunk=None):
            if on_chunk:
                on_chunk("Two seats to New York")
            return "Two seats to New York for your anniversary — all set.", 0.031

        with patch("app.ai.claude.stream_opus", _stream):
            text, model, cost = await generate_closing(DONNA_LEAD, DONNA, crm_ok=True)
        assert "anniversary" in text
        assert "opus" in model
        assert cost == 0.031

    @pytest.mark.asyncio
    async def test_slow_first_chunk_falls_back(self):
        import time

        def _slow(system, user, on_chunk=None):
            time.sleep(0.4)
            return "too late", 0.0

        with patch("app.ai.claude.stream_opus", _slow), \
             patch.object(closing_svc, "CLOSING_FIRST_CHUNK_BUDGET", 0.05):
            text, model, cost = await generate_closing(DONNA_LEAD, DONNA, crm_ok=True)

        assert text is None and model == "template" and cost == 0.0

    @pytest.mark.asyncio
    async def test_error_falls_back(self):
        def _boom(system, user, on_chunk=None):
            raise RuntimeError("provider down")

        with patch("app.ai.claude.stream_opus", _boom):
            text, model, cost = await generate_closing(DONNA_LEAD, DONNA, crm_ok=True)
        assert text is None and model == "template"

    @pytest.mark.asyncio
    async def test_empty_text_falls_back(self):
        def _empty(system, user, on_chunk=None):
            if on_chunk:
                on_chunk("")
            return "   ", 0.0

        with patch("app.ai.claude.stream_opus", _empty), \
             patch.object(closing_svc, "CLOSING_FIRST_CHUNK_BUDGET", 0.2):
            text, model, _cost = await generate_closing(DONNA_LEAD, DONNA, crm_ok=True)
        assert text is None and model == "template"


# ════════════════════════════════════════════════════════════
# Donna, end to end through the pipeline
# ════════════════════════════════════════════════════════════

def _pipeline_patches(conv, lead, crm_success=True):
    from app.services.crm import CRMResult

    return (
        patch("app.services.conversation_service.get_or_create_conversation",
              new=AsyncMock(return_value=conv)),
        patch("app.services.conversation_service.add_message",
              new=AsyncMock(return_value={"id": "m1"})),
        patch("app.pipeline.orchestrator.db.get_recent_messages",
              new=AsyncMock(return_value=[])),
        patch("app.services.moderation.moderate_message", new=AsyncMock()),
        patch("app.pipeline.orchestrator.db.update_conversation", new=AsyncMock()),
        patch("app.pipeline.orchestrator.lead_service.get_or_create_lead",
              new=AsyncMock(return_value=dict(lead))),
        patch("app.pipeline.orchestrator.check_crm_ready", return_value=True),
        patch("app.pipeline.orchestrator.submit_to_crm",
              new=AsyncMock(return_value=CRMResult(success=crm_success, request_id="R-1"))),
        patch("app.pipeline.orchestrator.db.mark_lead_created_in_crm", new=AsyncMock()),
        patch("app.pipeline.orchestrator.generate_response",
              return_value=SimpleNamespace(
                  text="(main answer)", model_used="sonnet", cost=0.001,
                  route_card=None, tool_entities=None)),
        patch("app.pipeline.orchestrator.db.update_lead", new=AsyncMock()),
    )


async def _run_yes(lead=DONNA_LEAD, crm_success=True, claimed=True):
    conv = dict(_CONFIRMED_CONV)
    conv["metadata"] = dict(conv["metadata"])
    with _pipeline_patches(conv, lead, crm_success)[0], \
         _pipeline_patches(conv, lead, crm_success)[1]:
        pass  # patches are applied below via contextlib for clarity
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in _pipeline_patches(conv, lead, crm_success):
            stack.enter_context(p)
        stack.enter_context(
            patch("app.services.closing.claim_closing_sent",
                  new=AsyncMock(return_value=claimed))
        )
        return await _pipeline(
            _CID, "yes", "sales", DONNA, None,
            _persist_state={"ai_persisted": False},
        )


class TestDonnaEndToEnd:
    @pytest.mark.asyncio
    async def test_yes_gets_a_written_closing_not_the_brand_template(self):
        from app.services.closing import compute_closing_text

        def _stream(system, user, on_chunk=None):
            if on_chunk:
                on_chunk("Tampa")
            return (
                "Tampa to New York on the 12th, back on the 17th — your "
                "anniversary trip is in expert hands.",
                0.04,
            )

        with patch("app.ai.claude.stream_opus", _stream):
            resp = await _run_yes()

        assert "anniversary" in resp.message
        assert resp.message != compute_closing_text(None)
        assert resp.model_used != "template"      # the panel shows the real tier
        assert "opus" in resp.model_used

    @pytest.mark.asyncio
    async def test_generation_failure_serves_the_template_and_counts_it(self):
        GENERATION_HEALTH["by_reason"] = {}
        GENERATION_HEALTH["fallbacks_since_boot"] = 0

        def _boom(system, user, on_chunk=None):
            raise RuntimeError("provider down")

        with patch("app.ai.claude.stream_opus", _boom):
            resp = await _run_yes()

        assert resp.model_used == "template"
        assert GENERATION_HEALTH["by_reason"].get("closing") == 1
        assert GENERATION_HEALTH["last_reason"] == "closing"

    @pytest.mark.asyncio
    async def test_failed_crm_push_still_gets_a_closing(self):
        """Before: a confirmed client whose push failed got NO closing at
        all — the branch required a successful submit."""
        captured = {}

        def _stream(system, user, on_chunk=None):
            captured["system"] = system
            if on_chunk:
                on_chunk("x")
            return "Your trip is with your consultant now.", 0.02

        with patch("app.ai.claude.stream_opus", _stream):
            resp = await _run_yes(crm_success=False)

        assert "consultant" in resp.message
        # …and the promise is the safe one.
        assert "30 minutes" not in captured["system"]
        assert "personally" in captured["system"]

    @pytest.mark.asyncio
    async def test_double_yes_acks_without_regenerating(self):
        calls = {"n": 0}

        def _stream(system, user, on_chunk=None):
            calls["n"] += 1
            if on_chunk:
                on_chunk("x")
            return "second closing", 0.02

        with patch("app.ai.claude.stream_opus", _stream):
            resp = await _run_yes(claimed=False)

        assert "You're all set" in resp.message
        assert calls["n"] == 0        # no second generation, no second cost

    @pytest.mark.asyncio
    async def test_aaa_lead_closing_invents_nothing(self):
        """An AAA-defaulted lead reaches the closing (placeholders satisfy
        the missing-fields check) — and must not be dressed up as a real
        itinerary."""
        captured = {}

        def _stream(system, user, on_chunk=None):
            captured["system"] = system
            captured["user"] = user
            if on_chunk:
                on_chunk("x")
            return "You're in good hands — your consultant will confirm the details.", 0.01

        with patch("app.ai.claude.stream_opus", _stream):
            resp = await _run_yes(lead=AAA_LEAD)

        assert "Do NOT invent" in captured["system"]
        assert "MANDATORY" not in captured["system"]
        assert resp.message

    @pytest.mark.asyncio
    async def test_a_lead_without_route_never_reaches_a_closing_at_all(self):
        """Collection isn't finished — Step 7.5 must not fire."""
        calls = {"n": 0}

        def _stream(system, user, on_chunk=None):
            calls["n"] += 1
            return "should not happen", 0.0

        with patch("app.ai.claude.stream_opus", _stream):
            await _run_yes(lead=THIN_LEAD)
        assert calls["n"] == 0


class TestSourceContracts:
    def test_closing_no_longer_requires_a_successful_push(self):
        src = inspect.getsource(_pipeline)
        assert "_confirmed_this_turn or _crm_submitted_this_turn" in src

    def test_template_is_only_the_fallback(self):
        src = inspect.getsource(_pipeline)
        gen_at = src.index("generate_closing(")
        tpl_at = src.index("compute_closing_text(_site)")
        assert gen_at < tpl_at, "generation must be attempted before the template"

    def test_real_model_and_cost_reach_the_panel(self):
        src = inspect.getsource(_pipeline)
        assert "gen.model_used = _closing_model" in src
        assert "gen.cost = _closing_cost" in src
