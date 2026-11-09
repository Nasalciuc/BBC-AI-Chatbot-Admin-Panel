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
  * nothing is remembered: whose chat it is can change between two polls
    (given to a colleague, back in the line), so every read asks again —
    live typing is gathered so a stream asks at most twice a second for it;
  * a check that can't be made fails CLOSED (503, counted in /health);
  * a chat that doesn't exist isn't hidden ("no chat" is not "couldn't look");
  * the side doors hold too: the list keeps a waiting chat's contact out, and
    an agent can't change or close a waiting chat it hasn't taken — the
    answer would be the whole row, and a closed or re-moded chat isn't
    waiting any more, so it would open without a Take.
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


def test_every_read_asks_again_nothing_is_remembered():
    # Even for the agent's own chat: it can be given away between two polls.
    blind = AsyncMock(return_value=_row(assigned_agent_id=ME, status="active", mode="human"))
    reader = AsyncMock(return_value=[{"id": "m1", "content": "Thanks"}])
    with patch.object(db, "get_blind_inputs", new=blind), \
         patch.object(db, "get_messages_after", new=reader):
        c = _client("sales")
        for _ in range(3):
            assert c.get("/api/conversations/c1/messages").status_code == 200
    assert blind.await_count == 3
    assert reader.await_count == 3


REQUEUED = _row(engaged_agent_id=OTHER)   # a colleague had it, then let it go back to the line


def test_a_chat_that_changed_hands_and_went_back_to_the_line_closes_to_its_old_agent():
    # The agent read it as theirs; it was given to a colleague (still readable);
    # the colleague let it go back to the line. From then on: closed — the
    # client's new words are not the old agent's to read.
    blind = AsyncMock(side_effect=[
        _row(assigned_agent_id=ME, status="active", mode="human"),  # messages: mine
        _row(assigned_agent_id=ME, status="active", mode="human"),  # typing: mine
        _row(assigned_agent_id=OTHER, engaged_agent_id=OTHER, status="active", mode="human"),
        REQUEUED,                                                    # messages
        REQUEUED,                                                    # typing
    ])
    reader = AsyncMock(return_value=[{"id": "m9", "content": "Actually, make it Doha"}])
    typing = AsyncMock(return_value={"is_typing": True, "text": "and two kids"})
    from app.realtime.typing_indicator import typing_manager
    with patch.object(db, "get_blind_inputs", new=blind), \
         patch.object(db, "get_messages_after", new=reader), \
         patch.object(typing_manager, "get_typing", new=typing):
        c = _client("sales")
        assert c.get("/api/conversations/c1/messages").status_code == 200
        assert c.get("/api/conversations/c1/typing").status_code == 200
        assert c.get("/api/conversations/c1/messages").status_code == 200   # a colleague's: readable
        r_messages = c.get("/api/conversations/c1/messages")
        r_typing = c.get("/api/conversations/c1/typing")
    assert r_messages.status_code == 403 and r_typing.status_code == 403
    assert "doha" not in r_messages.text.lower() and "kids" not in r_typing.text
    assert reader.await_count == 2 and typing.await_count == 1


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


def test_a_blip_closes_the_agents_own_chat_too():
    # Fail closed has no exceptions: with no memory there is nothing to fall
    # back on, and a guess is exactly what this check exists to avoid.
    blind = AsyncMock(side_effect=[_row(assigned_agent_id=ME, status="active", mode="human"),
                                   RuntimeError("network")])
    with patch.object(db, "get_blind_inputs", new=blind), \
         patch.object(db, "get_messages_after", new=AsyncMock(return_value=[])):
        c = _client("sales")
        assert c.get("/api/conversations/c1/messages").status_code == 200
        assert c.get("/api/conversations/c1/messages").status_code == 503
    assert blind.await_count == 2


def test_managers_never_pay_for_the_check():
    # Not even when the check would fail: they never make it.
    blind = AsyncMock(side_effect=RuntimeError("network"))
    with patch.object(db, "get_blind_inputs", new=blind), \
         patch.object(db, "get_messages_after", new=AsyncMock(return_value=[])):
        r = _client("owner").get("/api/conversations/c1/messages")
    assert r.status_code == 200
    blind.assert_not_awaited()


# ── the side doors: the list, a change, a close ───────────────────────


