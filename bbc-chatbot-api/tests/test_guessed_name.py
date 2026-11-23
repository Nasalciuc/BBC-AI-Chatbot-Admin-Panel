"""The reply never calls the client by a name they did not give (validator.strip_guessed_name).

#3341, and again in November 2026: with no name in the chat, the reply called the client by a name read
from their e-mail address. The prompt forbids it and the model did it anyway; now a word the reply uses
to address the client, found in the e-mail's local part and never typed by the client, is taken out.
Names and addresses below are invented.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.models.chat import VisitorInfo
from app.pipeline.generator import GeneratedResponse
from app.pipeline.orchestrator import _pipeline
from app.pipeline.validator import strip_guessed_name

EMAIL = "dana.miller1960@example.com"


@pytest.mark.parametrize("reply,clean", [
    ("Email is all set, Dana! When would you like to fly?", "Email is all set! When would you like to fly?"),
    ("Thanks, Dana. What dates work?", "Thanks. What dates work?"),
    ("Hi Dana, happy to help.", "Hi, happy to help."),
    ("Perfect Dana!", "Perfect!"),
    ("Thank you, Dana Miller!", "Thank you!"),
    ("Dana, I've noted your dates. Got it.", "I've noted your dates. Got it."),
    ("Noted. Dana, how many travel?", "Noted. How many travel?"),
    ("I'll pass this to a consultant, Dana.", "I'll pass this to a consultant."),
    ("Speak soon, Dana", "Speak soon"),
])
def test_a_name_read_from_the_email_is_taken_out(reply, clean):
    assert strip_guessed_name(reply, EMAIL, ["my email is " + EMAIL]) == (clean, True)


@pytest.mark.parametrize("reply,clean", [
    ("Thanks, Jordan!", "Thanks!"),
    ("Have a great trip, Jordan!", "Have a great trip!"),
])
def test_the_same_word_addressing_the_client_still_goes(reply, clean):
    assert strip_guessed_name(reply, "jordan@example.com", ["Flights to Amman, please"]) == (clean, True)


def test_part_of_a_run_together_address_counts_too():
    assert strip_guessed_name("Thanks, Dana!", "danam@example.com", []) == ("Thanks!", True)


@pytest.mark.parametrize("reply,email,typed", [
    ("Thanks, Dana!", EMAIL, ["Hi, Dana here, two of us to Rome"]),   # the client typed it
    ("Thanks, Dana!", None, []),                                       # no e-mail
    ("Thanks, Dana!", "12345@example.com", []),                        # no letters to read a name from
    ("Thanks, Robin!", EMAIL, []),                                     # not in the address
    ("Thanks, Dana Smith!", EMAIL, []),                                # a last name the address lacks
    ("Flights to Miller Field? Sure.", EMAIL, []),                     # not addressing the client
    ("Great, I have your dates.", "greattravels@example.com", []),     # a sentence opener, never a name
    ("Thanks! When would you like to fly?", EMAIL, []),
    ("Got it, thanks.", "thanksgiving@example.com", []),
    ("", EMAIL, []),
    # A place and its country, not someone addressed (CodeRabbit #279).
    ("Your destination is Amman, Jordan.", "jordan@example.com", ["Flights to Amman, please"]),
])
def test_these_stay_as_written(reply, email, typed):
    assert strip_guessed_name(reply, email, typed) == (reply, False)


# ── the turn ─────────────────────────────────────────────────────────────────

_CID = "aaaaaaaa-bbbb-cccc-dddd-000000003341"
_CONV = {"id": _CID, "mode": "ai", "status": "active", "tunnel": "sales", "metadata": {}}
_LEAD = {"id": "lead-g", "conversation_id": _CID, "origin_code": "JFK", "destination_code": "LHR",
         "trip_type": "round_trip", "notes": ""}


@pytest.fixture(autouse=True)
def _flags_clean():
    flags._reset_for_tests()
    yield
    flags._reset_for_tests()


class _Delivered(Exception):
    """Raised at STEP 8: the reply has been checked."""


async def _checked(visitor: VisitorInfo, reply: str) -> MagicMock:
    """Run one AI turn up to delivery; the spy on strip_guessed_name says what it was given."""
    from contextlib import ExitStack

    spy = MagicMock(wraps=strip_guessed_name)
    with ExitStack() as stack:
        for target, value in (
            ("app.services.conversation_service.get_or_create_conversation", AsyncMock(return_value=dict(_CONV))),
            ("app.services.conversation_service.add_message", AsyncMock(return_value={"id": "m1"})),
            ("app.pipeline.orchestrator.db.get_recent_messages",
             AsyncMock(return_value=[{"role": "user", "content": "hello, flights to London"}])),
            ("app.services.moderation.moderate_message", AsyncMock()),
            ("app.pipeline.orchestrator.db.update_conversation", AsyncMock()),
            ("app.pipeline.orchestrator.db.update_lead", AsyncMock()),
            ("app.pipeline.orchestrator.lead_service.get_or_create_lead", AsyncMock(return_value=dict(_LEAD))),
            ("app.pipeline.orchestrator.lead_service.update_lead_from_entities", AsyncMock()),
            ("app.pipeline.orchestrator._taken_by_agent", AsyncMock(side_effect=_Delivered("delivered"))),
        ):
            stack.enter_context(patch(target, new=value))
        stack.enter_context(patch("app.pipeline.intent.claude_classify", return_value=(None, 0.0)))
        stack.enter_context(patch("app.pipeline.orchestrator.strip_guessed_name", new=spy))
        stack.enter_context(patch(
            "app.pipeline.orchestrator.generate_response",
            return_value=GeneratedResponse(text=reply, model_used="sonnet"),
        ))
        with pytest.raises(_Delivered):
            await _pipeline(_CID, "my email is " + EMAIL, "sales", visitor, None,
                            _persist_state={"ai_persisted": False})
    return spy


@pytest.mark.asyncio
async def test_a_chat_without_a_name_has_its_reply_checked():
    spy = await _checked(VisitorInfo(email=EMAIL), "Email is all set, Dana! When would you like to fly?")
    text, email, typed = spy.call_args.args
    assert email == EMAIL and "my email is " + EMAIL in typed and "hello, flights to London" in typed
    assert strip_guessed_name(text, email, typed) == ("Email is all set! When would you like to fly?", True)


@pytest.mark.asyncio
async def test_a_chat_with_a_name_is_left_alone():
    spy = await _checked(VisitorInfo(name="Dana", email=EMAIL), "Email is all set, Dana!")
    spy.assert_not_called()
