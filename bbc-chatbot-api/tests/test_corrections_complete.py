"""Four live transcripts the correction path could not hear.

MARKY    "And return from Paris to Sydney on nov 9" — a third city. The
         pipeline OVERWROTE his route with it, so the trip he had just
         described disappeared from his own summary.
KAZUO    corrections that must UNSET or REJECT: an explicit one-way still
         showed the cancelled return; an impossible return pair was
         written without a word; "X or Y" silently became Y.
ALISTAIR "Return glight" (typo) was not heard as round trip at all, and
         "round trip dates" got him the SAME summary back — whose date
         line had no label, because round_trip without a return_date fell
         through both branches of build_summary.
LOOP     the same wall three times is not a conversation.

Each resolves in ONE turn here.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.ai.templates import build_summary
from app.models.chat import VisitorInfo
from app.pipeline.corrections import (
    classify_correction,
    detect_extra_leg,
    detect_field_only_correction,
    loop_action,
    needs_return_date,
)
from app.pipeline.entity_extractor import extract_entities
from app.pipeline.orchestrator import _pipeline

_CID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_CTX = {"departure_date": "2026-10-01"}

MARKY_LEAD = {
    "id": "lead-marky", "conversation_id": _CID,
    "origin_code": "TPA", "destination_code": "SJU",
    "departure_date": "2026-11-01", "passengers": 2,
    "cabin_class": "business", "trip_type": "one_way", "notes": "",
}
ALISTAIR_LEAD = {
    "id": "lead-alistair", "conversation_id": _CID,
    "origin_code": "LHR", "destination_code": "JFK",
    "departure_date": "2026-10-01", "passengers": 1,
    "cabin_class": "business", "trip_type": "round_trip", "notes": "",
}

_SUMMARY_CONV = {
    "id": _CID, "mode": "ai", "status": "active", "tunnel": "sales",
    "metadata": {"summary_shown_at": "2026-08-13T10:00:00+00:00"},
}
# Contact complete — otherwise collection isn't finished and Step 7.5
# (where the loop breaker lives) never runs at all.
_FULL_VISITOR = VisitorInfo(name="Loop", email="loop@gmail.com", phone="+12105550100")


def _fp_of(lead: dict) -> str:
    """The fingerprint the pipeline will compute for this lead's summary —
    the counter only advances while the SAME trip is on screen."""
    from app.pipeline.corrections import summary_fingerprint

    return summary_fingerprint(build_summary(lead))


def _probe(message, ctx=_CTX):
    return extract_entities(message, context=ctx)


# ════════════════════════════════════════════════════════════
# MARKY — a third city is an added leg, never an overwrite
# ════════════════════════════════════════════════════════════

class TestMarky:
    def test_two_new_cities_are_an_added_leg(self):
        p = _probe("And return from Paris to Sydney on nov 9")
        leg = detect_extra_leg(p, MARKY_LEAD, "And return from Paris to Sydney on nov 9")
        assert leg == {
            "from": "CDG", "to": "SYD",
            "date": "2026-11-09", "source": "client-stated",
        }

    def test_replacement_language_is_never_a_new_leg(self):
        """"Actually from Miami to San Juan" fixes endpoints — it must NOT
        turn the trip into multi-city, even when the IATA resolver reads
        "San Juan" as SAN and BOTH cities therefore look new."""
        msg = "Actually from Miami to San Juan"
        assert detect_extra_leg(_probe(msg), MARKY_LEAD, msg) is None

    def test_a_new_pair_without_an_additive_cue_is_not_a_leg(self):
        msg = "Miami to San Diego"
        assert detect_extra_leg(_probe(msg), MARKY_LEAD, msg) is None

    def test_one_shared_endpoint_is_an_endpoint_correction(self):
        msg = "and from Tampa to Madrid"          # TPA is already the origin
        assert detect_extra_leg(_probe(msg), MARKY_LEAD, msg) is None

    def test_classify_marks_multi_city(self):
        p = _probe("And return from Paris to Sydney on nov 9")
        out = classify_correction(p, MARKY_LEAD, "And return from Paris to Sydney on nov 9")
        assert out.multi_city is True and out.extra_leg["from"] == "CDG"

    def test_summary_shows_the_client_stated_leg(self):
        summary = build_summary(
            MARKY_LEAD,
            extra_legs=[{"from": "CDG", "to": "SYD", "date": "2026-11-09"}],
        )
        assert "CDG → SYD" in summary and "2026-11-09" in summary
        # …and still asks for confirmation AFTER showing the full trip.
        assert summary.index("CDG → SYD") < summary.index("Is everything correct?")

    @pytest.mark.asyncio
    async def test_e2e_route_survives_and_leg_is_recorded(self):
        upd = AsyncMock()
        with _patches(dict(_SUMMARY_CONV), MARKY_LEAD, update_conversation=upd), \
             patch("app.pipeline.orchestrator.generate_response") as gen:
            gen.side_effect = RuntimeError("stop after the branch under test")
            try:
                await _pipeline(
                    _CID, "And return from Paris to Sydney on nov 9", "sales",
                    VisitorInfo(name="Marky"), None,
                    _persist_state={"ai_persisted": False},
                )
            except Exception:
                pass

        metas = [c.args[1]["metadata"] for c in upd.await_args_list
                 if isinstance(c.args[1], dict) and "metadata" in c.args[1]]
        legs = [m for m in metas if m.get("extra_legs")]
        assert legs, "the client-stated leg must be recorded"
        assert legs[-1]["extra_legs"][0]["from"] == "CDG"
        assert legs[-1]["multi_city_declared"] is True

    def test_leg_fields_never_overwrite_the_trip(self):
        import inspect

        src = inspect.getsource(_pipeline)
        assert '"origin": None if _suppress_leg_fields else' in src
        assert '"departure_date": None if _suppress_leg_fields else' in src


# ════════════════════════════════════════════════════════════
# KAZUO — corrections must UNSET and REJECT
# ════════════════════════════════════════════════════════════

class TestKazuo:
    def test_explicit_one_way_clears_the_return(self):
        p = _probe("actually one way")
        out = classify_correction(p, {"departure_date": "2026-10-01",
                                      "return_date": "2026-10-20"}, "actually one way")
        assert out.clear_return is True

    def test_return_before_departure_is_rejected_and_asked(self):
        p = _probe("returning September 20")
        out = classify_correction(p, {"departure_date": "2026-10-01"},
                                  "returning September 20")
        assert out.ask == "return_before_departure" and out.drop_return is True

    def test_return_equal_to_departure_asks(self):
        p = _probe("returning October 1")
        out = classify_correction(p, {"departure_date": "2026-10-01"},
                                  "returning October 1")
        assert out.ask == "return_equals_departure" and out.drop_return is True

    def test_alternatives_ask_which_and_write_nothing(self):
        p = _probe("Oct 1 or Oct 8")
        out = classify_correction(p, {"departure_date": "2026-09-01"}, "Oct 1 or Oct 8")
        assert out.ask == "date_choice"
        assert out.ask_options == ["Oct 1", "Oct 8"]
        assert out.drop_return is True

    def test_lead_service_can_actually_unset(self):
        import inspect

        from app.services import lead_service

        src = inspect.getsource(lead_service.update_lead_from_entities)
        assert '_clear_return_date' in src
        assert 'lead_payload["return_date"] = None' in src

    @pytest.mark.asyncio
    async def test_e2e_impossible_return_asks_in_one_turn(self):
        with _patches(dict(_SUMMARY_CONV),
                      {**ALISTAIR_LEAD, "trip_type": "one_way"}):
            resp = await _pipeline(
                _CID, "returning September 20", "sales",
                VisitorInfo(name="Kazuo"), None,
                _persist_state={"ai_persisted": False},
            )
        assert "before the departure" in resp.message
        assert "when would you fly back" in resp.message.lower()

    @pytest.mark.asyncio
    async def test_e2e_alternatives_ask_which(self):
        with _patches(dict(_SUMMARY_CONV), ALISTAIR_LEAD):
            resp = await _pipeline(
                _CID, "Oct 1 or Oct 8", "sales", VisitorInfo(name="Kazuo"),
                None, _persist_state={"ai_persisted": False},
            )
        assert "Oct 1" in resp.message and "Oct 8" in resp.message


# ════════════════════════════════════════════════════════════
# ALISTAIR — the word, the label, and the field with no value
# ════════════════════════════════════════════════════════════

class TestAlistair:
    @pytest.mark.parametrize("msg", [
        "Return", "return", "Returning", "Return glight", "Return fligt",
    ])
    def test_standalone_return_is_a_round_trip_signal_post_summary(self, msg):
        assert _probe(msg).trip_type == "round_trip"

    @pytest.mark.parametrize("msg", ["Return", "Return glight"])
    def test_but_never_mid_collection(self, msg):
        """Without a summary on the table the word is ambiguous."""
        assert extract_entities(msg).trip_type is None

    def test_return_the_call_is_not_a_round_trip(self):
        assert _probe("please return the call tomorrow").trip_type is None

    def test_round_trip_without_return_never_renders(self):
        assert needs_return_date(ALISTAIR_LEAD) is True
        assert needs_return_date({**ALISTAIR_LEAD, "return_date": "2026-10-20"}) is False

    def test_the_naked_date_line_is_labelled_if_it_ever_renders(self):
        summary = build_summary(ALISTAIR_LEAD)
        assert "(round trip — return date pending)" in summary

    def test_field_named_without_value_is_detected(self):
        assert detect_field_only_correction("round trip dates", _probe("round trip dates")) == "dates"
        assert detect_field_only_correction("change the dates", _probe("change the dates")) == "dates"
        assert detect_field_only_correction("wrong cities", _probe("wrong cities")) == "route"

    def test_a_real_value_is_not_a_field_ask(self):
        msg = "change the dates to Oct 20"
        assert detect_field_only_correction(msg, _probe(msg)) is None

    @pytest.mark.asyncio
    async def test_e2e_round_trip_dates_asks_for_dates_not_a_rerender(self):
        lead_upd = AsyncMock()
        with _patches(dict(_SUMMARY_CONV), ALISTAIR_LEAD, update_lead=lead_upd):
            resp = await _pipeline(
                _CID, "round trip dates", "sales", VisitorInfo(name="Alistair"),
                None, _persist_state={"ai_persisted": False},
            )
        assert "which dates" in resp.message.lower()
        assert "Is everything correct?" not in resp.message   # no re-render
        # the trip type he DID state is persisted
        assert any(c.args[1].get("trip_type") == "round_trip"
                   for c in lead_upd.await_args_list)

    @pytest.mark.asyncio
    async def test_e2e_return_typo_reopens_the_summary(self):
        upd = AsyncMock()
        with _patches(dict(_SUMMARY_CONV), {**ALISTAIR_LEAD, "trip_type": "one_way"},
                      update_conversation=upd), \
             patch("app.pipeline.orchestrator.generate_response") as gen:
            gen.side_effect = RuntimeError("stop after the branch under test")
            try:
                await _pipeline(
                    _CID, "Return glight", "sales", VisitorInfo(name="Alistair"),
                    None, _persist_state={"ai_persisted": False},
                )
            except Exception:
                pass
        metas = [c.args[1]["metadata"] for c in upd.await_args_list
                 if isinstance(c.args[1], dict) and "metadata" in c.args[1]]
        assert any("summary_shown_at" not in m for m in metas)


# ════════════════════════════════════════════════════════════
# LOOP BREAKER
# ════════════════════════════════════════════════════════════

class TestLoopBreaker:
    def test_thresholds(self):
        assert loop_action(0) is None
        assert loop_action(2) is None
        assert loop_action(3) == "escalate"
        assert loop_action(4) == "consultant"

    def test_fingerprint_is_stable_across_processes(self):
        """hash() is salted per process — with several workers the stored
        fingerprint would never match and the breaker would never fire."""
        import subprocess
        import sys as _sys

        from app.pipeline.corrections import summary_fingerprint

        here = summary_fingerprint("same summary text")
        out = subprocess.run(
            [_sys.executable, "-c",
             "import sys; sys.path.insert(0, r'" + os.path.join(os.path.dirname(__file__), "..") + "');"
             "import os; os.environ.setdefault('SUPABASE_URL','x');"
             "os.environ.setdefault('SUPABASE_KEY','x');"
             "os.environ.setdefault('ANTHROPIC_API_KEY','x');"
             "from app.pipeline.corrections import summary_fingerprint as f;"
             "print(f('same summary text'))"],
            capture_output=True, text=True, timeout=60,
        )
        assert out.stdout.strip() == here

    @pytest.mark.asyncio
    async def test_a_changed_summary_resets_the_counter(self):
        """Three DIFFERENT corrections are iteration, not a loop — the
        client must never be escalated for the pipeline working."""
        lead = {**ALISTAIR_LEAD, "return_date": "2026-10-20"}
        conv = dict(_SUMMARY_CONV)
        conv["metadata"] = {
            "summary_render_count": 3,
            "summary_fingerprint": "stale-different-trip",
        }
        with _patches(conv, lead):
            resp = await _pipeline(
                _CID, "hmm", "sales", _FULL_VISITOR, None,
                _persist_state={"ai_persisted": False},
            )
        assert "Is everything correct?" in resp.message   # rendered, not escalated

    @pytest.mark.asyncio
    async def test_third_render_asks_for_the_trip_in_one_line(self):
        lead = {**ALISTAIR_LEAD, "return_date": "2026-10-20"}
        conv = dict(_SUMMARY_CONV)
        conv["metadata"] = {
            "summary_render_count": 2,           # this attempt makes 3
            "summary_fingerprint": _fp_of(lead),
        }
        with _patches(conv, lead):
            resp = await _pipeline(
                _CID, "hmm", "sales", _FULL_VISITOR, None,
                _persist_state={"ai_persisted": False},
            )
        assert "one line" in resp.message

    @pytest.mark.asyncio
    async def test_fourth_offers_a_consultant_and_queues(self):
        lead = {**ALISTAIR_LEAD, "return_date": "2026-10-20"}
        conv = dict(_SUMMARY_CONV)
        conv["metadata"] = {
            "summary_render_count": 3,           # this attempt makes 4
            "summary_fingerprint": _fp_of(lead),
        }
        with _patches(conv, lead), \
             patch("app.services.routing.dispatch_needs_agent",
                   new=AsyncMock()) as dispatch:
            resp = await _pipeline(
                _CID, "hmm", "sales", _FULL_VISITOR, None,
                _persist_state={"ai_persisted": False},
            )
        assert "consultant" in resp.message.lower()
        dispatch.assert_called()


# ════════════════════════════════════════════════════════════
# The old behavior that must survive
# ════════════════════════════════════════════════════════════

class TestNoRegressions:
    @pytest.mark.asyncio
    async def test_urs_still_reasks(self):
        with _patches(dict(_SUMMARY_CONV), ALISTAIR_LEAD):
            resp = await _pipeline(
                _CID, "Urs", "sales", VisitorInfo(name="Purnisa"), None,
                _persist_state={"ai_persisted": False},
            )
        assert "reply YES" in resp.message

    @pytest.mark.parametrize("msg,expected", [
        ("no return needed", "one_way"),
        ("I do not need a return", "one_way"),
        ("Drop the return", "one_way"),
        ("what is your return policy?", None),
        ("I am a returning customer", None),
        ("I will return to you later", None),
        ("point of no return", None),
    ])
    def test_return_word_is_read_in_context_not_blindly(self, msg, expected):
        """The bare-return rule used to fire on every one of these — the
        negations wrote the INVERSE of what the client asked for."""
        assert _probe(msg).trip_type == expected

    @pytest.mark.parametrize("msg", [
        "Terminal 5 or Terminal 2", "gate 5 or gate 6", "option 1 or option 2",
    ])
    def test_non_date_alternatives_are_not_date_questions(self, msg):
        assert _probe(msg).date_alternatives is None

    def test_single_traveler_never_deletes_a_return(self):
        """ONE_WAY_RE matches "single"; a destructive NULL needs the client
        to have actually said one-way."""
        msg = "Just a single traveler, keep everything else"
        out = classify_correction(_probe(msg),
                                  {"departure_date": "2026-10-01",
                                   "return_date": "2026-10-20"}, msg)
        assert out.clear_return is False

    def test_departure_moved_past_a_stored_return_is_caught(self):
        msg = "Actually depart Nov 20"
        out = classify_correction(_probe(msg),
                                  {"departure_date": "2026-11-01",
                                   "return_date": "2026-11-10"}, msg)
        assert out.ask == "return_before_departure"

    @pytest.mark.asyncio
    async def test_ask_turn_keeps_what_it_did_not_ask_about(self):
        """"Make it 4 passengers, business, returning Oct 1 or Oct 8" asks
        which date — and used to throw the passengers and cabin away."""
        from app.pipeline import orchestrator as orch

        with _patches(dict(_SUMMARY_CONV), ALISTAIR_LEAD), \
             patch.object(orch.lead_service, "update_lead_from_entities",
                          new=AsyncMock()) as upd:
            await _pipeline(
                _CID, "Make it 4 passengers, business, returning Oct 1 or Oct 8",
                "sales", VisitorInfo(name="Kazuo"), None,
                _persist_state={"ai_persisted": False},
            )
        kept = [c.args[1] for c in upd.await_args_list]
        assert any(k.get("passengers") == 4 for k in kept)
        assert all("return_date" not in k for k in kept)

    @pytest.mark.asyncio
    async def test_the_answer_to_our_return_question_is_a_return(self):
        """A bare "October 20" after we asked "when would you fly back?"
        used to overwrite the DEPARTURE and re-trigger the question."""
        from app.pipeline import orchestrator as orch

        conv = dict(_SUMMARY_CONV)
        conv["metadata"] = {
            "awaiting_return_date": True,
            "awaiting_return_departure": "2026-10-01",
        }
        with _patches(conv, ALISTAIR_LEAD), \
             patch.object(orch.lead_service, "update_lead_from_entities",
                          new=AsyncMock()) as upd, \
             patch("app.pipeline.orchestrator.generate_response") as gen:
            gen.side_effect = RuntimeError("stop after extraction")
            try:
                await _pipeline(
                    _CID, "October 20", "sales", _FULL_VISITOR, None,
                    _persist_state={"ai_persisted": False},
                )
            except Exception:
                pass
        written = [c.args[1] for c in upd.await_args_list]
        assert any(w.get("return_date") == "2026-10-20" for w in written)
        assert all(not w.get("departure_date") for w in written)

    def test_tool_merge_cannot_undo_the_suppressions(self):
        import inspect

        src = inspect.getsource(_pipeline)
        assert "_tool_blocked" in src
        assert 'if key in _tool_blocked:' in src

    def test_multi_city_latch_is_released_by_a_simple_trip_type(self):
        import inspect

        src = inspect.getsource(_pipeline)
        assert 'if _probe.trip_type in ("one_way", "round_trip"):' in src
        assert '_meta_clear.pop("multi_city_declared", None)' in src

    def test_correction_context_reaches_step_4(self):
        """#199's day-only return resolved in the probe but Step 4
        re-extracted the message WITHOUT context and threw it away."""
        import inspect

        src = inspect.getsource(_pipeline)
        assert "extract_entities(message, context=_correction_context)" in src


# ── shared patch set ─────────────────────────────────────────

def _patches(conv, lead, update_conversation=None, update_lead=None):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch(
        "app.services.conversation_service.get_or_create_conversation",
        new=AsyncMock(return_value=conv)))
    stack.enter_context(patch(
        "app.services.conversation_service.add_message",
        new=AsyncMock(return_value={"id": "m1"})))
    stack.enter_context(patch(
        "app.pipeline.orchestrator.db.get_recent_messages",
        new=AsyncMock(return_value=[])))
    stack.enter_context(patch(
        "app.services.moderation.moderate_message", new=AsyncMock()))
    stack.enter_context(patch(
        "app.pipeline.orchestrator.db.update_conversation",
        new=update_conversation or AsyncMock()))
    stack.enter_context(patch(
        "app.pipeline.orchestrator.db.update_lead",
        new=update_lead or AsyncMock()))
    stack.enter_context(patch(
        "app.pipeline.orchestrator.lead_service.get_or_create_lead",
        new=AsyncMock(return_value=dict(lead))))
    stack.enter_context(patch(
        "app.pipeline.orchestrator.lead_service.update_lead_from_entities",
        new=AsyncMock()))
    return stack
