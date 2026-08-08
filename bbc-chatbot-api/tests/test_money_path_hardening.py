"""Money-path hardening — the lead a consultant can trust.

Pins the fixes for two live incidents:
- Conv "Costa": the pipeline extracted entities from the AI's OWN text,
  parsed "available 24/7" as July 24, invented a departure date, skipped the
  dates question, and shipped a phantom itinerary; the client's "no" to
  "Is everything correct?" had no branch to land in.
- Conv #1244 "Ali": a 4-city trip collapsed to "JFK↔CMN round trip", middle
  legs lost, a middle-leg date mislabeled return_date, "one" unparsed.
"""

import inspect
import os
import re
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.ai.templates import TEMPLATES, get_template
from app.models.chat import VisitorInfo
from app.pipeline.entity_extractor import extract_entities
from app.services import lead_service
from app.services.lead_service import LEAD_WRITE_HEALTH, ensure_lead, update_lead_from_entities

_CID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


# ── 1a + 1b: AI text never writes lead facts; 24/7 is not a date ─────────

class TestNoPhantomDates:
    def test_ai_recon_step_is_gone(self):
        from app.pipeline import orchestrator as orch

        src = inspect.getsource(orch)
        assert "_ai_recon" not in src, "AI-text reconciliation must stay deleted"
        assert "RECONCILE FROM AI RESPONSE" not in src.replace(
            "STEP 6.1.5 (RECONCILE FROM AI RESPONSE TEXT) is DELETED", ""
        )
        assert "output, not evidence" in src  # the guard comment

    @pytest.mark.parametrize(
        "text",
        [
            "We're available 24/7 to help with your booking.",
            "Our consultants answer 24/7 — call anytime.",
            "support 24/7",
        ],
    )
    def test_24_7_idiom_is_not_a_date(self, text):
        e = extract_entities(text)
        assert e.departure_date is None
        assert e.return_date is None

    def test_real_dates_still_parse(self):
        assert extract_entities("July 24").departure_date is not None
        assert extract_entities("24/07").departure_date is not None
        assert extract_entities("flying 7/24 next year").departure_date is not None


# ── 1c + 1d + 1e: rejection branch, open-door arming, gated closing ──────

class TestRejectionBranch:
    @pytest.mark.asyncio
    async def test_post_summary_no_asks_for_correction(self):
        conv = {
            "id": _CID,
            "mode": "ai",
            "status": "active",
            "tunnel": "sales",
            "metadata": {"summary_shown_at": "2026-08-08T10:00:00+00:00"},
        }
        from app.pipeline.orchestrator import _pipeline

        add_msg = AsyncMock(return_value={"id": "m1"})
        with patch(
            "app.services.conversation_service.get_or_create_conversation",
            new=AsyncMock(return_value=conv),
        ), patch(
            "app.services.conversation_service.add_message", add_msg
        ), patch(
            "app.pipeline.orchestrator.db.get_recent_messages",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.services.moderation.moderate_message", new=AsyncMock()
        ), patch(
            "app.pipeline.orchestrator.db.update_conversation", new=AsyncMock()
        ) as upd:
            resp = await _pipeline(
                _CID, "No", "sales", VisitorInfo(name="Costa"),
                None, _persist_state={"ai_persisted": False},
            )

        assert "what should I fix" in resp.message
        assert resp.model_used == "template"
        # Closing never fired; open door never armed on a rejection.
        assert all(
            "consultant takes it from here" not in str(c)
            for c in add_msg.await_args_list
        )
        for call in upd.await_args_list:
            meta = (call.args[1] or {}).get("metadata") or {}
            assert not meta.get("open_door_pending")
            assert not meta.get("confirmed_at")

    def test_reject_words_checked_before_confirm_words(self):
        from app.pipeline import orchestrator as orch

        src = inspect.getsource(orch)
        assert src.index("_reject_words") < src.index("_confirm_words")

    def test_correction_template_exists(self):
        text = get_template("summary_correction", "sales", VisitorInfo())
        assert "what should I fix" in text

    def test_open_door_arms_at_confirmation_not_summary(self):
        from app.pipeline import orchestrator as orch

        src = inspect.getsource(orch)
        _arm = src.index('open_door_pending"] = True')
        assert abs(_arm - src.index('confirmed_at"] = ')) < 600
        assert abs(_arm - src.index('summary_shown_at"] = ')) > 600

    def test_closing_requires_confirmed_at(self):
        from app.pipeline import orchestrator as orch

        src = inspect.getsource(orch)
        assert 'elif _conv_meta.get("confirmed_at") and _crm_submitted_this_turn:' in src


