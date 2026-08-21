"""Closed orphans + the CRM contract (127-orphan census).

Two silent excluders kept dialable leads out of the CRM forever:
  status='active'  → a CLOSED conversation with contact inside the 30-day
                     window is exactly the dialable lead the business rule
                     protects (Diana class).
  mode='ai'        → any conversation a human ever touched was skipped
                     forever (Paulette class: human took over, the push
                     was orphaned, nothing retried).
Plus: the CRM's contract (createAiChat) is now enforced BEFORE the call,
the AAA defaults carry honest notes, past departures go to the work-list
instead of being dialed, and the backfill dedups by contact so one human
never becomes two CRM rows.
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _fresh_day_guard():
    """The duplicate-push day guard (fix/crm-duplicate-pushes) is per-process
    module state, exactly like production. These tests reuse the same
    conversation ids across cases, so each case starts with a clean day —
    otherwise the second sweep in the file is "blocked" by the first one's
    successful push, which is the guard working, not the sweep breaking."""
    from app.api import cron as _cron
    _cron._pushed_today = set()
    _cron._pushed_today_date = ""
    yield
    _cron._pushed_today = set()
    _cron._pushed_today_date = ""

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.services import crm
from app.services.crm import (
    CRM_PUSH_HEALTH,
    CRMResult,
    build_crm_payload,
    compute_defaults_notes,
    format_phone_international,
    submit_abandoned_to_crm,
    validate_crm_payload,
)

APP = os.path.join(os.path.dirname(__file__), "..", "app")

_FUTURE = (date.today() + timedelta(days=45)).isoformat()
_PAST = (date.today() - timedelta(days=5)).isoformat()

BASE_CONV = {
    "id": "conv-1",
    "visitor_name": "Diana",
    "visitor_email": "diana@gmail.com",
    "visitor_phone": "+1 (210) 884-4081",
    "metadata": {"utm_source": "google", "page_url": "https://buybusinessclass.com/lax"},
    "visitor_id": "v-1",
    "status": "closed",
    "mode": "ai",
    "message_count": 4,
    "created_at": "2026-08-01T10:00:00+00:00",
}


def _read(*parts: str) -> str:
    with open(os.path.join(APP, *parts), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(autouse=True)
def _reset():
    for k in ("ok", "failed", "refused"):
        CRM_PUSH_HEALTH[k] = 0
    yield


def _crm_ok(crm_id="R-1"):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": {"id": crm_id}}
    resp.text = ""
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    return client


async def _push(conv, lead):
    client = _crm_ok()
    with patch.object(crm, "_get_crm_client", return_value=client), \
         patch.object(crm.settings, "crm_api_url", "https://crm.test"):
        res = await submit_abandoned_to_crm(dict(conv), dict(lead))
    return res, client


# ════════════════════════════════════════════════════════════
# (a) The select: closed + human are dialable too
# ════════════════════════════════════════════════════════════

class TestWidenedSelect:
    def test_status_and_mode_are_wide(self):
        src = _read("db", "supabase.py")
        assert '.in_("status", ["active", "closed"])' in src
        assert '.in_("mode", ["ai", "human"])' in src
        # 'completed' is post-CRM — it must NOT be in the list.
        assert '"completed"' not in src.split("get_abandoned_conversations")[1][:1200]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,mode,label", [
        ("closed", "ai", "Diana"),
        ("active", "human", "Paulette"),
        ("closed", "human", "closed+human"),
    ])
    async def test_census_classes_are_selected(self, status, mode, label):
        from app.db import supabase as sb

        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        conv = dict(BASE_CONV, status=status, mode=mode, created_at=old)
        calls = {"n": 0}

        async def fake_run_sync(fn, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return SimpleNamespace(data=[dict(conv)])
            return SimpleNamespace(
                data=[{"created_at": old, "role": "user"}]
            )

        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", fake_run_sync):
            out = await sb.get_abandoned_conversations(30)

        assert len(out) == 1, f"{label} must be swept"

    @pytest.mark.asyncio
    async def test_closed_and_already_in_crm_is_left_alone(self):
        """The close-only path must not post a closing into a finished chat."""
        from app.api import cron

        with patch.object(cron.db, "get_abandoned_conversations",
                          new=AsyncMock(return_value=[dict(BASE_CONV)])), \
             patch.object(cron, "get_or_create_lead",
                          new=AsyncMock(return_value={"id": "L1", "created_in_crm": True})), \
             patch.object(cron.db, "add_message", new=AsyncMock()) as add_msg, \
             patch.object(cron.db, "update_conversation", new=AsyncMock()) as upd:
            cron._AAA_BACKFILL_RAN = True
            out = await cron.run_abandoned_crm()

        assert out["results"][0]["status"] == "already_done"
        add_msg.assert_not_awaited()
        upd.assert_not_awaited()


# ════════════════════════════════════════════════════════════
# (b) The contract — refuse what the CRM would 422
# ════════════════════════════════════════════════════════════

class TestContract:
    def _payload(self, **over):
        p = {
            "client": {"name": "Diana", "email": "d@gmail.com", "phone": "+12108844081"},
            "flights": [{"from": "LAX", "to": "SYD", "date": _FUTURE}],
            "passengers": {"adult": 1, "child": 0, "infant": 0},
            "cabin_class": "business",
            "trip_type": "one_way",
        }
        p.update(over)
        return p

    def test_valid_payload_passes(self):
        assert validate_crm_payload(self._payload()) is None

    @pytest.mark.parametrize("over,expected", [
        ({"client": {"name": "Diana", "phone": "(210) 884-4081"}}, "phone_format"),
        ({"client": {"name": "D", "phone": "+12108844081"}}, "name_too_short"),
        ({"flights": [{"from": "LAXX", "to": "SYD", "date": _FUTURE}]}, "iata_from"),
        ({"flights": [{"from": "LAX", "to": "S", "date": _FUTURE}]}, "iata_to"),
        ({"flights": [{"from": "LAX", "to": "SYD", "date": "next week"}]}, "date_format"),
        ({"flights": []}, "no_flights"),
        ({"passengers": {"adult": 0}}, "adult_range"),
        ({"passengers": {"adult": 12}}, "adult_range"),
        ({"cabin_class": "economy"}, "cabin_enum"),
    ])
    def test_contract_refusals(self, over, expected):
        assert validate_crm_payload(self._payload(**over)) == expected

    def test_spaced_phone_normalizes_into_contract_shape(self):
        formatted = format_phone_international("+1 (210) 884-4081")
        assert formatted == "+12108844081"
        assert validate_crm_payload(
            self._payload(client={"name": "Diana", "phone": formatted})
        ) is None

    def test_422_body_logged_verbatim(self):
        src = _read("services", "crm.py")
        assert "body(verbatim)={resp.text}" in src
        assert "conv={cid}" in src


# ════════════════════════════════════════════════════════════
# (c) AAA defaults — each census class, with honest notes
# ════════════════════════════════════════════════════════════

class TestDefaultsPerCensusClass:
    @pytest.mark.asyncio
    async def test_route_less_lead(self):
        res, client = await _push(BASE_CONV, {"id": "L1", "departure_date": _FUTURE})
        assert res.success
        payload = client.post.call_args.kwargs["json"]
        assert payload["flights"][0]["from"] == "AAA"
        assert "route unknown — defaulted to AAA" in payload["chat_context"]

    @pytest.mark.asyncio
    async def test_date_less_lead(self):
        res, client = await _push(
            BASE_CONV, {"id": "L1", "origin_code": "LAX", "destination_code": "SYD"}
        )
        assert res.success
        payload = client.post.call_args.kwargs["json"]
        assert "date unknown — defaulted to +30 days" in payload["chat_context"]
        assert validate_crm_payload(payload) is None

    @pytest.mark.asyncio
    async def test_round_trip_without_return_becomes_one_way_with_note(self):
        res, client = await _push(BASE_CONV, {
            "id": "L1", "origin_code": "LAX", "destination_code": "SYD",
            "departure_date": _FUTURE, "trip_type": "round_trip",
        })
        assert res.success
        payload = client.post.call_args.kwargs["json"]
        assert payload["trip_type"] == "one_way"
        assert len(payload["flights"]) == 1
        assert "client said round trip, return unknown" in payload["chat_context"]

    @pytest.mark.asyncio
    async def test_past_date_goes_to_the_work_list_never_pushed(self):
        client = _crm_ok()
        with patch.object(crm, "_get_crm_client", return_value=client), \
             patch.object(crm.settings, "crm_api_url", "https://crm.test"):
            res = await submit_abandoned_to_crm(
                dict(BASE_CONV), {"id": "L1", "departure_date": _PAST}
            )
        assert not res.success and res.error == "gate:past_date"
        client.post.assert_not_called()
        assert CRM_PUSH_HEALTH["refused"] == 1

    @pytest.mark.asyncio
    async def test_cabin_outside_enum_downgrades_with_note(self):
        res, client = await _push(BASE_CONV, {
            "id": "L1", "origin_code": "LAX", "destination_code": "SYD",
            "departure_date": _FUTURE, "cabin_class": "economy",
        })
        assert res.success
        payload = client.post.call_args.kwargs["json"]
        assert payload["cabin_class"] == "premium_economy"
        assert "outside CRM enum" in payload["chat_context"]

    def test_notes_always_lead_with_source_and_landing(self):
        notes = compute_defaults_notes({}, {"utm_source": "google", "page_url": "/lax"})
        assert notes[0] == "form only / data incomplete — source: google, landing: /lax"

    def test_strict_path_cabin_default_unchanged(self):
        """Only the defaults path downgrades — the confirm-flow keeps the
        historical business default (no silent behavior change)."""
        payload = build_crm_payload(
            {"cabin_class": "economy", "origin_code": "LAX",
             "destination_code": "SYD", "departure_date": _FUTURE, "passengers": 1},
            SimpleNamespace(name="D", email="d@g.com", phone="+12108844081"),
        )
        assert payload["cabin_class"] == "business"


# ════════════════════════════════════════════════════════════
# (d) Backfill — dedup by contact, idempotent
# ════════════════════════════════════════════════════════════

class TestBackfill:
    def test_richest_row_wins_and_twin_is_noted(self):
        from app.api.cron import pick_richest_by_contact

        thin = dict(BASE_CONV, id="thin", message_count=0, visitor_name="")
        rich = dict(BASE_CONV, id="rich", message_count=9)
        out = pick_richest_by_contact([thin, rich])
        assert len(out) == 1
        assert out[0]["id"] == "rich"
        assert out[0]["_twin_count"] == 1

    def test_different_contacts_are_never_merged(self):
        from app.api.cron import pick_richest_by_contact

        a = dict(BASE_CONV, id="a")
        b = dict(BASE_CONV, id="b", visitor_email="other@gmail.com",
                 visitor_phone="+13105550111")
        assert len(pick_richest_by_contact([a, b])) == 2

    @pytest.mark.asyncio
    async def test_twin_note_reaches_the_payload(self):
        conv = dict(BASE_CONV, _twin_count=2)
        res, client = await _push(conv, {
            "id": "L1", "origin_code": "LAX", "destination_code": "SYD",
            "departure_date": _FUTURE,
        })
        assert res.success
        assert "2 duplicate contact conversation(s)" in client.post.call_args.kwargs["json"]["chat_context"]

    @pytest.mark.asyncio
    async def test_refusals_land_in_the_work_list_with_a_reason(self):
        from app.api import cron
        from app.db import supabase as sb

        with patch.object(sb, "get_recent_contact_conversations",
                          new=AsyncMock(return_value=[dict(BASE_CONV)])), \
             patch.object(cron, "get_or_create_lead",
                          new=AsyncMock(return_value={"id": "L1", "created_in_crm": False})), \
             patch.object(sb, "update_lead_crm_push_state", new=AsyncMock()) as state, \
             patch.object(cron, "submit_abandoned_to_crm",
                          new=AsyncMock(return_value=CRMResult(success=False, error="gate:past_date"))):
            out = await cron.run_aaa_backfill()

        assert out["work_list"] == 1 and out["pushed"] == 0
        state.assert_awaited_once_with("L1", gate_reason="past_date")

    @pytest.mark.asyncio
    async def test_second_run_pushes_nothing(self):
        from app.api import cron
        from app.db import supabase as sb

        with patch.object(sb, "get_recent_contact_conversations",
                          new=AsyncMock(return_value=[dict(BASE_CONV)])), \
             patch.object(cron, "get_or_create_lead",
                          new=AsyncMock(return_value={"id": "L1", "created_in_crm": True})), \
             patch.object(cron, "submit_abandoned_to_crm", new=AsyncMock()) as push:
            out = await cron.run_aaa_backfill()

        assert out["pushed"] == 0 and out["skipped"] == 1
        push.assert_not_awaited()

    def test_window_is_thirty_days_and_closed_included(self):
        src = _read("db", "supabase.py")
        body = src[src.index("def get_recent_contact_conversations"):][:1400]
        assert "days: int = 30" in body
        assert '.in_("status", ["active", "closed"])' in body

    def test_summary_line_has_three_counters(self):
        src = _read("api", "cron.py")
        assert (
            'f"AAA backfill: {pushed} pushed, {work_list} work-list, {skipped} skipped"'
            in src
        )


# ════════════════════════════════════════════════════════════
# Width feeds the PUSH only — never the close/unassign powers.
# (adversarial-review hardening: a widened select must not let the
#  cron seize a chat a human is actively working)
# ════════════════════════════════════════════════════════════

class TestWidthGrantsNoDestructivePowers:
    async def _run_cron(self, conv, lead, crm_result=None):
        from app.api import cron

        cron._AAA_BACKFILL_RAN = True
        with patch.object(cron.db, "get_abandoned_conversations",
                          new=AsyncMock(return_value=[dict(conv)])), \
             patch.object(cron, "get_or_create_lead", new=AsyncMock(return_value=dict(lead))), \
             patch.object(cron.db, "ensure_lead_for_conversation",
                          new=AsyncMock(return_value=dict(lead))), \
             patch.object(cron.db, "mark_lead_created_in_crm",
                          new=AsyncMock(return_value={"id": lead["id"]})) as mark, \
             patch.object(cron.db, "update_lead_crm_push_state", new=AsyncMock()) as state, \
             patch.object(cron.db, "add_message", new=AsyncMock()) as add_msg, \
             patch.object(cron.db, "update_conversation", new=AsyncMock()) as upd, \
             patch.object(cron, "submit_abandoned_to_crm",
                          new=AsyncMock(return_value=crm_result or CRMResult(success=True, request_id="R-9"))) as push:
            out = await cron.run_abandoned_crm()
        return out, {"push": push, "mark": mark, "upd": upd, "add_msg": add_msg, "state": state}

    @pytest.mark.asyncio
    async def test_live_human_chat_pushed_but_never_closed_or_unassigned(self):
        """Paulette class: the human owns the chat. Rescuing the orphaned
        CRM push is the point of widening mode — the closing message, the
        force close and the unassign are NOT."""
        conv = dict(BASE_CONV, status="active", mode="human")
        out, m = await self._run_cron(conv, {"id": "L1", "created_in_crm": False})

        m["push"].assert_awaited_once()
        m["mark"].assert_awaited_once()
        m["upd"].assert_not_awaited()
        m["add_msg"].assert_not_awaited()
        assert out["results"][0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_closed_conv_pushed_without_rewriting_its_closure(self):
        conv = dict(BASE_CONV, status="closed", mode="ai")
        out, m = await self._run_cron(conv, {"id": "L1", "created_in_crm": False})

        m["push"].assert_awaited_once()
        m["upd"].assert_not_awaited()   # closed_at / agent assignment preserved
        assert out["results"][0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_human_owned_and_already_in_crm_is_left_alone(self):
        conv = dict(BASE_CONV, status="active", mode="human")
        out, m = await self._run_cron(conv, {"id": "L1", "created_in_crm": True})

        m["push"].assert_not_awaited()
        m["upd"].assert_not_awaited()
        m["add_msg"].assert_not_awaited()
        assert out["results"][0]["status"] == "already_done"

    @pytest.mark.asyncio
    async def test_flag_write_failure_stops_the_row_instead_of_looping(self):
        """CRM accepted but the flag write died: closing hides it, leaving
        it open re-pushes a duplicate every tick. Shout and stop."""
        from app.api import cron

        conv = dict(BASE_CONV, status="active", mode="ai")
        cron._AAA_BACKFILL_RAN = True
        with patch.object(cron.db, "get_abandoned_conversations",
                          new=AsyncMock(return_value=[dict(conv)])), \
             patch.object(cron, "get_or_create_lead",
                          new=AsyncMock(return_value={"id": "L1", "created_in_crm": False})), \
             patch.object(cron.db, "mark_lead_created_in_crm",
                          new=AsyncMock(return_value=None)), \
             patch.object(cron.db, "update_conversation", new=AsyncMock()) as upd, \
             patch.object(cron, "submit_abandoned_to_crm",
                          new=AsyncMock(return_value=CRMResult(success=True, request_id="R-9"))):
            out = await cron.run_abandoned_crm()

        assert out["results"][0]["status"] == "flag_write_failed"
        assert out["results"][0]["crm_id"] == "R-9"
        upd.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_live_cron_dedups_twins_too(self):
        """Otherwise the backfill dedup is undone minutes later."""
        from app.api import cron

        thin = dict(BASE_CONV, id="thin", message_count=0)
        rich = dict(BASE_CONV, id="rich", message_count=8)
        cron._AAA_BACKFILL_RAN = True
        with patch.object(cron.db, "get_abandoned_conversations",
                          new=AsyncMock(return_value=[thin, rich])), \
             patch.object(cron, "get_or_create_lead",
                          new=AsyncMock(side_effect=lambda cid: {"id": f"L-{cid}", "created_in_crm": False})), \
             patch.object(cron.db, "mark_lead_created_in_crm",
                          new=AsyncMock(return_value={"id": "L-rich"})), \
             patch.object(cron.db, "update_lead_crm_push_state", new=AsyncMock()) as state, \
             patch.object(cron.db, "update_conversation", new=AsyncMock()), \
             patch.object(cron, "submit_abandoned_to_crm",
                          new=AsyncMock(return_value=CRMResult(success=True, request_id="R-9"))) as push:
            out = await cron.run_abandoned_crm()

        assert push.await_count == 1        # one human, one CRM row
        statuses = {r["id"]: r["status"] for r in out["results"]}
        assert statuses["thin"] == "dedup_twin"
        state.assert_awaited_once_with("L-thin", gate_reason="duplicate_contact")

    @pytest.mark.asyncio
    async def test_transient_failure_retryable_gate_refusal_persisted(self):
        from app.api import cron

        conv = dict(BASE_CONV, status="active", mode="ai")

        async def _run(error):
            cron._AAA_BACKFILL_RAN = True
            with patch.object(cron.db, "get_abandoned_conversations",
                              new=AsyncMock(return_value=[dict(conv)])), \
                 patch.object(cron, "get_or_create_lead",
                              new=AsyncMock(return_value={"id": "L1", "created_in_crm": False})), \
                 patch.object(cron.db, "update_lead_crm_push_state", new=AsyncMock()) as state, \
                 patch.object(cron, "submit_abandoned_to_crm",
                              new=AsyncMock(return_value=CRMResult(success=False, error=error))):
                await cron.run_abandoned_crm()
            return state

        assert (await _run("HTTP 500: upstream")).await_count == 0
        assert (await _run("gate:past_date")).await_count == 1


class TestDedupIdentity:
    def test_same_human_missing_email_on_one_row_is_one_group(self):
        from app.api.cron import pick_richest_by_contact

        full = dict(BASE_CONV, id="full", message_count=5)
        phone_only = dict(BASE_CONV, id="phone_only", visitor_email="", message_count=1)
        out = pick_richest_by_contact([full, phone_only])
        assert len(out) == 1 and out[0]["id"] == "full"

    def test_shared_email_different_phone_still_merges(self):
        from app.api.cron import pick_richest_by_contact

        a = dict(BASE_CONV, id="a", visitor_phone="+13105550111", message_count=2)
        b = dict(BASE_CONV, id="b", message_count=7)
        assert len(pick_richest_by_contact([a, b])) == 1


class TestNoteHonesty:
    def test_cabin_translation_alone_never_claims_incomplete(self):
        notes = compute_defaults_notes(
            {"origin_code": "LAX", "destination_code": "SYD",
             "departure_date": _FUTURE, "cabin_class": "economy"},
            {},
        )
        assert notes and all("data incomplete" not in n for n in notes)
        assert any("outside CRM enum" in n for n in notes)

    @pytest.mark.asyncio
    async def test_silent_lead_prints_source_once(self):
        conv = dict(BASE_CONV, _no_engagement=True)
        _res, client = await _push(conv, {"id": "L1"})
        note = client.post.call_args.kwargs["json"]["chat_context"]
        assert note.count("source:") == 1
        assert note.startswith("No engagement — form only")

    @pytest.mark.asyncio
    async def test_one_char_name_is_dialable_not_refused(self):
        conv = dict(BASE_CONV, visitor_name="J")
        res, client = await _push(conv, {
            "id": "L1", "origin_code": "LAX", "destination_code": "SYD",
            "departure_date": _FUTURE,
        })
        assert res.success
        assert client.post.call_args.kwargs["json"]["client"]["name"] == "Customer"

    def test_scan_caps_are_never_silent(self):
        src = _read("db", "supabase.py")
        assert "hit the {_ABANDONED_SCAN_CAP}-row" in src
        assert "hit the 500-row cap" in src