def _list_rows():
    contact = {"visitor_email": "grace@example.com", "visitor_phone": "+971 50 000 0000",
               "flagged_reason": "Shared a card number"}
    return [
        dict(_row(id="waiting"), tunnel="sales", visitor_name="Grace Miller", metadata={}, **contact),
        dict(_row(id="mine", assigned_agent_id=ME, status="active", mode="human"),
             tunnel="sales", visitor_name="David Kim", metadata={}, **contact),
        # A colleague's chat reads whole in its detail, so its row does too.
        dict(_row(id="theirs", assigned_agent_id=OTHER, status="active", mode="human"),
             tunnel="sales", visitor_name="Ana Pop", metadata={}, **contact),
    ]


@pytest.mark.parametrize("role", AGENTS)
def test_the_list_keeps_a_waiting_chats_contact_out_too(role):
    # Same as its detail: who, never how to reach them. An e-mail or a phone
    # finds the lead, and the lead names the cities.
    with patch.object(db, "get_conversations", new=AsyncMock(return_value=(_list_rows(), 3))), \
         patch.object(db, "get_last_message_previews", new=AsyncMock(return_value={})):
        r = _client(role).get("/api/conversations?status=active")
    assert r.status_code == 200
    rows = {c["id"]: c for c in r.json()["data"]}
    w = rows["waiting"]
    assert (w["visitor_email"], w["visitor_phone"], w["flagged_reason"]) == (None, None, None)
    assert w["visitor_name"] == "Grace Miller", "the gate shows who it is"
    for cid in ("mine", "theirs"):
        assert rows[cid]["visitor_email"] == "grace@example.com" and rows[cid]["visitor_phone"]


def test_a_managers_list_keeps_every_contact():
    with patch.object(db, "get_conversations", new=AsyncMock(return_value=(_list_rows(), 3))), \
         patch.object(db, "get_last_message_previews", new=AsyncMock(return_value={})):
        r = _client("owner").get("/api/conversations?status=active")
    assert all(c["visitor_email"] == "grace@example.com" for c in r.json()["data"])


def test_an_agent_cannot_change_a_waiting_chat_it_has_not_taken():
    # The answer is the whole row, and a new status or mode would open the
    # chat without a Take: refused before anything is written.
    write = AsyncMock(return_value=dict(FULL, status="active", mode="ai"))
    with patch.object(db, "get_blind_inputs", new=AsyncMock(return_value=_row())), \
         patch.object(db, "update_conversation", new=write):
        r = _client("sales").patch("/api/conversations/c1", json={"status": "active", "mode": "ai"})
    assert r.status_code == 403 and r.json()["detail"] == "Take the chat to read it."
    assert "dubai" not in r.text.lower() and "grace@" not in r.text
    write.assert_not_awaited()


def test_an_agent_still_changes_its_own_chat():
    # The blind check lets an agent through on its own chat. What it may
    # change there is the role gate's call (v2 PR-4): not the visitor's
    # name, e-mail or phone, which only management changes.
    mine = _row(assigned_agent_id=ME, status="active", mode="human")
    write = AsyncMock(return_value=dict(FULL, assigned_agent_id=ME, mode="ai"))
    with patch.object(db, "get_blind_inputs", new=AsyncMock(return_value=mine)), \
         patch.object(db, "update_conversation", new=write):
        r = _client("sales").patch("/api/conversations/c1", json={"mode": "ai"})
    assert r.status_code == 200 and r.json()["success"] is True
    write.assert_awaited_once_with("c1", {"mode": "ai"})


def test_on_its_own_chat_an_agent_still_cannot_rename_the_visitor():
    # Past the blind check, the role gate still keeps the contact fields to
    # management: nothing is written, and the answer says why.
    mine = _row(assigned_agent_id=ME, status="active", mode="human")
    write = AsyncMock(return_value=dict(FULL))
    with patch.object(db, "get_blind_inputs", new=AsyncMock(return_value=mine)), \
         patch.object(db, "update_conversation", new=write):
        r = _client("sales").patch("/api/conversations/c1", json={"visitor_name": "Grace M."})
    assert r.status_code == 200 and r.json()["success"] is False
    assert r.json()["error"] == "No permitted fields to update"
    write.assert_not_awaited()


