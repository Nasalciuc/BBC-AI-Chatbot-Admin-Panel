"""What the client needs on the trip reaches the lead at any turn, and every need said is kept.

23 Nov 2026: a need reached the lead only in the one turn after the summary (the open door). #3579
asked for trip insurance and named a loyalty account mid-chat; the lead kept neither. Now the needs a
message names are kept as labels — a seat, help at the airport, a lie-flat bed, TSA PreCheck / KTN, a
loyalty account or miles, travel insurance — and they add up instead of the last one replacing the
rest. Never the number a client types (a KTN or a loyalty number), never a reason or a condition.
Phrasings are invented.
"""

import os
import re
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.models.chat import VisitorInfo
from app.pipeline.entity_extractor import extract_entities, merge_must_haves, needs_of
from app.pipeline.generator import GeneratedResponse
from app.pipeline.orchestrator import _pipeline


@pytest.fixture(autouse=True)
def _flags_clean():
    flags._reset_for_tests()
    yield
    flags._reset_for_tests()


# ── needs_of ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,labels", [
    ("Only one (me), and yes I would like trip insurance", "travel insurance"),
    ("could I get an aisle seat please", "aisle seat"),
    ("window seat for my daughter", "window seat"),
    ("business with lie-flat seats only", "lie-flat seat"),
    ("my mother will need a wheelchair at the airport", "wheelchair / airport assistance"),
    ("we need special assistance in Frankfurt", "wheelchair / airport assistance"),
    ("I have TSA PreCheck", "TSA PreCheck / KTN"),
    ("my known traveler number is 98765432", "TSA PreCheck / KTN"),
    ("I have a frequent flyer number with two airlines", "loyalty number / miles"),
    ("i also have a number for points with a few airlines", "loyalty number / miles"),
    ("can we pay with miles?", "loyalty number / miles"),
    ("aisle seat, travel insurance and my KTN", "aisle seat; TSA PreCheck / KTN; travel insurance"),
    # A need said away only within its clause (CodeRabbit #279).
    ("No window seat but aisle seat please", "aisle seat"),
    ("no insurance needed, however a wheelchair at the airport", "wheelchair / airport assistance"),
])
def test_the_needs_a_message_names(text, labels):
    assert needs_of(text) == labels


@pytest.mark.parametrize("text", [
    "no travel insurance, thanks", "I don't need a wheelchair", "we don't want trip insurance",
    "I will", "sounds good", "what are the TSA liquid rules?", "a window of a few days either side",
    "after we walk down the aisle", "do you sell insurance?", "", None,
])
def test_these_name_no_need(text):
    assert needs_of(text) is None


def test_a_number_the_client_types_is_never_kept():
    labels = needs_of("my KTN is TT1234567 and my SkyMiles number is 0012345678")
    assert labels == "TSA PreCheck / KTN; loyalty number / miles"
    assert not re.search(r"\d", labels)
    assert extract_entities("my KTN is TT1234567").needs == "TSA PreCheck / KTN"


# ── merge_must_haves ─────────────────────────────────────────────────────────

def test_needs_add_up_and_a_need_said_twice_is_kept_once():
    assert merge_must_haves("aisle seat", "travel insurance") == "aisle seat; travel insurance"
    assert merge_must_haves("aisle seat; travel insurance", "aisle seat") == "aisle seat; travel insurance"
    assert merge_must_haves(None, "aisle seat") == "aisle seat"
    assert merge_must_haves("aisle seat", None) == "aisle seat"


def test_a_label_the_clients_words_already_hold_is_not_added():
    assert merge_must_haves("I need an aisle seat please", "aisle seat") == "I need an aisle seat please"


def test_never_past_the_limit_and_never_a_cut_part():
    stored = "x" * 190
    assert merge_must_haves(stored, "travel insurance") == stored
    assert merge_must_haves("a; b", "c", limit=7) == "a; b; c"
    assert merge_must_haves("a; b", "cd", limit=7) == "a; b"


def test_a_stored_value_that_is_not_text_is_left_behind():
    assert merge_must_haves(["legacy"], "aisle seat") == "aisle seat"


# ── the lead write ───────────────────────────────────────────────────────────

_CID = "aaaaaaaa-bbbb-cccc-dddd-000000000911"


async def _write(existing_signals: dict, entities: dict) -> dict:
    """update_lead_from_entities against a fake table: the lead payload it writes."""
    from app.services import lead_service

    client = MagicMock()
    sel = client.table.return_value.select.return_value.eq.return_value.limit.return_value
    sel.execute.return_value = MagicMock(data=[{"id": "lead-n", "intent_signals": existing_signals, "notes": ""}])

    async def _run(fn, **kw):
        return fn()

    with patch("app.db.supabase.get_client", return_value=client), \
         patch("app.db.supabase._run_sync", new=_run), \
         patch.object(lead_service, "_stored_contacts", new=AsyncMock(return_value={})):
        await lead_service.update_lead_from_entities(_CID, entities)
    payloads = [c.args[0] for c in client.table.return_value.update.call_args_list]
    return next((p for p in payloads if "intent_signals" in p), {})


