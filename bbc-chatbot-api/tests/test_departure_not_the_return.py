"""A date read from the client's message that is the lead's return does not become the departure.

#3830 (27 Nov 2026): the client wrote a range with the month at the end; the parser read only its last day
and gave it as the departure, the lead already held that day as the return, and the chat ended with the
trip leaving on the day it came back (22 → 22 Aug). The parser reads those ranges now
(test_entity_extractor.TestRangesWithTheMonthLast); this is the net behind it, for the shapes it still
misses. (Dates and phrasings below are invented.)
"""

import os
import sys
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.pipeline.entity_extractor import speaks_of_leaving
from app.services import lead_service
from app.services.lead_service import _departure_is_the_return

# Relative to today: a fixed day turns past one day, and the writer moves a past day to its next year.
_OUT_DAY, _BACK_DAY = date.today() + timedelta(days=60), date.today() + timedelta(days=75)
_OUT, _BACK = _OUT_DAY.isoformat(), _BACK_DAY.isoformat()
_BACK_WORDS = f"{_BACK_DAY:%B} {_BACK_DAY.day}"          # "August 22"
_LEAD = {"id": "L1", "departure_date": _OUT, "return_date": _BACK, "trip_type": "round_trip"}


class TestTheNet:
    def test_the_lead_s_return_read_as_the_departure_is_kept_off(self):
        assert _departure_is_the_return(_BACK, {"departure_date": _BACK, "_departure_said": False}, _LEAD)

    @pytest.mark.parametrize("entities", [
        {"departure_date": _BACK, "_departure_said": True},                            # "we'd rather leave on the 22nd"
        {"departure_date": _BACK},                                                     # a writer that does not say
        {"departure_date": _BACK, "_departure_said": False, "return_date": (_BACK_DAY + timedelta(days=14)).isoformat()},  # a new return with it
        {"departure_date": _BACK, "_departure_said": False, "_clear_return_date": True},
        {"departure_date": _BACK, "_departure_said": False, "trip_type": "one_way"},
    ])
    def test_a_message_that_moves_the_departure_still_moves_it(self, entities):
        assert not _departure_is_the_return(_BACK, entities, _LEAD)

    @pytest.mark.parametrize("lead", [
        {**_LEAD, "trip_type": "one_way"},
        {**_LEAD, "return_date": None},
        {**_LEAD, "departure_date": None},
        {**_LEAD, "departure_date": _BACK},          # the pair is already one day: nothing to keep
        {**_LEAD, "return_date": (_BACK_DAY + timedelta(days=8)).isoformat()},  # another day than the return
    ])
    def test_only_the_return_of_a_trip_that_has_one(self, lead):
        assert not _departure_is_the_return(_BACK, {"departure_date": _BACK, "_departure_said": False}, lead)

    def test_a_stored_timestamp_reads_as_its_day(self):
        lead = {**_LEAD, "departure_date": f"{_OUT}T00:00:00", "return_date": f"{_BACK}T00:00:00"}
        assert _departure_is_the_return(_BACK, {"departure_date": _BACK, "_departure_said": False}, lead)


@pytest.mark.parametrize("text,said", [
    ("we'd rather leave on the 22nd", True),
    ("departure on August 22 please", True),
    ("can we fly out August 22?", True),
    ("leaving August 22 then", True),
    ("August 22 works", False),
    ("the 22nd is fine for us", False),
    ("", False),
    (None, False),
])
def test_what_speaks_of_leaving(text, said):
    assert speaks_of_leaving(text) is said


def _chain(rows):
    t = MagicMock()
    for name in ("select", "eq", "limit", "update", "insert"):
        getattr(t, name).return_value = t
    t.execute.return_value = MagicMock(data=rows)
    return t


async def _run_now(fn, *args, **kwargs):
    return fn()