# ── 1f + 1g: ensure_lead + un-swallowed money path ───────────────────────

def _table_router(tables: dict):
    """MagicMock supabase client whose .table(name) chains resolve per-table."""
    client = MagicMock()

    def _table(name):
        return tables[name]

    client.table.side_effect = _table
    return client


def _chain(result_data, insert_data=None):
    t = MagicMock()
    t.select.return_value = t
    t.eq.return_value = t
    t.limit.return_value = t
    t.update.return_value = t
    t.insert.return_value = t
    t.execute.return_value = MagicMock(data=result_data)
    if insert_data is not None:
        # insert(...).execute() returns the inserted row
        insert_chain = MagicMock()
        insert_chain.execute.return_value = MagicMock(data=insert_data)
        t.insert.return_value = insert_chain
    return t


async def _fake_run_sync(fn):
    return fn()


class TestEnsureLead:
    @pytest.mark.asyncio
    async def test_creates_missing_lead_with_dict_signals(self):
        lead_row = {"id": "L1", "conversation_id": _CID, "intent_signals": {}}
        leads = _chain([], insert_data=[lead_row])
        convs = _chain([{"tunnel": "sales"}])
        client = _table_router({"leads": leads, "conversations": convs})
        with patch("app.db.supabase.get_client", return_value=client), \
             patch("app.db.supabase._run_sync", side_effect=_fake_run_sync):
            row = await ensure_lead(_CID)
        assert row == lead_row
        insert_payload = leads.insert.call_args.args[0]
        assert insert_payload["intent_signals"] == {}  # never the legacy []

    @pytest.mark.asyncio
    async def test_existing_lead_returned_without_insert(self):
        lead_row = {"id": "L1", "conversation_id": _CID}
        leads = _chain([lead_row])
        client = _table_router({"leads": leads})
        with patch("app.db.supabase.get_client", return_value=client), \
             patch("app.db.supabase._run_sync", side_effect=_fake_run_sync):
            row = await ensure_lead(_CID)
        assert row == lead_row
        leads.insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_support_conversations_never_get_lead_rows(self):
        leads = _chain([])
        convs = _chain([{"tunnel": "support"}])
        client = _table_router({"leads": leads, "conversations": convs})
        with patch("app.db.supabase.get_client", return_value=client), \
             patch("app.db.supabase._run_sync", side_effect=_fake_run_sync):
            row = await ensure_lead(_CID)
        assert row is None
        leads.insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_or_create_lead_is_thin_wrapper(self):
        with patch.object(lead_service, "ensure_lead", new=AsyncMock(return_value={"id": "L1"})) as ens:
            row = await lead_service.get_or_create_lead(_CID)
        assert row == {"id": "L1"}
        ens.assert_awaited_once()

    def test_start_endpoint_ensures_lead(self):
        from app.api.chat import chat_start

        src = inspect.getsource(chat_start)
        assert "ensure_lead" in src

    @pytest.mark.asyncio
    async def test_db_error_reply_alive_and_counter_incremented(self):
        before = LEAD_WRITE_HEALTH["errors_since_boot"]
        with patch("app.db.supabase.get_client", side_effect=RuntimeError("db down")):
            row = await ensure_lead(_CID)          # must not raise
            await update_lead_from_entities(_CID, {"origin": "JFK"})  # must not raise
        assert row is None
        assert LEAD_WRITE_HEALTH["errors_since_boot"] >= before + 2
        assert LEAD_WRITE_HEALTH["last_error_at"] is not None

    @pytest.mark.asyncio
    async def test_health_surfaces_lead_writes(self):
        from app.api.health import health

        with patch("app.api.health.get_scheduler_health", return_value={}), \
             patch("app.api.health.supervisor_columns_status", return_value=True), \
             patch("app.api.health.settings") as s:
            s.debug = False
            payload = await health()
        assert "lead_writes" in payload
        assert "errors_since_boot" in payload["lead_writes"]