@pytest.mark.asyncio
async def test_a_second_need_is_added_to_the_first():
    lead = await _write({"must_haves": "aisle seat"}, {"must_haves": "travel insurance"})
    assert lead["intent_signals"]["must_haves"] == "aisle seat; travel insurance"
    assert "Must-have=aisle seat; travel insurance" in lead["notes"]


@pytest.mark.asyncio
async def test_a_turn_without_a_need_leaves_the_needs_as_they_are():
    lead = await _write({"must_haves": "aisle seat"}, {"occasion": "anniversary"})
    assert lead["intent_signals"]["must_haves"] == "aisle seat"


# ── the turn ─────────────────────────────────────────────────────────────────

_CONV = {"id": _CID, "mode": "ai", "status": "active", "tunnel": "sales", "metadata": {}}
_LEAD = {"id": "lead-n", "conversation_id": _CID, "origin_code": "JFK", "destination_code": "LHR",
         "trip_type": "round_trip", "notes": ""}


class _StopAfterMerge(Exception):
    """Raised by the patched validate_response: the pipeline reached STEP 7."""


async def _turn_entities(message: str, meta: dict | None = None) -> dict:
    """The entities STEP 5 hands the lead for this client message ({} when nothing)."""
    from contextlib import ExitStack

    update = AsyncMock()
    conv = {**_CONV, "metadata": dict(meta or {})}
    with ExitStack() as stack:
        for target, value in (
            ("app.services.conversation_service.get_or_create_conversation", AsyncMock(return_value=conv)),
            ("app.services.conversation_service.add_message", AsyncMock(return_value={"id": "m1"})),
            ("app.pipeline.orchestrator.db.get_recent_messages", AsyncMock(return_value=[])),
            ("app.services.moderation.moderate_message", AsyncMock()),
            ("app.pipeline.orchestrator.db.update_conversation", AsyncMock()),
            ("app.pipeline.orchestrator.db.update_lead", AsyncMock()),
            ("app.pipeline.orchestrator.lead_service.get_or_create_lead", AsyncMock(return_value=dict(_LEAD))),
            ("app.pipeline.orchestrator.lead_service.update_lead_from_entities", update),
        ):
            stack.enter_context(patch(target, new=value))
        stack.enter_context(patch("app.pipeline.intent.claude_classify", return_value=(None, 0.0)))
        stack.enter_context(patch("app.pipeline.orchestrator.validate_response",
                                  side_effect=_StopAfterMerge("stop after the merge")))
        stack.enter_context(patch(
            "app.pipeline.orchestrator.generate_response",
            return_value=GeneratedResponse(text="Noted!", model_used="sonnet", tool_entities=None),
        ))
        with pytest.raises(_StopAfterMerge):
            await _pipeline(_CID, message, "sales", VisitorInfo(name="Client"), None,
                            _persist_state={"ai_persisted": False})
    calls = [c.args[1] for c in update.await_args_list]
    return calls[0] if calls else {}


@pytest.mark.asyncio
async def test_a_need_said_mid_chat_reaches_the_lead():
    ents = await _turn_entities("Only one (me), and yes I would like trip insurance")
    assert ents.get("must_haves") == "travel insurance"


@pytest.mark.asyncio
async def test_a_message_without_a_need_carries_none():
    ents = await _turn_entities("sounds good, talk soon")
    assert not ents.get("must_haves")


@pytest.mark.asyncio
async def test_the_open_door_answer_is_still_the_clients_own_words():
    ents = await _turn_entities("a quiet seat away from the galley", meta={"open_door_pending": True})
    assert ents.get("must_haves") == "a quiet seat away from the galley"


# ── a chat with an agent ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_agent_chat_keeps_a_zero_count_and_sends_no_count_it_did_not_read(monkeypatch):
    """"2 adults and a baby": no child, said; a 0 reaches the lead and clears an older child count. "2 adults"
    names no minor: nothing about minors is sent (CodeRabbit #279)."""
    from app.pipeline import operator_card

    monkeypatch.delenv("EXTRACT_EVERY_MESSAGE", raising=False)
    upd = AsyncMock()
    with patch("app.services.lead_service.update_lead_from_entities", upd):
        await operator_card.extract_and_push("conv-1", "2 adults and a baby")
        await operator_card.extract_and_push("conv-1", "2 adults")
    with_baby, adults_only = (c.args[1] for c in upd.await_args_list)
    assert (with_baby["_children_count"], with_baby["_infant_count"], with_baby["passengers"]) == (0, 1, 3)
    assert "_children_count" not in adults_only and "_infant_count" not in adults_only


@pytest.mark.asyncio
async def test_a_need_said_to_an_agent_reaches_the_lead(monkeypatch):
    from app.pipeline import operator_card

    monkeypatch.delenv("EXTRACT_EVERY_MESSAGE", raising=False)
    upd = AsyncMock()
    with patch("app.services.lead_service.update_lead_from_entities", upd):
        await operator_card.extract_and_push("conv-1", "aisle seat please, my husband likes the window")
    assert upd.await_args.args[1] == {"must_haves": "aisle seat; window seat"}