async def _write(entities: dict) -> list[dict]:
    """update_lead_from_entities over a lead that holds _OUT → _BACK; the lead payloads it wrote."""
    leads = _chain([dict(_LEAD, conversation_id="c-1", score=40)])
    convs = _chain([{}])
    client = MagicMock()
    client.table.side_effect = lambda name: {"leads": leads, "conversations": convs}[name]
    with patch("app.db.supabase.get_client", return_value=client), \
         patch("app.db.supabase._run_sync", side_effect=_run_now):
        assert await lead_service.update_lead_from_entities("c-1", entities) is True
    return [c.args[0] for c in leads.update.call_args_list]


@pytest.mark.asyncio
async def test_the_writer_keeps_the_departure_and_writes_the_rest():
    before = lead_service.LEAD_WRITE_HEALTH["departure_kept"]
    written = await _write({"departure_date": _BACK, "passengers": 2, "_departure_said": False})
    assert all("departure_date" not in p for p in written)
    assert any(p.get("passengers") == 2 for p in written)
    assert lead_service.LEAD_WRITE_HEALTH["departure_kept"] == before + 1


@pytest.mark.asyncio
async def test_the_writer_moves_the_departure_when_the_client_speaks_of_leaving():
    before = lead_service.LEAD_WRITE_HEALTH["departure_kept"]
    written = await _write({"departure_date": _BACK, "_departure_said": True})
    assert any(p.get("departure_date") == _BACK for p in written)
    assert lead_service.LEAD_WRITE_HEALTH["departure_kept"] == before


class _StopAfterMerge(Exception):
    """Raised by the patched validate_response: the pipeline reached STEP 7."""


async def _pipeline_writes(message: str, tool_entities) -> list[dict]:
    """The entities the pipeline hands the lead writer, up to STEP 6.1 (the parser's, then the tool's)."""
    from contextlib import ExitStack

    from app import flags
    from app.models.chat import VisitorInfo
    from app.pipeline.generator import GeneratedResponse
    from app.pipeline.orchestrator import _pipeline

    cid = "aaaaaaaa-bbbb-cccc-dddd-000000003830"
    conv = {"id": cid, "mode": "ai", "status": "active", "tunnel": "sales", "metadata": {}}
    update = AsyncMock(return_value=True)
    flags._reset_for_tests()
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
            return_value=GeneratedResponse(text="Noted!", model_used="sonnet", tool_entities=tool_entities),
        ))
        with pytest.raises(_StopAfterMerge):
            await _pipeline(cid, message, "sales", VisitorInfo(name="Client"), None,
                            _persist_state={"ai_persisted": False})
    flags._reset_for_tests()
    return [dict(c.args[1]) for c in update.await_args_list]


@pytest.mark.asyncio
async def test_the_pipeline_tells_the_writer_whether_the_message_spoke_of_leaving():
    # The parser's reading and the tool's: the tool read the 22nd alone as the departure too.
    parsed, merged = await _pipeline_writes(f"{_BACK_WORDS} works", {"departure_date": _BACK})
    assert parsed["departure_date"] == _BACK and parsed["_departure_said"] is False
    assert merged["departure_date"] == _BACK and merged["_departure_said"] is False
    (said,) = await _pipeline_writes(f"we leave {_BACK_WORDS}", None)
    assert said["_departure_said"] is True


@pytest.mark.asyncio
async def test_said_to_an_agent_the_reading_says_whether_it_spoke_of_leaving(monkeypatch):
    """The operator card reads the client's messages to an agent with the parser alone: it says it too, and only
    when a departure was read (a message with nothing in it writes nothing, as before)."""
    from app.pipeline import operator_card

    monkeypatch.delenv("EXTRACT_EVERY_MESSAGE", raising=False)
    upd = AsyncMock()
    with patch("app.services.lead_service.update_lead_from_entities", upd):
        await operator_card.extract_and_push("conv-1", "August 22 works for us")
        await operator_card.extract_and_push("conv-1", "we leave August 22")
        await operator_card.extract_and_push("conv-1", "we are 3 people")
        await operator_card.extract_and_push("conv-1", "thanks")
    works, leave, people = (c.args[1] for c in upd.await_args_list)
    assert works["_departure_said"] is False and works["departure_date"].endswith("-08-22")
    assert leave["_departure_said"] is True
    assert "_departure_said" not in people
    assert upd.await_count == 3
