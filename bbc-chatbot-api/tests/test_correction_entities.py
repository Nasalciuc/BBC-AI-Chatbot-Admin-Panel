"""Post-summary corrections hear the client — the Deborah transcript.

Conv w/ Deborah (TPA→SJU): the summary printed "(one-way)" from mere
ABSENCE of a return, then burned TWO client turns:
  "No returning on the 17th am if possible"  → re-ask (day-only date:
      no parser, no context)
  "Round trip, returning on the 17th"        → re-ask although the
      extractor DID set trip_type — the gate checked only origin/dest/
      dates/passengers and threw the entity away.
Only "Round trip 12–17 of November 2026" passed.

Pins: the gate hears trip_type/cabin_class; day-only dates resolve from
the departure context (correction path only, collision-safe); the return
question is asked BEFORE the summary; the second re-ask teaches the
format instead of repeating the wall.
"""

import inspect
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.models.chat import VisitorInfo
from app.pipeline.entity_extractor import extract_entities
from app.pipeline.orchestrator import _pipeline

_CID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_DEP_CTX = {"departure_date": "2026-11-12"}  # Deborah's TPA→SJU departure

_AWAITING_CONV = {
    "id": _CID, "mode": "ai", "status": "active", "tunnel": "sales",
    "metadata": {"summary_shown_at": "2026-08-13T10:00:00+00:00"},
}

_DEBORAH_LEAD = {
    "id": "lead-deb", "conversation_id": _CID,
    "origin_code": "TPA", "destination_code": "SJU",
    "departure_date": "2026-11-12", "notes": "",
}


def _patches(conv):
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
              new=AsyncMock(return_value=dict(_DEBORAH_LEAD))),
        patch("app.pipeline.orchestrator.db.update_lead", new=AsyncMock()),
    )


# ════════════════════════════════════════════════════════════
# Day-only dates — extractor unit (correction path only)
# ════════════════════════════════════════════════════════════

class TestDayOnlyDates:
    def test_deborah_message_one_verbatim(self):
        e = extract_entities(
            "No returning on the 17th am if possible", context=_DEP_CTX
        )
        assert e.return_date == "2026-11-17"      # 17 > 12 → departure's month
        assert e.trip_type == "round_trip"
        assert e.time_preference == "morning"     # "am" survives

    def test_bare_ordinal_resolves(self):
        e = extract_entities("the 17th", context=_DEP_CTX)
        assert e.return_date == "2026-11-17"

    def test_day_before_departure_rolls_to_next_month(self):
        e = extract_entities("returning on the 5th", context=_DEP_CTX)
        assert e.return_date == "2026-12-05"      # 5 <= 12 → next month

    def test_december_rolls_into_next_year(self):
        e = extract_entities(
            "returning the 5th", context={"departure_date": "2026-12-20"}
        )
        assert e.return_date == "2027-01-05"

    def test_no_context_no_resolution(self):
        e = extract_entities("No returning on the 17th am if possible")
        assert e.return_date is None              # old call sites unchanged

    def test_passenger_phrase_is_not_a_date(self):
        e = extract_entities("the 3 of us", context=_DEP_CTX)
        assert e.return_date is None

    def test_pax_and_ordinal_coexist(self):
        e = extract_entities("5 of us on the 5th", context=_DEP_CTX)
        assert e.passengers == 5
        assert e.return_date == "2026-12-05"      # 5 <= 12 → next month

    def test_full_month_date_wins_over_ordinal(self):
        e = extract_entities(
            "returning November 20th actually, not the 17th", context=_DEP_CTX
        )
        assert e.return_date is not None
        assert e.return_date.endswith("-11-20")

    def test_nonsense_day_yields_nothing(self):
        e = extract_entities("returning on the 45", context=_DEP_CTX)
        assert e.return_date is None
        e2 = extract_entities(
            "returning the 31st", context={"departure_date": "2026-02-10"}
        )
        assert e2.return_date is None             # Feb 31 → re-ask is correct


# ════════════════════════════════════════════════════════════
# The gate hears trip_type (Deborah's message 2 — its own test)
# ════════════════════════════════════════════════════════════