# ── 1h: legacy array-shaped intent_signals never raise ───────────────────

class TestLegacySignalsMerge:
    @pytest.mark.asyncio
    async def test_list_shaped_signals_merge_without_raise(self):
        lead_row = {
            "id": "L1", "score": 0, "origin_code": None, "destination_code": None,
            "departure_date": None, "passengers": None, "cabin_class": None,
            "return_date": None, "trip_type": None, "children_count": None,
            "infant_count": None, "intent_signals": ["legacy_marker"], "notes": "",
        }
        leads = _chain([lead_row])
        convs = _chain([])
        client = _table_router({"leads": leads, "conversations": convs})
        with patch("app.db.supabase.get_client", return_value=client), \
             patch("app.db.supabase._run_sync", side_effect=_fake_run_sync):
            await update_lead_from_entities(_CID, {"occasion": "honeymoon"})

        update_payloads = [c.args[0] for c in leads.update.call_args_list]
        signal_updates = [p for p in update_payloads if "intent_signals" in p]
        assert signal_updates, "signals merge must still write"
        merged = signal_updates[0]["intent_signals"]
        assert merged["occasion"] == "honeymoon"
        assert merged["legacy"] == ["legacy_marker"]  # legacy data preserved


# ── 1i + 1j: word-number passengers and multi-city survival ──────────────

class TestAliTranscript:
    def test_word_number_short_answer(self):
        assert extract_entities("one").passengers == 1
        assert extract_entities("Two").passengers == 2
        assert extract_entities("three travelers").passengers == 3

    def test_one_way_is_not_a_passenger_count(self):
        assert extract_entities("one way").passengers is None
        assert extract_entities("one-way ticket please").passengers is None

    def test_multi_city_chain_survives(self):
        e = extract_entities(
            "JFK to CMN on Nov 3, then CMN to CAI Nov 7, CAI to DEL, and DEL back to JFK"
        )
        assert e.trip_type == "multi_city"
        assert e.itinerary == "JFK→CMN→CAI→DEL→JFK"
        assert e.origin_code == "JFK"
        assert e.destination_code == "CMN"  # FIRST leg in the route fields
        assert e.return_date is None        # NEVER guessed from a middle leg
        assert e.departure_date is not None  # first leg date kept

    @pytest.mark.asyncio
    async def test_itinerary_leads_the_notes(self):
        lead_row = {
            "id": "L1", "score": 0, "origin_code": None, "destination_code": None,
            "departure_date": None, "passengers": None, "cabin_class": None,
            "return_date": None, "trip_type": None, "children_count": None,
            "infant_count": None, "intent_signals": {},
            "notes": "Chat: Type=needs_based",
        }
        leads = _chain([lead_row])
        convs = _chain([])
        client = _table_router({"leads": leads, "conversations": convs})
        with patch("app.db.supabase.get_client", return_value=client), \
             patch("app.db.supabase._run_sync", side_effect=_fake_run_sync):
            await update_lead_from_entities(
                _CID,
                {"origin": "JFK", "destination": "CMN",
                 "trip_type": "multi_city", "itinerary": "JFK→CMN→CAI→DEL→JFK"},
            )

        update_payloads = [c.args[0] for c in leads.update.call_args_list]
        notes_updates = [p for p in update_payloads if "notes" in p]
        assert notes_updates
        notes = notes_updates[0]["notes"]
        assert notes.splitlines()[0] == "Itinerary: JFK→CMN→CAI→DEL→JFK"
        assert "Chat: Type=needs_based" in notes  # persona line kept, after


# ── 1l: unified SLA ──────────────────────────────────────────────────────

class TestUnifiedSLA:
    def test_no_template_promises_2_hours(self):
        for key, variants in TEMPLATES.items():
            for text in variants:
                assert not re.search(r"\b2 hours\b", text), f"'2 hours' survives in {key}"

    def test_sla_constant_is_30_minutes(self):
        from app.ai.templates import CONSULTANT_SLA_TEXT

        assert CONSULTANT_SLA_TEXT == "~30 minutes"
        assert any(
            "~30 minutes" in t for t in TEMPLATES["specialist_handoff:sales"]
        )
        assert any("~30 minutes" in t for t in TEMPLATES["lead_captured:sales"])
