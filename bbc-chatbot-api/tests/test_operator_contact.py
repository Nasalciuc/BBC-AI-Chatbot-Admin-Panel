"""A contact the client types to an operator reaches the conversation (operator_contact).

16-17 Nov: in chats an operator had taken, 5 of the 7 emails clients typed never
reached the conversation; in chats with Alex alone, 7 of 7 did.
"""

import asyncio
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.pipeline import operator_contact  # noqa: E402

NAME_Q = [
    {"role": "user", "content": "I need 2 seats JFK to LHR"},
    {"role": "agent", "content": "Happy to help! What's your full name?"},
]


@pytest.fixture(autouse=True)
def _reset():
    operator_contact._reset_for_tests()
    yield
    operator_contact._reset_for_tests()


def _db(history=None, conv=None):
    return (
        AsyncMock(),  # update_lead_from_entities
        AsyncMock(return_value=history if history is not None else []),
        AsyncMock(return_value=conv if conv is not None else {}),
    )


async def _run(message, history=None, conv=None):
    write, recent, simple = _db(history, conv)
    with patch("app.services.lead_service.update_lead_from_entities", new=write), \
         patch("app.db.supabase.get_recent_messages", new=recent), \
         patch("app.db.supabase.get_conversation_simple", new=simple):
        await operator_contact.record_contact("c1", message)
    return write, recent, simple


@pytest.mark.asyncio
async def test_an_email_typed_to_the_operator_reaches_the_conversation_and_never_the_logs(caplog):
    with caplog.at_level(logging.INFO):
        write, recent, _ = await _run("Sure, it's paul.moreau@example.com")
    write.assert_awaited_once_with("c1", {"email": "paul.moreau@example.com"})
    assert "paul.moreau@example.com" not in caplog.text
    assert "email from the client's message" in caplog.text
    assert operator_contact.OPERATOR_CONTACT_HEALTH == {"found": 1, "failures": 0}
    # Nothing in the message has the shape of a name: the history is not even read.
    recent.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_phone_typed_to_the_operator_reaches_the_conversation(caplog):
    with caplog.at_level(logging.INFO):
        write, _, _ = await _run("you can call me at +1 (305) 555-0101 after 6pm")
    write.assert_awaited_once_with("c1", {"phone": "+1 (305) 555-0101"})
    assert "555-0101" not in caplog.text


@pytest.mark.asyncio
async def test_the_answer_to_the_operators_name_question_names_the_client():
    write, recent, _ = await _run("Paul Moreau", history=NAME_Q, conv={"visitor_name": None})
    write.assert_awaited_once_with("c1", {"name": "Paul Moreau"})
    recent.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_name_shaped_message_names_nobody_when_no_one_asked():
    asked_dates = [{"role": "agent", "content": "Which dates work for you?"}]
    write, _, simple = await _run("Next week", history=asked_dates)
    write.assert_not_awaited()
    simple.assert_not_awaited()
    write, _, _ = await _run("Paul Moreau", history=asked_dates)
    write.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_known_name_is_never_replaced_by_another():
    write, _, _ = await _run("Mary Smith", history=NAME_Q, conv={"visitor_name": "Paul Moreau"})
    write.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_trip_message_records_nothing():
    write, recent, _ = await _run("JFK to LHR on 12/20/2026, back 01/05/2027, 2 adults")
    write.assert_not_awaited()
    recent.assert_not_awaited()
    assert operator_contact.OPERATOR_CONTACT_HEALTH == {"found": 0, "failures": 0}


@pytest.mark.asyncio
async def test_a_failed_read_is_counted_and_logged_without_the_message(caplog):
    write = AsyncMock()
    with patch("app.services.lead_service.update_lead_from_entities", new=write), \
         patch("app.db.supabase.get_recent_messages",
               new=AsyncMock(side_effect=RuntimeError("down: paul.moreau@example.com"))), \
         caplog.at_level(logging.INFO):
        await operator_contact.record_contact("c1", "Paul Moreau")
    write.assert_not_awaited()
    assert operator_contact.OPERATOR_CONTACT_HEALTH == {"found": 0, "failures": 1}
    assert "not recorded (RuntimeError)" in caplog.text
    assert "paul.moreau@example.com" not in caplog.text


def test_health_shows_the_operator_contacts():
    import inspect

    from app.api import health

    src = inspect.getsource(health.health_detail)
    assert src.count('"operator_contacts": dict(OPERATOR_CONTACT_HEALTH)') == 2