class TestGateHearsTripType:
    @pytest.mark.asyncio
    async def test_round_trip_alone_triggers_the_correction(self):
        """"Round trip, returning on the 17th" — the extractor set
        trip_type all along; the gate must not throw it away."""
        p = _patches(dict(_AWAITING_CONV))
        upd = p[4]
        with p[0], p[1], p[2], p[3], upd as upd_mock, p[5], p[6], \
             patch("app.pipeline.orchestrator.generate_response") as gen:
            gen.side_effect = RuntimeError("stop after the branch under test")
            try:
                await _pipeline(
                    _CID, "Round trip, returning on the 17th", "sales",
                    VisitorInfo(name="Deborah"), None,
                    _persist_state={"ai_persisted": False},
                )
            except Exception:
                pass
        meta_updates = [c.args[1].get("metadata") for c in upd_mock.await_args_list
                        if isinstance(c.args[1], dict) and "metadata" in c.args[1]]
        assert any(m is not None and "summary_shown_at" not in m for m in meta_updates), \
            "trip_type correction must re-open the summary"

    def test_gate_lists_trip_type_and_cabin(self):
        src = inspect.getsource(_pipeline)
        assert '"trip_type", "cabin_class",' in src
        assert "triggered by:" in src   # the trigger fields are logged


# ════════════════════════════════════════════════════════════
# Deborah e2e: ONE turn instead of three
# ════════════════════════════════════════════════════════════

class TestDeborahOneTurn:
    @pytest.mark.asyncio
    async def test_message_one_now_corrects_and_notes_morning(self):
        p = _patches(dict(_AWAITING_CONV))
        upd, lead_upd = p[4], p[6]
        with p[0], p[1], p[2], p[3], upd as upd_mock, p[5], lead_upd as lead_mock, \
             patch("app.pipeline.orchestrator.generate_response") as gen:
            gen.side_effect = RuntimeError("stop after the branch under test")
            try:
                await _pipeline(
                    _CID, "No returning on the 17th am if possible", "sales",
                    VisitorInfo(name="Deborah"), None,
                    _persist_state={"ai_persisted": False},
                )
            except Exception:
                pass
        # The summary re-opens on this very turn — no re-ask wall.
        meta_updates = [c.args[1].get("metadata") for c in upd_mock.await_args_list
                        if isinstance(c.args[1], dict) and "metadata" in c.args[1]]
        assert any(m is not None and "summary_shown_at" not in m for m in meta_updates)
        # The morning preference survives into the lead notes.
        note_writes = [c.args[1] for c in lead_mock.await_args_list]
        assert any("morning" in (w.get("notes") or "") for w in note_writes)


# ════════════════════════════════════════════════════════════
# Re-asks: still fire on true ambiguity; the second teaches format
# ════════════════════════════════════════════════════════════

class TestReasks:
    @pytest.mark.asyncio
    async def test_urs_still_reasks(self):
        p = _patches(dict(_AWAITING_CONV))
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6]:
            resp = await _pipeline(
                _CID, "Urs", "sales", VisitorInfo(name="Purnisa"),
                None, _persist_state={"ai_persisted": False},
            )
        assert "reply YES" in resp.message

    @pytest.mark.asyncio
    async def test_second_reask_teaches_the_format(self):
        conv = dict(_AWAITING_CONV)
        conv["metadata"] = {
            "summary_shown_at": "2026-08-13T10:00:00+00:00",
            "summary_reask_count": 1,
        }
        p = _patches(conv)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6]:
            resp = await _pipeline(
                _CID, "Urs", "sales", VisitorInfo(name="Deborah"),
                None, _persist_state={"ai_persisted": False},
            )
        # The old example was a hardcoded date — "e.g. 'returning Nov 17'".
        # 18 Aug 2026 a client flying in December read it and answered
        # "I never said anything about November": he thought we were inventing
        # his trip. The second re-ask still teaches the format, but it names
        # the FIELDS he can change and never a date that is not his.
        assert "Nov" not in resp.message
        for field in ("dates", "travellers", "cabin"):
            assert field in resp.message, f"second re-ask must name {field}"

    def test_correction_resets_the_reask_counter(self):
        src = inspect.getsource(_pipeline)
        assert '_meta_upd.pop("summary_reask_count", None)' in src


# ════════════════════════════════════════════════════════════
# Pre-summary: never print one-way from absence
# ════════════════════════════════════════════════════════════

class TestNeverOneWayFromAbsence:
    def test_return_question_directive_reaches_the_model(self):
        from app.ai.prompts import build_conversational_prompt

        static, dynamic = build_conversational_prompt(
            tunnel="sales", visitor=VisitorInfo(name="Deborah")
        )
        prompt = f"{static}\n{dynamic}"
        assert "And when would you fly back" in prompt
        assert "Never label a trip one-way from mere absence" in prompt
