"""The dates a client gave stay on the lead until the client speaks of a date again.

#3579 (23 Nov 2026): the client had written a January departure and a March return, and the lead
held them; on the last turn ("I will") the AI's extraction answered other dates and the merge took them:
23 Nov → 30 Nov 2026, minutes before the abandoned-lead job would have sent them to the CRM. #3403
(20 Nov): a week in February became 15 → 30 Dec on "sounds good". Passengers and cabin were already
kept from the tool's overwrite; the dates were not. (Route, dates and phrasings below are invented.)
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.models.chat import VisitorInfo
from app.pipeline.entity_extractor import mentions_date
from app.pipeline.generator import GeneratedResponse
from app.pipeline.orchestrator import _pipeline

_CID = "aaaaaaaa-bbbb-cccc-dddd-000000000357"
_CONV = {"id": _CID, "mode": "ai", "status": "active", "tunnel": "sales", "metadata": {}}
_OUT, _BACK = "2027-01-12", "2027-03-10"
_LEAD = {"id": "lead-dates", "conversation_id": _CID, "origin_code": "JFK", "destination_code": "LHR",
         "trip_type": "round_trip", "departure_date": _OUT, "return_date": _BACK, "notes": ""}
_WRONG = {"origin": "JFK", "destination": "LHR", "departure_date": "2026-11-23", "return_date": "2026-11-30",
          "trip_type": "round_trip"}


@pytest.fixture(autouse=True)
def _flags_clean():
    flags._reset_for_tests()
    yield
    flags._reset_for_tests()


class _StopAfterMerge(Exception):
    """Raised by the patched validate_response: the pipeline reached STEP 7."""


async def _merged(message: str, tool_entities: dict, lead: dict) -> dict:
    """Run the pipeline up to STEP 6.1 and return what the merge hands the lead ({} when nothing)."""
    from contextlib import ExitStack

    update = AsyncMock()
    with ExitStack() as stack:
        for target, value in (
            ("app.services.conversation_service.get_or_create_conversation", AsyncMock(return_value=dict(_CONV))),
            ("app.services.conversation_service.add_message", AsyncMock(return_value={"id": "m1"})),
            ("app.pipeline.orchestrator.db.get_recent_messages", AsyncMock(return_value=[])),
            ("app.services.moderation.moderate_message", AsyncMock()),
            ("app.pipeline.orchestrator.db.update_conversation", AsyncMock()),
            ("app.pipeline.orchestrator.db.update_lead", AsyncMock()),
            ("app.pipeline.orchestrator.lead_service.get_or_create_lead", AsyncMock(return_value=dict(lead))),
            ("app.pipeline.orchestrator.lead_service.update_lead_from_entities", update),
        ):
            stack.enter_context(patch(target, new=value))
        stack.enter_context(patch("app.pipeline.intent.claude_classify", return_value=(None, 0.0)))
        stack.enter_context(patch("app.pipeline.orchestrator.validate_response",
                                  side_effect=_StopAfterMerge("stop after the merge")))
        stack.enter_context(patch(
            "app.pipeline.orchestrator.generate_response",
            return_value=GeneratedResponse(text="Speak soon!", model_used="sonnet", tool_entities=tool_entities),
        ))
        with pytest.raises(_StopAfterMerge):
            await _pipeline(_CID, message, "sales", VisitorInfo(name="Client"), None,
                            _persist_state={"ai_persisted": False})
    calls = [c.args[1] for c in update.await_args_list]
    return calls[-1] if calls else {}


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["I will", "sounds good", "Ok", "nothing specific yet, I'll tell you later"])
async def test_a_message_without_a_date_keeps_the_dates_the_client_gave(message):
    ents = await _merged(message, dict(_WRONG), _LEAD)
    assert ents.get("departure_date") in (None, _OUT)
    assert ents.get("return_date") in (None, _BACK)
    assert "2026-11-23" not in ents.values() and "2026-11-30" not in ents.values()
    # The rest of what the tool read still goes in.
    assert ents.get("origin") == "JFK" and ents.get("destination") == "LHR"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["actually make it February 20 instead",
                                     "can we push it back a week?",
                                     "2/20 works better",
                                     "change the dates please, the 20th"])
async def test_a_message_that_speaks_of_a_date_still_moves_them(message):
    ents = await _merged(message, {**_WRONG, "departure_date": "2027-02-20", "return_date": "2027-03-20"}, _LEAD)
    assert ents.get("departure_date") == "2027-02-20"
    assert ents.get("return_date") == "2027-03-20"


@pytest.mark.asyncio
async def test_a_lead_without_dates_still_takes_the_tools_dates():
    """Nothing to keep: the tool fills what the lead does not hold yet, as before."""
    lead = {k: v for k, v in _LEAD.items() if k not in ("departure_date", "return_date")}
    ents = await _merged("I will", {"departure_date": _OUT, "return_date": _BACK}, lead)
    assert ents.get("departure_date") == _OUT and ents.get("return_date") == _BACK


@pytest.mark.asyncio
async def test_only_the_held_date_is_kept_a_missing_return_is_still_filled():
    lead = {k: v for k, v in _LEAD.items() if k != "return_date"}
    ents = await _merged("sounds good", {"departure_date": "2026-11-23", "return_date": _BACK}, lead)
    assert ents.get("departure_date") in (None, _OUT)
    assert ents.get("return_date") == _BACK


@pytest.mark.parametrize("text", [
    "January 12, 2027", "back on 10 March 2027", "feb 3 to feb 9", "Nov. 8", "12/12/2026", "2027-02-10",
    "on Friday", "the 17th", "a week later", "push it back", "change the dates", "next month", "2 weeks",
    "in March", "tomorrow", "in two weeks", "a couple of weeks", "for Christmas", "next summer", "asap", "12.01",
    "on the 20", "what is my return date",
    # A month after a word that makes it one (CodeRabbit #279): "may" alone stays a verb.
    "Actually, in May instead", "early Feb works", "mid-March", "end of June",
])
def test_these_speak_of_a_date(text):
    assert mentions_date(text)


@pytest.mark.parametrize("text", [
    "I will", "sounds good", "Ok", "Only one (me), and yes to trip insurance",
    "nothing specific yet, I'll tell you later", "1", "2 adults", "Dana", "", None,
    "we may go business", "send it to the email", "have a nice day", "my wife and I", "aisle seat please",
    "I may call later", "in decent shape",
])
def test_these_do_not(text):
    assert not mentions_date(text)
