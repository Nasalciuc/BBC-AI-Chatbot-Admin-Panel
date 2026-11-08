"""Blind take, on the server — an agent reads a waiting chat only after Take.

Owner's rule (Nov 2026): agents must not know where a waiting client flies,
so they can't pick chats by destination. The panel shows a gate; these tests
pin that the API agrees, so the gate is not the only thing standing:

  * the rule itself: unassigned, open, waiting for a person or in the shared
    line, not a chat this agent was already talking in, and only for agents;
  * the detail comes back with the words, the lead, the summary, the contact
    and the visit's metadata taken out (presence stays, the gate shows it);
  * messages, typing and the stream answer 403 until Take;
  * an open stream asks again before every batch of words it sends, after
    taking the batch off its queue — no interval — so a chat that starts
    waiting stops reaching an agent who isn't on it at the very next event;
  * the agent's own chats cost one tiny read per 10 minutes, not one per poll;
  * a check that can't be made fails CLOSED (503, counted in /health) — the
    agent's own chats answer from memory, so a blip doesn't reach them;
  * a chat that doesn't exist isn't hidden ("no chat" is not "couldn't look").
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import conversations as conv_api  # noqa: E402
from app.db import supabase as db  # noqa: E402
from app.security.auth import get_current_user  # noqa: E402

ME = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"

AGENTS = ["sales", "support"]
MANAGERS = ["owner", "admin", "dev", "qa", "supervisor", "project_manager"]


@pytest.fixture(autouse=True)
def _fresh_known_cache():
    conv_api._known_until.clear()
    yield
    conv_api._known_until.clear()


def _row(**kw):
    base = {"id": "c1", "status": "needs_agent", "mode": "ai", "assigned_agent_id": None,
            "engaged_agent_id": None, "queued_at": "2026-11-08T20:00:00+00:00"}
    base.update(kw)
    return base


def _user(role="sales"):
    return {"id": ME, "role": role, "tunnel_scope": "sales"}


# ── the rule ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", AGENTS)
@pytest.mark.parametrize("row", [
    _row(),                                                         # asked for a person
    _row(status="active", mode="human", queued_at=None),            # human mode, nobody on it
    _row(status="active", mode="waiting_for_agent", queued_at=None),
    _row(status="active", mode="ai"),                               # released back into the line
    _row(engaged_agent_id=OTHER),                                   # someone else talked in it
])
def test_an_agent_is_blind_on_a_chat_waiting_for_a_person(role, row):
    assert conv_api._taken_blind(_user(role), row) is True


@pytest.mark.parametrize("row", [
    _row(assigned_agent_id=ME),                                     # mine (or my silent reservation)
    _row(engaged_agent_id=ME),                                      # I was talking in it
    _row(engaged_agent_id=None, metadata={"engaged_agent_id": ME}), # same, full row shape
    _row(assigned_agent_id=OTHER),                                  # another agent has it
    _row(status="closed"),
    _row(status="active", mode="ai", queued_at=None),               # the AI has it, nobody waits
])
def test_an_agent_reads_every_chat_that_is_not_waiting_or_is_theirs(row):
    assert conv_api._taken_blind(_user("sales"), row) is False


@pytest.mark.parametrize("role", MANAGERS)
def test_people_who_give_chats_out_are_never_blind(role):
    assert conv_api._taken_blind(_user(role), _row()) is False


# ── the endpoints ─────────────────────────────────────────────────────


def _client(role="sales") -> TestClient:
    app = FastAPI()
    app.include_router(conv_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    return TestClient(app)


FULL = {
    "id": "c1", "tunnel": "sales", "status": "needs_agent", "mode": "ai", "assigned_agent_id": None,
    "visitor_name": "Grace Miller", "visitor_email": "grace@example.com", "visitor_phone": "+971 50 000 0000",
    "visitor_phone_country": "AE", "summary": "Wants business class Boston to Dubai on 14 Dec.",
    "flagged_reason": None, "chat_number": 4799, "queued_at": "2026-11-08T20:00:00+00:00",
    "created_at": "2026-11-08T19:55:00+00:00", "updated_at": "2026-11-08T20:01:00+00:00",
    "message_count": 6, "ai_cost_total": 0.03,
    "metadata": {"widget_presence": "online", "widget_last_event_at": "2026-11-08T20:01:00+00:00",
                 "utm_term": "business class boston to dubai", "page_url": "https://x/flights/dubai",
                 "client_ip": "203.0.113.9", "engaged_agent_id": None},
    "messages": [{"id": "m1", "role": "user", "content": "Boston to Dubai, two of us"}],
    "lead": {"origin_code": "BOS", "destination_code": "DXB", "notes": "Chat: Budget~$9,000"},
}


@pytest.mark.parametrize("role", AGENTS)
def test_the_detail_of_a_waiting_chat_is_who_and_how_long_only(role):
    with patch.object(db, "get_conversation", new=AsyncMock(return_value=dict(FULL))):
        r = _client(role).get("/api/conversations/c1")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["messages"] == [] and d["lead"] is None and d["summary"] is None
    assert d["visitor_email"] is None and d["visitor_phone"] is None and d["visitor_phone_country"] is None
    assert d["metadata"] == {"widget_presence": "online", "widget_last_event_at": "2026-11-08T20:01:00+00:00"}
    # What the gate shows stays.
    assert (d["visitor_name"], d["status"], d["queued_at"], d["chat_number"]) == (
        "Grace Miller", "needs_agent", "2026-11-08T20:00:00+00:00", 4799)
    assert "dubai" not in r.text.lower() and "dxb" not in r.text.lower()


def test_the_agent_who_has_it_gets_the_whole_chat():
    mine = dict(FULL, assigned_agent_id=ME, status="active", mode="human")
    with patch.object(db, "get_conversation", new=AsyncMock(return_value=mine)):
        r = _client("sales").get("/api/conversations/c1")
    d = r.json()["data"]
    assert d["messages"] and d["lead"]["destination_code"] == "DXB" and d["summary"]


def test_the_owner_reads_a_waiting_chat_whole():
    with patch.object(db, "get_conversation", new=AsyncMock(return_value=dict(FULL))):
        r = _client("owner").get("/api/conversations/c1")
    d = r.json()["data"]
    assert d["messages"] and d["lead"]["destination_code"] == "DXB"
    assert d["metadata"]["utm_term"] == "business class boston to dubai"


def test_messages_and_typing_wait_for_take():
    reader = AsyncMock(return_value=[{"id": "m1", "content": "Boston to Dubai"}])
    with patch.object(db, "get_blind_inputs", new=AsyncMock(return_value=_row())), \
         patch.object(db, "get_messages_after", new=reader):
        c = _client("sales")
        r1 = c.get("/api/conversations/c1/messages")
        r2 = c.get("/api/conversations/c1/typing")
    assert r1.status_code == 403 and r2.status_code == 403
    assert r1.json()["detail"] == "Take the chat to read it."
    reader.assert_not_awaited()


def test_an_agents_own_chat_is_checked_once_then_remembered():
    blind = AsyncMock(return_value=_row(assigned_agent_id=ME, status="active", mode="human"))
    reader = AsyncMock(return_value=[{"id": "m1", "content": "Thanks"}])
    with patch.object(db, "get_blind_inputs", new=blind), \
         patch.object(db, "get_messages_after", new=reader):
        c = _client("sales")
        for _ in range(5):
            assert c.get("/api/conversations/c1/messages").status_code == 200
    assert blind.await_count == 1, "polls of my own chat must not each cost a read"
    assert reader.await_count == 5


def test_a_waiting_chat_is_checked_every_time_until_take():
    blind = AsyncMock(return_value=_row())
    with patch.object(db, "get_blind_inputs", new=blind), \
         patch.object(db, "get_messages_after", new=AsyncMock(return_value=[])):
        c = _client("sales")
        c.get("/api/conversations/c1/messages")
        c.get("/api/conversations/c1/messages")
        blind.return_value = _row(assigned_agent_id=ME, status="active", mode="human")  # Take
        r = c.get("/api/conversations/c1/messages")
    assert blind.await_count == 3, "\"may read\" is never remembered for a waiting chat"
    assert r.status_code == 200


def test_a_check_that_cant_be_made_fails_closed():
    reader = AsyncMock(return_value=[{"id": "m1", "content": "Boston to Dubai"}])
    before = conv_api.BLIND_HEALTH["check_failed"]
    with patch.object(db, "get_blind_inputs", new=AsyncMock(side_effect=RuntimeError("network"))), \
         patch.object(db, "get_messages_after", new=reader):
        c = _client("sales")
        r1 = c.get("/api/conversations/c1/messages")
        r2 = c.get("/api/conversations/c1/typing")
    assert r1.status_code == 503 and r2.status_code == 503
    assert r1.json()["detail"] == "Couldn't check this chat. Try again in a moment."
    assert "dubai" not in r1.text.lower()
    reader.assert_not_awaited()
    assert conv_api.BLIND_HEALTH["check_failed"] == before + 2, "every closed door is counted in /health"
    assert conv_api.BLIND_HEALTH["last_failed_at"]


def test_a_chat_that_does_not_exist_is_not_hidden():
    # "No chat" is not "couldn't look": nothing in it to hide, so the request
    # goes on and answers whatever it answers for a missing chat.
    reader = AsyncMock(return_value=[])
    before = conv_api.BLIND_HEALTH["check_failed"]
    with patch.object(db, "get_blind_inputs", new=AsyncMock(return_value=None)), \
         patch.object(db, "get_messages_after", new=reader):
        r = _client("sales").get("/api/conversations/c1/messages")
    assert r.status_code == 200
    reader.assert_awaited_once()
    assert conv_api.BLIND_HEALTH["check_failed"] == before


def test_an_agents_own_chat_keeps_working_through_a_blip():
    blind = AsyncMock(side_effect=[_row(assigned_agent_id=ME, status="active", mode="human"),
                                   RuntimeError("network")])
    with patch.object(db, "get_blind_inputs", new=blind), \
         patch.object(db, "get_messages_after", new=AsyncMock(return_value=[])):
        c = _client("sales")
        assert c.get("/api/conversations/c1/messages").status_code == 200
        assert c.get("/api/conversations/c1/messages").status_code == 200, "answered from memory"
    assert blind.await_count == 1


def test_managers_never_pay_for_the_check():
    # Not even when the check would fail: they never make it.
    blind = AsyncMock(side_effect=RuntimeError("network"))
    with patch.object(db, "get_blind_inputs", new=blind), \
         patch.object(db, "get_messages_after", new=AsyncMock(return_value=[])):
        r = _client("owner").get("/api/conversations/c1/messages")
    assert r.status_code == 200
    blind.assert_not_awaited()


def _stream_client(role="sales") -> TestClient:
    from app.api import agent as agent_api

    app = FastAPI()
    app.include_router(agent_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    return TestClient(app)


def test_the_stream_waits_for_take():
    with patch.object(db, "get_conversation_simple", new=AsyncMock(return_value={"id": "c1", "tunnel": "sales"})), \
         patch.object(db, "get_blind_inputs", new=AsyncMock(return_value=_row())):
        r = _stream_client().get("/api/agent/stream/c1")
    assert r.status_code == 403


def test_the_stream_fails_closed_at_the_door_too():
    with patch.object(db, "get_conversation_simple", new=AsyncMock(return_value={"id": "c1", "tunnel": "sales"})), \
         patch.object(db, "get_blind_inputs", new=AsyncMock(side_effect=RuntimeError("network"))):
        r = _stream_client().get("/api/agent/stream/c1")
    assert r.status_code == 503


# ── an open stream ────────────────────────────────────────────────────

THEIRS = _row(assigned_agent_id=OTHER, status="active", mode="human")   # a colleague has it: readable
MINE = _row(assigned_agent_id=ME, status="active", mode="human")
M1 = {"id": "m1", "role": "user", "content": "Boston to Dubai, two of us"}
M2 = {"id": "m2", "role": "user", "content": "Business class, back on the 28th"}
TYPING = {"event": "typing", "is_typing": True, "text": "Do you fly to Dub"}
PRESENCE = {"event": "presence", "widget_open": True, "widget_presence": "online"}
CHUNK = {"event": "stream_chunk", "delta": "Dubai on the 14th"}
END = {"event": "stream_end", "id": "m3", "role": "ai", "content": "Dubai on the 14th works."}


def _frame(e: dict) -> str:
    import json
    return f"data: {json.dumps(e)}\n\n"


def _drive(user: dict, seen: AsyncMock, *waves: list):
    """Open the panel stream at production settings and feed it `waves` of
    events: each wave lands in its queue after the stream finished with the
    one before (the first is there when it opens). Returns every frame the
    stream sent and the release of its subscription."""
    import asyncio
    from app.api import agent as agent_api
    from app.realtime.manager import manager

    async def go():
        q: asyncio.Queue = asyncio.Queue()
        pending = [list(w) for w in waves]

        def next_wave() -> bool:
            if not pending:
                return True  # nothing left to say: the panel goes away
            for e in pending.pop(0):
                q.put_nowait(e)
            return False

        request = MagicMock()
        request.is_disconnected = AsyncMock(side_effect=lambda: next_wave())
        with patch.object(db, "get_conversation_simple", new=AsyncMock(return_value={"id": "c1", "tunnel": "sales"})), \
             patch.object(db, "get_blind_inputs", new=seen), \
             patch.object(manager, "connect", new=AsyncMock(return_value=q)), \
             patch.object(manager, "disconnect") as release:
            res = await agent_api.agent_stream("c1", request, user=user)
            frames = [chunk async for chunk in res.body_iterator]
        return frames, release

    return asyncio.run(go())


def test_a_chat_that_starts_waiting_stops_reaching_an_agent_who_isnt_on_it():
    # Opened while a colleague had it; between two messages the colleague let
    # it go back to the line. No interval: the very next message is held.
    seen = AsyncMock(side_effect=[THEIRS, THEIRS, _row()])
    before = conv_api.BLIND_HEALTH["streams_closed"]
    frames, release = _drive(_user("sales"), seen, [M1], [M2])
    assert frames == [_frame(M1), _frame({"event": "blind"})], "the panel is told, and M2 never goes out"
    assert "28th" not in "".join(frames)
    assert conv_api.BLIND_HEALTH["streams_closed"] == before + 1
    release.assert_called_once()


def test_the_first_event_after_the_door_is_checked_too():
    # It went back to the line between opening the stream and the first word.
    seen = AsyncMock(side_effect=[THEIRS, _row()])
    frames, _ = _drive(_user("sales"), seen, [M1, TYPING])
    assert frames == [_frame({"event": "blind"})]
    assert "dubai" not in "".join(frames).lower()


def test_a_stream_the_agent_may_still_read_carries_every_event():
    seen = AsyncMock(return_value=THEIRS)
    frames, release = _drive(_user("sales"), seen, [M1], [TYPING], [M2])
    assert frames == [_frame(M1), _frame(TYPING), _frame(M2)]
    assert seen.await_count == 4, "the door, then once before each batch"
    release.assert_called_once()


def test_events_waiting_together_go_out_after_one_check():
    seen = AsyncMock(return_value=THEIRS)
    frames, _ = _drive(_user("sales"), seen, [M1, TYPING, M2])
    assert frames == [_frame(M1), _frame(TYPING), _frame(M2)]
    assert seen.await_count == 2, "the door, then one check for the three"


def test_presence_alone_asks_nothing():
    # Presence carries no words — the gate itself shows it.
    seen = AsyncMock(side_effect=[THEIRS])
    frames, _ = _drive(_user("sales"), seen, [PRESENCE])
    assert frames == [_frame(PRESENCE)]
    assert seen.await_count == 1


def test_a_re_check_that_cant_be_made_closes_the_stream():
    seen = AsyncMock(side_effect=[THEIRS, RuntimeError("network")])
    before = conv_api.BLIND_HEALTH["check_failed"]
    frames, release = _drive(_user("sales"), seen, [M1])
    assert frames == [], "fail closed, without a word: the panel reconnects and is asked at the door"
    assert conv_api.BLIND_HEALTH["check_failed"] == before + 1
    release.assert_called_once()


def test_the_agents_own_chat_is_not_re_read_mid_stream():
    seen = AsyncMock(return_value=MINE)
    frames, _ = _drive(_user("sales"), seen, [M1], [TYPING], [M2])
    assert len(frames) == 3
    assert seen.await_count == 1, "answered from memory"


def test_an_agent_gets_whole_replies_not_the_ai_chunks():
    # The panel renders whole messages; chunks would only cost checks.
    frames, _ = _drive(_user("sales"), AsyncMock(return_value=MINE), [CHUNK, CHUNK, END])
    assert frames == [_frame(END)]


def test_a_managers_stream_is_never_re_checked_and_keeps_every_frame():
    seen = AsyncMock(return_value=_row())
    frames, _ = _drive(_user("owner"), seen, [M1, CHUNK], [END])
    assert frames == [_frame(M1), _frame(CHUNK), _frame(END)]
    seen.assert_not_awaited()


def test_health_shows_the_blind_take_counters():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "health.py").read_text()
    assert src.count('"blind_take": dict(BLIND_HEALTH),') == 2, "both the light and the full payload"


# ── the read behind the check ─────────────────────────────────────────


class _Q:
    def __init__(self, fail_first=None):
        self.selects = []
        self.fail_first = fail_first

    def table(self, _n):
        return self

    def select(self, cols):
        self.selects.append(cols)
        return self

    def eq(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self.fail_first and len(self.selects) == 1:
            raise self.fail_first
        return MagicMock(data=[{"id": "c1", "status": "active"}])


def _run(q):
    import asyncio
    with patch.object(db, "get_client", return_value=q), \
         patch.object(db, "_run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())):
        return asyncio.run(db.get_blind_inputs("c1"))


def test_the_check_reads_five_fields_and_the_queue_mark():
    q = _Q()
    with patch.object(db, "_queued_at_column_available", return_value=True):
        assert _run(q) == {"id": "c1", "status": "active"}
    assert "queued_at" in q.selects[0] and "engaged_agent_id:metadata->>engaged_agent_id" in q.selects[0]


def test_without_the_queue_column_the_check_still_reads():
    q = _Q(fail_first=Exception('column conversations.queued_at does not exist'))
    with patch.object(db, "_queued_at_column_available", return_value=True), \
         patch.object(db, "_is_missing_column_error", return_value=True), \
         patch.object(db, "_downgrade_queued_at") as down:
        assert _run(q) == {"id": "c1", "status": "active"}
    down.assert_called_once()
    assert "queued_at" not in q.selects[1]


def test_a_broken_read_raises_and_is_logged(caplog):
    # Never None: None means "no such chat", which the guard lets through.
    q = _Q(fail_first=RuntimeError("network"))
    with patch.object(db, "_queued_at_column_available", return_value=True), \
         patch.object(db, "_is_missing_column_error", return_value=False):
        with pytest.raises(RuntimeError):
            _run(q)
    assert any("get_blind_inputs" in r.message for r in caplog.records)


def test_a_missing_chat_reads_as_none():
    class _Empty(_Q):
        def execute(self):
            return MagicMock(data=[])

    with patch.object(db, "_queued_at_column_available", return_value=True):
        assert _run(_Empty()) is None