def test_a_change_that_cant_be_checked_is_not_made():
    write = AsyncMock(return_value=dict(FULL))
    before = conv_api.BLIND_HEALTH["check_failed"]
    with patch.object(db, "get_blind_inputs", new=AsyncMock(side_effect=RuntimeError("network"))), \
         patch.object(db, "update_conversation", new=write):
        r = _client("sales").patch("/api/conversations/c1", json={"status": "active"})
    assert r.status_code == 503
    write.assert_not_awaited()
    assert conv_api.BLIND_HEALTH["check_failed"] == before + 1


def test_a_manager_changes_a_waiting_chat_without_the_check():
    blind = AsyncMock(side_effect=RuntimeError("network"))
    write = AsyncMock(return_value=dict(FULL, status="active"))
    with patch.object(db, "get_blind_inputs", new=blind), \
         patch.object(db, "update_conversation", new=write):
        r = _client("owner").patch("/api/conversations/c1", json={"status": "active"})
    assert r.status_code == 200
    blind.assert_not_awaited()
    write.assert_awaited_once()


def _close(role: str, conv: dict):
    mark = AsyncMock(return_value=True)
    write = AsyncMock(return_value=dict(conv, status="closed"))
    with patch.object(db, "get_conversation", new=AsyncMock(return_value=dict(conv))), \
         patch.object(db, "patch_conversation_presence", new=mark), \
         patch.object(db, "update_conversation", new=write), \
         patch("app.services.presence.log_activity", new=MagicMock()), \
         patch("app.pipeline.orchestrator._fire_and_forget", new=MagicMock()):
        r = _client(role).post("/api/conversations/c1/close")
    return r, mark, write


@pytest.mark.parametrize("role", AGENTS)
def test_an_agent_cannot_close_a_waiting_chat_it_has_not_taken(role):
    # A closed chat isn't waiting any more: closing would open it.
    r, mark, write = _close(role, FULL)
    assert r.status_code == 403 and r.json()["detail"] == "Take the chat to read it."
    mark.assert_not_awaited()
    write.assert_not_awaited()


def test_an_agent_still_closes_its_own_chat():
    r, mark, write = _close("sales", dict(FULL, assigned_agent_id=ME, status="active", mode="human"))
    assert r.status_code == 200 and r.json()["data"]["status"] == "closed"
    mark.assert_awaited_once()
    assert write.await_args.args[1]["status"] == "closed"


def test_a_manager_still_closes_a_waiting_chat():
    r, _, write = _close("owner", FULL)
    assert r.status_code == 200
    assert write.await_args.args[1]["status"] == "closed"


# ── search: a match on what the row hides would tell ──────────────────
#
# The list's search runs in PostgREST (an `or=` filter). This tiny evaluator
# reads the subset of that grammar the filter uses, so the tests can hold the
# filter to the same answer as _taken_blind, row by row.


def _split_top(s: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in s:
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        depth += ch == "("
        depth -= ch == ")"
        cur += ch
    return parts + [cur]


def _cell(row: dict, col: str):
    if "->>" in col:
        base, key = col.split("->>")
        return (row.get(base) or {}).get(key)
    return row.get(col)


def _holds(expr: str, row: dict) -> bool:
    for op, join in (("or(", any), ("and(", all)):
        if expr.startswith(op) and expr.endswith(")"):
            return join(_holds(p, row) for p in _split_top(expr[len(op):-1]))
    col, rest = expr.split(".", 1)
    negated = rest.startswith("not.")
    op, val = (rest[4:] if negated else rest).split(".", 1)
    v = _cell(row, col)
    if op == "is":  # SQL: IS NULL / IS NOT NULL never yields NULL
        return (v is None) != negated
    if v is None:   # SQL: anything else against NULL is NULL, so not a match
        return False
    if op == "in":
        hit = str(v) in val.strip("()").split(",")
    elif op == "eq":
        hit = str(v) == val
    elif op == "neq":
        hit = str(v) != val
    elif op == "ilike":
        import re as _re
        hit = _re.fullmatch(_re.escape(val).replace("%", ".*"), str(v), _re.I) is not None
    else:
        raise AssertionError(f"evaluator doesn't know {op!r}")
    return hit != negated


def _grid():
    import itertools
    for status, mode, assigned, engaged, queued in itertools.product(
            ["needs_agent", "active", "closed"], ["ai", "human", "waiting_for_agent"],
            [None, ME, OTHER], [None, ME, OTHER], [None, "2026-11-08T20:00:00+00:00"]):
        yield {"status": status, "mode": mode, "assigned_agent_id": assigned,
               "queued_at": queued, "metadata": {"engaged_agent_id": engaged}}


@pytest.mark.parametrize("role", AGENTS)
def test_the_search_filter_and_the_blind_rule_agree_on_every_row(role):
    readable = db._readable_to_agent(ME, with_queue=True)
    for row in _grid():
        assert _holds(readable, row) is not conv_api._taken_blind(_user(role), row), row


def test_without_the_queue_column_the_filter_still_agrees_where_it_can():
    # Before 023/024 there is no queued_at to name, and no row carries one.
    readable = db._readable_to_agent(ME, with_queue=False)
    assert "queued_at" not in readable
    for row in _grid():
        if row["queued_at"] is None:
            assert _holds(readable, row) is not conv_api._taken_blind(_user("sales"), row), row


class _Recorder:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a, k))
            return self
        return rec

    def execute(self):
        res = MagicMock()
        res.data, res.count = [dict(r) for r in self.rows], len(self.rows)
        return res


