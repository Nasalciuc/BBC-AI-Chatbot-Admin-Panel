"""Your day — the operator's own numbers, built only from data the server keeps.

What is pinned here:
  * the first reply is measured from the messages, from the take to the
    operator's first message (the `first_response` action is logged too
    rarely to count on);
  * a chat that went back to the assistant before the operator answered is
    not "theirs" for the CRM count;
  * days are cut at the operator's local midnight (fixed UTC offset);
  * a failed read is a 502, never a page of zeros passed off as the truth;
  * the heartbeat tells the panel the deadline the sweep enforces;
  * list previews: one request, no system lines, never for supervisors or
    PMs, and a failed read still serves the list.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.db import supabase as db  # noqa: E402
from app.security.auth import get_current_user  # noqa: E402
from app.services.agent_day import (  # noqa: E402
    build_agent_day,
    local_day_start,
    window_start,
)

ME = "11111111-1111-1111-1111-111111111111"
NOW = datetime(2026, 11, 6, 12, 30, tzinfo=timezone.utc)  # 14:30 in Chișinău (+120)
OFFSET = 120


def _at(hours_ago: float = 0, minutes_ago: float = 0) -> str:
    return (NOW - timedelta(hours=hours_ago, minutes=minutes_ago)).isoformat()


def _day(**over):
    base = dict(
        now=NOW, offset_minutes=OFFSET, days=1,
        activity=[], agent_messages=[], conversations={}, leads={}, open_conversations=[],
    )
    base.update(over)
    return build_agent_day(**base)


# ── the pure shape ───────────────────────────────────────────────────


def test_local_day_starts_at_the_operators_midnight():
    # 12:30 UTC is 14:30 at +120 → local midnight is 22:00 UTC the day before.
    assert local_day_start(NOW, OFFSET) == datetime(2026, 11, 5, 22, 0, tzinfo=timezone.utc)
    # The window always covers two weeks: this week against the one before.
    assert window_start(NOW, OFFSET, 1) == datetime(2026, 10, 23, 22, 0, tzinfo=timezone.utc)
    assert window_start(NOW, OFFSET, 30) == datetime(2026, 10, 7, 22, 0, tzinfo=timezone.utc)


def test_first_reply_is_measured_from_the_take_to_the_first_operator_message():
    activity = [
        {"action": "claim_won", "conversation_id": "a", "happened_at": _at(minutes_ago=30), "response_seconds": 9},
        {"action": "claim_won", "conversation_id": "b", "happened_at": _at(minutes_ago=20), "response_seconds": 15},
    ]
    msgs = [
        # An operator line from BEFORE the take never counts.
        {"conversation_id": "a", "created_at": _at(minutes_ago=31)},
        {"conversation_id": "a", "created_at": (NOW - timedelta(minutes=30) + timedelta(seconds=41)).isoformat()},
        {"conversation_id": "b", "created_at": (NOW - timedelta(minutes=20) + timedelta(seconds=61)).isoformat()},
    ]
    d = _day(activity=activity, agent_messages=msgs)
    assert d["totals"]["took"] == 2
    assert d["totals"]["first_reply_median_s"] == 51  # median of 41 and 61
    assert d["totals"]["wait_median_s"] == 12          # median of 9 and 15


def test_a_reply_counts_only_inside_its_own_take():
    # Taken, back to the assistant with no reply, taken again, answered 30 s
    # after the second take: the first take had no reply (not ten minutes).
    t1 = NOW - timedelta(minutes=40)
    t2 = NOW - timedelta(minutes=30)
    activity = [
        {"action": "claim_won", "conversation_id": "a", "happened_at": t1.isoformat()},
        {"action": "deadline_fired", "conversation_id": "a", "happened_at": (t1 + timedelta(seconds=90)).isoformat()},
        {"action": "claim_won", "conversation_id": "a", "happened_at": t2.isoformat()},
    ]
    msgs = [{"conversation_id": "a", "created_at": (t2 + timedelta(seconds=30)).isoformat()}]
    d = _day(activity=activity, agent_messages=msgs, leads={"a": {"created_in_crm": False}})
    assert d["totals"]["took"] == 2
    assert d["totals"]["first_reply_median_s"] == 30
    assert d["totals"]["went_back"] == 1


def test_a_reply_after_the_chat_went_back_is_not_mine():
    # The messages read covers every operator in the chat: after it went
    # back, someone else took it and wrote. That line isn't my first reply,
    # and the chat isn't mine for the CRM count.
    t1 = NOW - timedelta(minutes=40)
    activity = [
        {"action": "claim_won", "conversation_id": "a", "happened_at": t1.isoformat()},
        {"action": "deadline_fired", "conversation_id": "a", "happened_at": (t1 + timedelta(seconds=90)).isoformat()},
    ]
    msgs = [{"conversation_id": "a", "created_at": (t1 + timedelta(minutes=5)).isoformat()}]
    d = _day(activity=activity, agent_messages=msgs, leads={"a": {"created_in_crm": False}})
    assert d["totals"]["first_reply_median_s"] is None
    assert (d["totals"]["with_lead"], d["totals"]["in_crm"]) == (0, 0)


def test_went_back_is_listed_newest_first_and_not_counted_as_yours_for_the_crm():
    activity = [
        {"action": "claim_won", "conversation_id": "sarah", "happened_at": _at(minutes_ago=16)},
        {"action": "deadline_fired", "conversation_id": "sarah", "happened_at": _at(minutes_ago=14)},
        {"action": "claim_won", "conversation_id": "mike", "happened_at": _at(minutes_ago=50)},
    ]
    msgs = [{"conversation_id": "mike", "created_at": _at(minutes_ago=49)}]
    convs = {
        "sarah": {"id": "sarah", "visitor_name": "Sarah Collins"},
        "mike": {"id": "mike", "visitor_name": "Michael Turner", "visitor_phone": "+1 555", "visitor_email": "m@x.com", "status": "active"},
    }
    leads = {"sarah": {"created_in_crm": False}, "mike": {"created_in_crm": False}}
    d = _day(activity=activity, agent_messages=msgs, conversations=convs, leads=leads)
    assert d["totals"]["went_back"] == 1
    assert d["went_back"][0]["first_name"] == "Sarah"
    # Sarah never got an answer from me: not mine for the CRM count.
    assert (d["totals"]["with_lead"], d["totals"]["in_crm"]) == (1, 0)
    assert [n["first_name"] for n in d["not_in_crm"]] == ["Michael"]
    assert d["not_in_crm"][0]["has_phone"] and d["not_in_crm"][0]["has_email"]


def test_in_the_crm_counts_whoever_sent_it():
    activity = [{"action": "claim_won", "conversation_id": "j", "happened_at": _at(minutes_ago=90)}]
    msgs = [{"conversation_id": "j", "created_at": _at(minutes_ago=89)}]
    d = _day(activity=activity, agent_messages=msgs, leads={"j": {"created_in_crm": True}})
    assert (d["totals"]["with_lead"], d["totals"]["in_crm"]) == (1, 1)
    assert d["not_in_crm"] == []


def test_waiting_on_you_and_the_client_who_left():
    open_convs = [
        {"id": "d", "visitor_name": "David Kim", "last_user_message_at": _at(minutes_ago=32),
         "last_agent_message_at": _at(minutes_ago=40),
         "metadata": {"widget_presence": "left", "widget_last_event_at": _at(minutes_ago=3)}},
        # Answered after the client's last line: not waiting on me.
        {"id": "m", "visitor_name": "Michael Turner", "last_user_message_at": _at(minutes_ago=5),
         "last_agent_message_at": _at(minutes_ago=4), "metadata": {}},
    ]
    d = _day(open_conversations=open_convs)
    assert d["totals"]["still_open"] == 2
    assert d["waiting_on_you"] == [
        {"conversation_id": "d", "first_name": "David", "minutes": 32, "left_minutes_ago": 3}
    ]


def test_days_are_cut_at_local_midnight_and_the_table_has_at_least_a_week():
    activity = [
        # 23:30 local yesterday (21:30 UTC) and 00:30 local today (22:30 UTC).
        {"action": "closed", "conversation_id": "x", "happened_at": datetime(2026, 11, 5, 21, 30, tzinfo=timezone.utc).isoformat()},
        {"action": "closed", "conversation_id": "y", "happened_at": datetime(2026, 11, 5, 22, 30, tzinfo=timezone.utc).isoformat()},
    ]
    d = _day(activity=activity)
    assert d["totals"]["closed"] == 1
    assert len(d["by_day"]) == 7
    assert d["by_day"][0]["date"] == "2026-11-06" and d["by_day"][0]["closed"] == 1
    assert d["by_day"][1]["date"] == "2026-11-05" and d["by_day"][1]["closed"] == 1
    assert len(_day(days=30)["by_day"]) == 30


def test_week_over_week_first_reply():
    def claim(days_ago, cid):
        return {"action": "claim_won", "conversation_id": cid, "happened_at": _at(hours_ago=24 * days_ago)}

    def reply(days_ago, cid, s):
        return {"conversation_id": cid, "created_at": (NOW - timedelta(hours=24 * days_ago) + timedelta(seconds=s)).isoformat()}

    activity = [claim(1, "a"), claim(9, "b")]
    msgs = [reply(1, "a", 41), reply(9, "b", 52)]
    d = _day(activity=activity, agent_messages=msgs)
    assert d["first_reply_week"] == {"this": 41, "previous": 52}


def test_unreadable_rows_are_left_out_not_fatal():
    d = _day(activity=[{"action": "claim_won", "conversation_id": "a", "happened_at": "not a date"}])
    assert d["totals"]["took"] == 0


# ── the endpoint ─────────────────────────────────────────────────────


def _client(role: str = "sales") -> TestClient:
    from app.api.agent import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": ME, "role": role, "tunnel_scope": "sales"}
    return TestClient(app)


def test_endpoint_refuses_other_ranges():
    r = _client().get("/api/agent/day?days=3")
    assert r.status_code == 422


def test_a_failed_read_is_a_502_never_zeros():
    with patch.object(db, "get_agent_day_inputs", new=AsyncMock(side_effect=RuntimeError("boom"))):
        r = _client().get("/api/agent/day?days=7&tz_offset_minutes=120")
    assert r.status_code == 502


def test_endpoint_reads_only_the_callers_rows_and_shapes_the_page():
    inputs = dict(activity=[], agent_messages=[], conversations={}, leads={}, open_conversations=[])
    with patch.object(db, "get_agent_day_inputs", new=AsyncMock(return_value=inputs)) as read:
        r = _client().get("/api/agent/day?days=7&tz_offset_minutes=120")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["days"] == 7
    assert read.await_args.args[0] == ME


def test_heartbeat_carries_the_deadline_the_sweep_enforces():
    from app.api import agent as agent_api
    from config.settings import settings

    user_row = {"id": ME, "role": "sales", "is_ready": False, "is_active": True, "chat_enabled": True, "tunnel_scope": "sales"}
    with patch.object(db, "update_user_last_seen", new=AsyncMock()), \
         patch.object(db, "get_user_by_id", new=AsyncMock(return_value=user_row)), \
         patch.object(db, "get_queue_for_operator", new=AsyncMock(return_value=[])), \
         patch("app.services.presence.record_presence_tick", new=AsyncMock()):
        r = _client().post("/api/agent/heartbeat", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["response_deadline_seconds"] == settings.agent_first_response_timeout_seconds
    assert body["viewing_grace_seconds"] == agent_api.VIEWING_GRACE_SECONDS == 120


# ── list previews ────────────────────────────────────────────────────


class _PreviewQuery:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a, k))
            return self
        return rec

    def execute(self):
        res = MagicMock()
        res.data = self.rows
        return res


def test_previews_are_one_request_without_system_lines_and_trimmed():
    """The real postgrest-py builder, stopped at execute(): the parameters
    PostgREST receives. The order is INSIDE each chat's messages
    (`messages.order`). The related order (`order=messages(created_at)`) sorts
    the chats by a to-one relation, and PostgREST answers it with a 400 for
    messages — every previews read failed that way in production."""
    import asyncio

    from postgrest import SyncPostgrestClient
    from postgrest._sync.request_builder import SyncSelectRequestBuilder

    rows = [
        {"id": "a", "messages": [{"role": "user", "content": "Thanks.  Morning\ndepartures if possible.", "created_at": "t"}]},
        {"id": "b", "messages": []},
        {"id": "c", "messages": [{"role": "ai", "content": "x" * 300, "created_at": "t"}]},
    ]
    sent: list[list[tuple[str, str]]] = []

    def execute(self):
        sent.append(list(self.params.multi_items()))
        return MagicMock(data=rows)

    rest = SyncPostgrestClient("http://rest.invalid/rest/v1")
    client = MagicMock()
    client.table.side_effect = rest.from_
    with patch.object(SyncSelectRequestBuilder, "execute", execute), \
         patch.object(db, "get_client", return_value=client):
        out = asyncio.run(db.get_last_message_previews(["a", "b", "c"]))
    assert out["a"]["content"] == "Thanks. Morning departures if possible."
    assert "b" not in out
    assert len(out["c"]["content"]) == 140 and out["c"]["content"].endswith("…")
    assert len(sent) == 1
    keys = [k for k, _ in sent[0]]
    params = dict(sent[0])
    assert keys.count("messages.order") == 1 and "order" not in keys
    assert params["messages.order"] == "created_at.desc"
    assert params["messages.limit"] == "1" and params["messages.role"] == "neq.system"
    assert params["id"] == "in.(a,b,c)"
    assert params["select"] == "id, messages(role, content, created_at)"


def _list_client(role: str) -> TestClient:
    from app.api.conversations import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": ME, "role": role, "tunnel_scope": "sales"}
    return TestClient(app)


ROWS = [{"id": "a", "tunnel": "sales", "visitor_name": "David Kim", "assigned_agent_id": ME, "metadata": {}}]


@pytest.mark.parametrize("role", ["sales", "owner", "qa"])
def test_list_rows_carry_the_last_message(role):
    previews = {"a": {"role": "user", "content": "Thanks.", "created_at": "t"}}
    with patch.object(db, "get_conversations", new=AsyncMock(return_value=([dict(r) for r in ROWS], 1))), \
         patch.object(db, "get_last_message_previews", new=AsyncMock(return_value=previews)):
        r = _list_client(role).get("/api/conversations?assigned_to=me&status=active")
    assert r.status_code == 200
    assert r.json()["data"][0]["last_message"]["content"] == "Thanks."


@pytest.mark.parametrize("role", ["supervisor", "project_manager"])
def test_no_previews_for_masked_or_team_scoped_readers(role):
    reader = AsyncMock(return_value={})
    with patch.object(db, "get_conversations", new=AsyncMock(return_value=([dict(r) for r in ROWS], 1))), \
         patch("app.api.conversations._resolve_team_scope", new=AsyncMock(return_value=["t1"])), \
         patch.object(db, "get_last_message_previews", new=reader):
        r = _list_client(role).get("/api/conversations?status=active")
    assert r.status_code == 200
    assert "last_message" not in r.json()["data"][0]
    reader.assert_not_awaited()


OTHER = "22222222-2222-2222-2222-222222222222"
ROUTE = {"from": "BOS", "to": "DXB"}


def _mixed_rows():
    return [
        {"id": "mine", "assigned_agent_id": ME, "metadata": {}, "route": dict(ROUTE)},
        {"id": "waiting", "assigned_agent_id": None, "metadata": {}, "route": dict(ROUTE)},
        # Went back to the assistant after I wrote in it: still mine to read.
        {"id": "engaged", "assigned_agent_id": None, "metadata": {"engaged_agent_id": ME}, "route": dict(ROUTE)},
        {"id": "theirs", "assigned_agent_id": OTHER, "metadata": {}, "route": dict(ROUTE)},
    ]


@pytest.mark.parametrize("role", ["sales", "support"])
def test_an_agent_gets_no_cities_and_no_words_from_a_chat_that_is_not_theirs(role):
    said = {"role": "user", "content": "Boston to Dubai, two of us", "created_at": "t"}
    reader = AsyncMock(side_effect=lambda ids: {i: dict(said) for i in ids})
    with patch.object(db, "get_conversations", new=AsyncMock(return_value=(_mixed_rows(), 4))), \
         patch.object(db, "get_last_message_previews", new=reader):
        r = _list_client(role).get("/api/conversations?assigned_to=none&status=active")
    assert r.status_code == 200
    rows = {c["id"]: c for c in r.json()["data"]}
    for cid in ("mine", "engaged"):
        assert rows[cid]["route"] == ROUTE
        assert rows[cid]["last_message"]["content"] == said["content"]
    for cid in ("waiting", "theirs"):
        assert "route" not in rows[cid], f"{cid}: the agent would know where the client flies"
        assert rows[cid]["last_message"] is None, f"{cid}: the client's own words name the cities"
    # The words are never read for the blind rows, not just dropped afterwards.
    assert sorted(reader.await_args.args[0]) == ["engaged", "mine"]


@pytest.mark.parametrize("role", ["owner", "admin", "dev", "qa"])
def test_people_who_give_chats_out_still_see_where_a_waiting_client_flies(role):
    said = {"role": "user", "content": "Boston to Dubai", "created_at": "t"}
    with patch.object(db, "get_conversations", new=AsyncMock(return_value=(_mixed_rows(), 4))), \
         patch.object(db, "get_last_message_previews", new=AsyncMock(side_effect=lambda ids: {i: dict(said) for i in ids})):
        r = _list_client(role).get("/api/conversations?status=active")
    rows = {c["id"]: c for c in r.json()["data"]}
    assert all(rows[cid]["route"] == ROUTE for cid in rows)
    assert all(rows[cid]["last_message"]["content"] == said["content"] for cid in rows)


def test_an_agent_with_only_waiting_rows_reads_no_previews_at_all():
    reader = AsyncMock(return_value={})
    rows = [r for r in _mixed_rows() if r["id"] in ("waiting", "theirs")]
    with patch.object(db, "get_conversations", new=AsyncMock(return_value=(rows, 2))), \
         patch.object(db, "get_last_message_previews", new=reader):
        r = _list_client("sales").get("/api/conversations?assigned_to=none&status=active")
    assert r.status_code == 200
    assert [c.get("route") for c in r.json()["data"]] == [None, None]
    reader.assert_not_awaited()


def test_a_failed_preview_read_still_serves_the_list():
    with patch.object(db, "get_conversations", new=AsyncMock(return_value=([dict(r) for r in ROWS], 1))), \
         patch.object(db, "get_last_message_previews", new=AsyncMock(side_effect=RuntimeError("boom"))):
        r = _list_client("sales").get("/api/conversations?assigned_to=me&status=active")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["data"][0]["id"] == "a"
    assert "last_message" not in body["data"][0]


# ── list rows: route and the deadline's start, without new queries ──


def test_list_rows_carry_the_route_and_when_the_chat_became_yours():
    import asyncio

    rows = [{"id": "a", "mode": "human", "assigned_agent_id": ME, "engaged_agent_id": None,
             "agent_assigned_at": "2026-11-06T12:00:00+00:00", "queued_at": "2026-11-06T11:58:00+00:00"},
            {"id": "b", "mode": "ai", "assigned_agent_id": None, "engaged_agent_id": None}]
    q = _PreviewQuery(rows)
    q.count = 2

    def execute():
        res = MagicMock()
        res.data = [dict(r) for r in rows]
        res.count = 2
        return res

    q.execute = execute
    client = MagicMock()
    client.table.return_value = q
    leads = {"a": {"origin_code": "JFK", "destination_code": "LHR"}, "b": {"origin_code": "BOS"}}
    with patch.object(db, "get_client", return_value=client), \
         patch.object(db, "_supervisor_columns_available", return_value=True), \
         patch.object(db, "_get_lead_completeness_inputs", new=AsyncMock(return_value=leads)), \
         patch.object(db, "enrich_conversations_agent_info", new=AsyncMock(side_effect=lambda r: r)):
        out, _ = asyncio.run(db.get_conversations())
    a, b = out
    assert a["route"] == {"from": "JFK", "to": "LHR"}
    assert "route" not in b  # half a route is no route
    assert a["metadata"]["agent_assigned_at"] == "2026-11-06T12:00:00+00:00"
    assert a["queued_at"] == "2026-11-06T11:58:00+00:00"
    select = next(c for c in q.calls if c[0] == "select")[1][0]
    assert "agent_assigned_at:metadata->>agent_assigned_at" in select and "queued_at" in select