@pytest.mark.asyncio
async def test_a_message_to_the_operator_records_its_contact_beside_the_queued_reply():
    from app.api.chat import chat
    from app.models.chat import ChatRequest, VisitorInfo
    from app.pipeline import orchestrator

    cid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    conv = {"id": cid, "status": "active", "mode": "human", "assigned_agent_id": "agent-1", "metadata": {}}
    started = asyncio.Event()
    never = asyncio.Event()
    seen = []

    async def _record(conversation_id, message):
        seen.append((conversation_id, message))
        started.set()
        await never.wait()  # the writes, as slow as they like: the reply never waits

    req = MagicMock(headers={}, client=MagicMock(host="127.0.0.1"))
    body = ChatRequest(message="my email is paul.moreau@example.com", conversation_id=cid, tunnel="sales",
                       visitor=VisitorInfo(name="Test"), visitor_id="visitor-uuid-1")
    with patch("app.api.chat.db.count_messages", new=AsyncMock(return_value=3)), \
         patch("app.api.chat.db.get_conversation_simple", new=AsyncMock(return_value=conv)), \
         patch("app.api.chat.lead_service.get_or_create_lead", new=AsyncMock(return_value=None)), \
         patch("app.api.chat.db.get_conversation_mode", new=AsyncMock(return_value="human")), \
         patch("app.services.handoff.is_agent_effectively_offline", new=AsyncMock(return_value=False)), \
         patch("app.services.conversation_service.add_message", new=AsyncMock(return_value={"id": "m1"})), \
         patch("app.services.handoff._handoff_phrase_recently_sent", new=AsyncMock(return_value=False)), \
         patch("app.pipeline.operator_card.extract_and_push", new=AsyncMock()), \
         patch("app.pipeline.operator_contact.record_contact", new=_record):
        resp = await asyncio.wait_for(chat(body, req, None), timeout=2)
        await asyncio.wait_for(started.wait(), timeout=1)
    assert resp.type == "queued"
    assert seen == [(cid, "my email is paul.moreau@example.com")]
    for t in list(orchestrator._background_tasks):
        t.cancel()


# ── The score keeps what the conversation already holds (CodeRabbit #268) ──
# The contacts live on the conversation, not the lead: an email typed to an
# operator arrives alone, and scored from it alone the name and phone points
# the client already earned would drop.


class _Res:
    def __init__(self, data):
        self.data = data


class _Table:
    """One table of the Supabase client: rows to read, every update written down."""

    def __init__(self, rows=None, read_fails=False):
        self.rows, self.read_fails, self.updates = rows or [], read_fails, []
        self._op = None

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def update(self, payload):
        self._op = "update"
        self.updates.append(payload)
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        if self._op == "select" and self.read_fails:
            raise ConnectionError("read refused")
        return _Res([dict(r) for r in self.rows] if self._op == "select" else [])


STORED = {"visitor_name": "Ana Test", "visitor_email": None, "visitor_phone": "+12105550100"}


async def _update(entities, *, score, conv=None, conv_read_fails=False, lead=None):
    from app.db import supabase as db
    from app.services import lead_service

    tables = {
        "leads": _Table([{"id": "L1", "score": score, **(lead or {})}]),
        "conversations": _Table([conv] if conv else [], read_fails=conv_read_fails),
    }
    client = MagicMock()
    client.table.side_effect = lambda name: tables[name]

    async def _sync(fn, *_a, **_k):
        return fn()

    with patch.object(db, "get_client", return_value=client), patch.object(db, "_run_sync", _sync):
        await lead_service.update_lead_from_entities("c1", entities)
    return tables


def _score_written(tables):
    return next((u for u in tables["leads"].updates if "score" in u), None)


@pytest.mark.asyncio
async def test_an_email_alone_keeps_the_name_and_phone_points():
    tables = await _update({"email": "ana@example.com"}, score=35, conv=STORED)
    assert _score_written(tables) == {"score": 45, "tier": "bronze"}  # name 20 + phone 15 + email 10


@pytest.mark.asyncio
async def test_a_route_alone_keeps_the_contact_points():
    conv = {**STORED, "visitor_email": "ana@example.com"}
    tables = await _update({"origin": "JFK", "destination": "LHR"}, score=45, conv=conv)
    assert _score_written(tables) == {"score": 60, "tier": "silver"}


@pytest.mark.asyncio
async def test_the_calls_contact_counts_beside_the_stored_ones():
    tables = await _update({"phone": "2105550100"}, score=20, conv={"visitor_name": "Ana Test"})
    assert {"visitor_phone": "+12105550100"} in tables["conversations"].updates
    assert _score_written(tables) == {"score": 35, "tier": "bronze"}


@pytest.mark.asyncio
async def test_an_unchanged_score_is_not_written_again():
    tables = await _update({"email": "ana@example.com"}, score=45, conv=STORED)
    assert _score_written(tables) is None


@pytest.mark.asyncio
async def test_a_number_too_short_to_dial_never_wipes_the_stored_phone():
    tables = await _update({"phone": "12345"}, score=35, conv=STORED)
    assert all("visitor_phone" not in u for u in tables["conversations"].updates)
    assert _score_written(tables) is None  # the stored phone still counts: 35 stands


@pytest.mark.asyncio
async def test_unread_contacts_never_lower_the_score(caplog):
    with caplog.at_level(logging.INFO):
        tables = await _update({"email": "ana@example.com"}, score=45, conv=STORED, conv_read_fails=True)
    assert _score_written(tables) is None  # 10 from this call alone would have dropped 35 points
    assert "stored contacts not read for the score (ConnectionError)" in caplog.text
    assert "ana@example.com" not in caplog.text


@pytest.mark.asyncio
async def test_unread_contacts_still_let_the_score_rise():
    tables = await _update(
        {"origin": "JFK", "destination": "LHR", "email": "ana@example.com"},
        score=0, conv=STORED, conv_read_fails=True,
    )
    assert _score_written(tables) == {"score": 25, "tier": "bronze"}  # route 15 + email 10, a floor
    assert {"visitor_email": "ana@example.com"} in tables["conversations"].updates