def _search_filter(viewer, term: str) -> str:
    import asyncio
    q = _Recorder([])
    client = MagicMock()
    client.table.return_value = q
    with patch.object(db, "get_client", return_value=client), \
         patch.object(db, "_supervisor_columns_available", return_value=True), \
         patch.object(db, "_get_lead_completeness_inputs", new=AsyncMock(return_value={})), \
         patch.object(db, "enrich_conversations_agent_info", new=AsyncMock(side_effect=lambda r: r)):
        asyncio.run(db.get_conversations(search=term, blind_viewer=viewer))
    (expr,) = [c[1][0] for c in q.calls if c[0] == "or_"]
    return f"or({expr})"


WAITING = {"status": "needs_agent", "mode": "ai", "assigned_agent_id": None, "queued_at": "2026-11-08T20:00:00+00:00",
           "visitor_name": "Grace Miller", "visitor_email": "grace@example.com", "visitor_phone": "+971500000000",
           "metadata": {"engaged_agent_id": None}}


@pytest.mark.parametrize("term", ["grace@", "example.com", "+97150"])
def test_an_agents_search_cant_find_a_waiting_chat_by_its_contact(term):
    assert not _holds(_search_filter(ME, term), WAITING), "the match itself would give the contact away"
    # Once it's theirs (or anyone's), the same search finds it.
    assert _holds(_search_filter(ME, term), dict(WAITING, assigned_agent_id=ME, status="active", mode="human"))
    assert _holds(_search_filter(ME, term), dict(WAITING, status="closed"))


def test_an_agents_search_still_finds_a_waiting_chat_by_name():
    # The gate shows who it is: a name match tells nothing new.
    assert _holds(_search_filter(ME, "grace mil"), WAITING)


def test_a_managers_search_matches_every_contact():
    expr = _search_filter(None, "grace@")
    assert _holds(expr, WAITING)
    assert expr == "or(visitor_name.ilike.%grace@%,visitor_email.ilike.%grace@%,visitor_phone.ilike.%grace@%)", \
        "the filter managers get is the one they had"


def test_an_agent_without_an_id_finds_no_waiting_chat_by_contact():
    # No id to be "theirs": the contact match falls to readable-by-anyone rows.
    assert not _holds(_search_filter("", "grace@"), WAITING)


@pytest.mark.parametrize("role,viewer", [("sales", ME), ("support", ME), ("owner", None), ("supervisor", None)])
def test_the_list_passes_the_searching_agent_down(role, viewer):
    lister = AsyncMock(return_value=([], 0))
    with patch.object(db, "get_conversations", new=lister), \
         patch("app.api.conversations._resolve_team_scope", new=AsyncMock(return_value=None)):
        r = _client(role).get("/api/conversations?search=grace")
    assert r.status_code == 200
    assert lister.await_args.kwargs["blind_viewer"] == viewer


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


def _drive(user: dict, seen: AsyncMock, *waves: list, during=None, typing_every=None):
    """Open the panel stream and feed it `waves` of events: each wave lands in
    its queue after the stream finished with the one before (the first is
    there when it opens). `during(queue)` runs alongside, for events that
    arrive while the stream waits. Settings are production's unless
    `typing_every` is given. Returns every frame the stream sent and the
    release of its subscription."""
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
        every = agent_api._TYPING_EVERY_SECONDS if typing_every is None else typing_every
        with patch.object(db, "get_conversation_simple", new=AsyncMock(return_value={"id": "c1", "tunnel": "sales"})), \
             patch.object(db, "get_blind_inputs", new=seen), \
             patch.object(agent_api, "_TYPING_EVERY_SECONDS", every), \
             patch.object(manager, "connect", new=AsyncMock(return_value=q)), \
             patch.object(manager, "disconnect") as release:
            res = await agent_api.agent_stream("c1", request, user=user)
            side = asyncio.create_task(during(q)) if during else None
            frames = [chunk async for chunk in res.body_iterator]
            if side:
                await side
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


def test_the_agents_own_chat_is_checked_mid_stream_too():
    seen = AsyncMock(return_value=MINE)
    frames, _ = _drive(_user("sales"), seen, [M1], [M2])
    assert frames == [_frame(M1), _frame(M2)]
    assert seen.await_count == 3, "the door, then each batch: it can be given away mid-stream"


def test_a_stream_on_my_chat_closes_once_it_changed_hands_and_went_back_to_the_line():
    M3 = {"id": "m3", "role": "user", "content": "Actually, make it Doha"}
    seen = AsyncMock(side_effect=[
        MINE,                                                              # the door
        MINE,                                                              # M1
        _row(assigned_agent_id=OTHER, engaged_agent_id=OTHER, status="active", mode="human"),  # M2
        REQUEUED,                                                          # M3
    ])
    frames, release = _drive(_user("sales"), seen, [M1], [M2], [M3])
    assert frames == [_frame(M1), _frame(M2), _frame({"event": "blind"})]
    assert "doha" not in "".join(frames).lower()
    release.assert_called_once()


def test_live_typing_is_gathered_so_it_costs_at_most_two_checks_a_second():
    import asyncio
    T2 = {"event": "typing", "is_typing": True, "text": "Do you fly to Duba"}
    T3 = {"event": "typing", "is_typing": True, "text": "Do you fly to Dubai?"}

    async def keeps_typing(q):
        await asyncio.sleep(0.05)   # arrives while the stream waits on T2
        q.put_nowait(T3)

    seen = AsyncMock(return_value=MINE)
    frames, _ = _drive(_user("sales"), seen, [TYPING], [T2], during=keeps_typing, typing_every=0.3)
    assert frames == [_frame(TYPING), _frame(T2), _frame(T3)]
    assert seen.await_count == 3, "the door, the first typing, then ONE check for T2 and T3"


def test_a_message_ends_the_typing_wait_at_once():
    import asyncio
    import time

    async def says_something(q):
        await asyncio.sleep(0.05)
        q.put_nowait(M2)

    seen = AsyncMock(return_value=MINE)
    t0 = time.monotonic()
    frames, _ = _drive(_user("sales"), seen, [TYPING], [TYPING], during=says_something, typing_every=5.0)
    assert time.monotonic() - t0 < 2.0, "a message never waits out the typing window"
    assert frames == [_frame(TYPING), _frame(TYPING), _frame(M2)]
    assert seen.await_count == 3


def test_gathered_typing_still_goes_out_only_after_a_check():
    import asyncio

    async def keeps_typing(q):
        await asyncio.sleep(0.05)
        q.put_nowait(TYPING)

    # It went back to the line while the stream gathered typing: none of it goes out.
    seen = AsyncMock(side_effect=[THEIRS, THEIRS, _row()])
    frames, _ = _drive(_user("sales"), seen, [M1], [TYPING], during=keeps_typing, typing_every=0.3)
    assert frames == [_frame(M1), _frame({"event": "blind"})]


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


def test_the_check_also_reads_the_tunnel_and_the_team():
    # A task linking a chat checks the whole door from this one row (tasks.py).
    q = _Q()
    with patch.object(db, "_queued_at_column_available", return_value=True):
        _run(q)
    assert "tunnel" in q.selects[0] and "team_id" in q.selects[0]


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
