"""The list's filter row: days in New York, contact, agent (managers only), source, the lead's airports
and CRM, each held to rows the way PostgREST reads the query; for an agent the trip and the source keep
only chats they may read. Every name here is invented."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import conversations as conv_api  # noqa: E402
from app.db import supabase as db  # noqa: E402
from app.security.auth import get_current_user  # noqa: E402
from app.services import chat_search  # noqa: E402
from tests.postgrest_eval import query_keeps  # noqa: E402

ME = "aaaaaaaa-0000-4000-8000-000000000001"
OTHER = "aaaaaaaa-0000-4000-8000-000000000002"


@pytest.fixture(autouse=True)
def _fresh_route_cache():
    conv_api._route_ids_cache.clear()
    yield
    conv_api._route_ids_cache.clear()


# ── the endpoint's reading of the row ─────────────────────────────


def test_days_are_new_york_days_inclusive():
    f = conv_api._list_filters({"role": "owner"}, date_from="2026-11-20", date_to="2026-11-20", has_contact=None,
                               agent=None, source=None, route_from=None, route_to=None, crm=None)
    assert f == {"created_from": "2026-11-20T05:00:00+00:00", "created_to": "2026-11-21T05:00:00+00:00"}


def test_a_day_across_the_clock_change_is_still_a_new_york_day():
    f = conv_api._list_filters({"role": "owner"}, date_from="2026-11-01", date_to="2026-11-01", has_contact=None,
                               agent=None, source=None, route_from=None, route_to=None, crm=None)
    assert f["created_from"] == "2026-11-01T04:00:00+00:00" and f["created_to"] == "2026-11-02T05:00:00+00:00"


@pytest.mark.parametrize("kw, status", [
    ({"date_from": "2026-11-21", "date_to": "2026-11-20"}, 400),
    ({"date_from": "2026-02-30"}, 400),
    ({"date_to": "9999-12-31"}, 400),
    ({"agent": "not-a-uuid"}, 400),
])
def test_a_wrong_filter_is_refused(kw, status):
    args = dict(date_from=None, date_to=None, has_contact=None, agent=None, source=None, route_from=None,
                route_to=None, crm=None)
    args.update(kw)
    with pytest.raises(HTTPException) as e:
        conv_api._list_filters({"role": "owner"}, **args)
    assert e.value.status_code == status


@pytest.mark.parametrize("role", ["sales", "support"])
def test_an_agent_cannot_filter_by_agent(role):
    with pytest.raises(HTTPException) as e:
        conv_api._list_filters({"role": role, "id": ME}, date_from=None, date_to=None, has_contact=None, agent=OTHER,
                               source=None, route_from=None, route_to=None, crm=None)
    assert e.value.status_code == 403


def _client(role, user_id=ME):
    app = FastAPI()
    app.include_router(conv_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "role": role, "tunnel_scope": "sales"}
    return TestClient(app)


def _list(role, query, *, route_ids=None, route_error=None):
    lister = AsyncMock(return_value=([], 0))
    routes = AsyncMock(return_value=route_ids or [], side_effect=route_error)
    with patch.object(db, "get_conversations", new=lister), \
            patch.object(db, "get_route_conversation_ids", new=routes), \
            patch("app.api.conversations._resolve_team_scope", new=AsyncMock(return_value=None)):
        r = _client(role).get(f"/api/conversations?{query}")
    return r, lister, routes


def test_the_row_reaches_the_query():
    r, lister, _ = _list("owner", f"date_from=2026-11-20&has_contact=false&agent={OTHER}&source=ads"
                                  "&route_from=JFK&route_to=IST&crm=out")
    assert r.status_code == 200
    assert lister.await_args.kwargs["filters"] == {
        "created_from": "2026-11-20T05:00:00+00:00", "has_contact": False, "agent": OTHER, "source": "ads",
        "route_from": "JFK", "route_to": "IST", "crm": "out"}


def test_no_filter_is_no_filter():
    """Rule 5: the list without the row asks for nothing new."""
    r, lister, routes = _list("sales", "assigned_to=me")
    assert r.status_code == 200 and lister.await_args.kwargs["filters"] == {}
    assert lister.await_args.kwargs["search_route_ids"] is None
    routes.assert_not_awaited()


@pytest.mark.parametrize("query", ["source=tv", "route_from=jfk", "crm=maybe", "date_from=20-11-2026"])
def test_a_value_outside_the_row_is_a_422(query):
    assert _list("owner", query)[0].status_code == 422


def test_a_city_search_reads_the_routes_once_per_half_minute():
    r, lister, routes = _list("sales", "search=Istanbul", route_ids=["aaaaaaaa-0000-4000-8000-0000000000aa"])
    assert r.status_code == 200
    assert lister.await_args.kwargs["search_route_ids"] == ["aaaaaaaa-0000-4000-8000-0000000000aa"]
    r, lister, routes = _list("sales", "search=istanbul", route_ids=["should-not-be-read"])
    routes.assert_not_awaited()
    assert lister.await_args.kwargs["search_route_ids"] == ["aaaaaaaa-0000-4000-8000-0000000000aa"]


@pytest.mark.parametrize("search", ["#3403", "grace@example.com", "+1 214 208 5185", "IST", "Is"])
def test_only_a_text_of_three_letters_or_more_reads_the_routes(search):
    from urllib.parse import quote

    r, lister, routes = _list("sales", f"search={quote(search)}")
    assert r.status_code == 200
    routes.assert_not_awaited()
    assert lister.await_args.kwargs["search_route_ids"] is None


def test_a_failed_route_read_serves_the_list_without_cities_and_counts():
    before = chat_search.SEARCH_HEALTH["route_read_failed"]
    r, lister, _ = _list("sales", "search=Istanbul", route_error=RuntimeError("db down"))
    assert r.status_code == 200 and r.json()["success"] is True
    assert lister.await_args.kwargs["search_route_ids"] is None
    assert chat_search.SEARCH_HEALTH["route_read_failed"] == before + 1


# ── the query, held to rows ─────────────────────────────────────


class _Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a, k))
            return self
        return rec

    def execute(self):
        res = MagicMock()
        res.data, res.count = [], 0
        return res


def _keeps(filters, row, *, viewer=None):
    q = _Recorder()
    client = MagicMock()
    client.table.return_value = q
    with patch.object(db, "get_client", return_value=client), \
            patch.object(db, "_supervisor_columns_available", return_value=True), \
            patch.object(db, "_get_lead_completeness_inputs", new=AsyncMock(return_value={})), \
            patch.object(db, "enrich_conversations_agent_info", new=AsyncMock(side_effect=lambda r: r)):
        asyncio.run(db.get_conversations(filters=filters, blind_viewer=viewer))
    return query_keeps(q.calls, row), q.calls


def _chat(**kw):
    base = {"id": "c1", "status": "closed", "mode": "ai", "assigned_agent_id": None, "queued_at": None,
            "created_at": "2026-11-20T12:00:00+00:00", "visitor_email": None, "visitor_phone": None,
            "metadata": {}, "leads": []}
    base.update(kw)
    return base


@pytest.mark.parametrize("row, has", [
    (_chat(visitor_email="dana@example.com"), True),
    (_chat(visitor_phone="+1 555 0100"), True),
    (_chat(metadata={"phone_to_check": "0722 000 000"}), True),
    (_chat(), False),
    (_chat(visitor_email="", visitor_phone="", metadata={"phone_to_check": ""}), False),
    (_chat(metadata=None), False),
    (_chat(metadata={"phone_to_check": "   "}), False),
    (_chat(visitor_email=" \t ", visitor_phone="  "), False),
    (_chat(visitor_email="  dana@example.com  "), True),
])
def test_has_contact_is_the_rows_own_has_contact(row, has):
    assert _keeps({"has_contact": True}, row)[0] is has
    assert _keeps({"has_contact": False}, row)[0] is (not has)
    from app.api.conversations import _has_contact

    assert _has_contact(row) is has, "the filter and the row's own answer agree"


def test_the_days_bound_the_creation_time():
    f = {"created_from": "2026-11-20T05:00:00+00:00", "created_to": "2026-11-21T05:00:00+00:00"}
    assert _keeps(f, _chat(created_at="2026-11-20T05:00:00+00:00"))[0]
    assert not _keeps(f, _chat(created_at="2026-11-21T05:00:00+00:00"))[0]
    assert not _keeps(f, _chat(created_at="2026-11-20T04:59:59+00:00"))[0]


@pytest.mark.parametrize("row, kept", [
    (_chat(assigned_agent_id=OTHER), True),
    (_chat(metadata={"closed_by_agent_id": OTHER}), True),
    (_chat(metadata={"engaged_agent_id": OTHER}), True),
    (_chat(assigned_agent_id=ME), False),
    (_chat(), False),
])
def test_the_agent_filter_is_the_agents_chats(row, kept):
    assert _keeps({"agent": OTHER}, row)[0] is kept


def test_the_lead_filters_join_the_lead():
    row = _chat(leads=[{"origin_code": "JFK", "destination_code": "IST", "created_in_crm": False}])
    assert _keeps({"route_from": "JFK", "route_to": "IST", "crm": "out"}, row)[0]
    assert not _keeps({"route_from": "JFK", "crm": "in"}, row)[0]
    assert not _keeps({"route_to": "JFK"}, row)[0]
    assert not _keeps({"route_from": "JFK"}, _chat())[0], "a chat without a lead never matches the trip"
    _, calls = _keeps({"route_from": "JFK"}, row)
    assert "leads!inner(id)" in [c for c in calls if c[0] == "select"][0][1][0]


WAITING_WITH_TRIP = _chat(status="needs_agent", queued_at="2026-11-20T20:00:00+00:00",
                          leads=[{"origin_code": "JFK", "destination_code": "IST", "created_in_crm": False}],
                          metadata={"utm_source": "kayak", "utm_medium": "cpc"})


@pytest.mark.parametrize("filters", [{"route_to": "IST"}, {"crm": "out"}, {"source": "kayak"}])
def test_an_agent_cannot_sort_waiting_chats_by_what_the_blind_take_hides(filters):
    assert not _keeps(filters, WAITING_WITH_TRIP, viewer=ME)[0]
    taken = dict(WAITING_WITH_TRIP, status="active", mode="human", assigned_agent_id=ME, queued_at=None)
    assert _keeps(filters, taken, viewer=ME)[0]
    assert _keeps(filters, WAITING_WITH_TRIP, viewer=None)[0], "a manager is never blind"


@pytest.mark.parametrize("filters", [{"has_contact": False}, {"created_from": "2026-11-20T05:00:00+00:00"}])
def test_what_the_row_already_shows_needs_no_guard(filters):
    """Rule 5: whether there is a contact, and when the chat began, the list row says for every chat."""
    assert _keeps(filters, WAITING_WITH_TRIP, viewer=ME)[0]


@pytest.mark.parametrize("source, meta", [
    ("kayak", {"utm_source": "kayak", "utm_medium": "cpc"}),
    ("ads", {"utm_source": "bing", "utm_medium": "cpc", "msclkid": "m-1"}),
    ("ads", {"gclid": "g-1"}),
    ("site", {"utm_source": "newsletter", "utm_medium": "email"}),
    ("site", {}),
])
def test_the_source_chip_reaches_the_query(source, meta):
    row = _chat(metadata=meta)
    for chip in chat_search.SOURCES:
        assert _keeps({"source": chip}, row)[0] is (chip == source), (chip, meta)


# ── CodeRabbit #273: the guard and the narrowed message search, held to rows ──


class _MsgRecorder(_Recorder):
    pass


def _message_calls(**kw):
    q = _MsgRecorder()
    client = MagicMock()
    client.table.return_value = q
    with patch.object(db, "get_client", return_value=client), \
            patch.object(db, "_queued_at_column_available", return_value=True):
        asyncio.run(db.search_messages("Lisbon", since_iso="2026-11-01T00:00:00+00:00", **kw))
    return q.calls


@pytest.mark.parametrize("conv, kept", [
    ({"tunnel": "sales", "status": "closed", "assigned_agent_id": OTHER}, True),
    ({"tunnel": "support", "status": "closed", "assigned_agent_id": OTHER}, False),
    ({"tunnel": "sales", "status": "needs_agent", "mode": "ai", "assigned_agent_id": None}, False),
    ({"tunnel": "sales", "status": "active", "mode": "ai", "assigned_agent_id": None,
      "queued_at": "2026-11-20T09:00:00+00:00"}, False),
    ({"tunnel": "sales", "status": "active", "mode": "human", "assigned_agent_id": None,
      "metadata": {"engaged_agent_id": ME}}, True),
])
def test_the_message_search_keeps_only_what_the_agent_may_read_before_its_limit(conv, kept):
    calls = _message_calls(tunnel="sales", blind_viewer=ME)
    select = next(a[0] for n, a, _ in calls if n == "select")
    assert "conversations!inner(" in select
    row = {"role": "user", "content": "to Lisbon", "created_at": "2026-11-20T10:00:00+00:00",
           "conversations": [{"queued_at": None, "metadata": {}, "mode": "ai", **conv}]}
    assert query_keeps(calls, row, embed="conversations") is kept


def test_a_manager_s_message_search_is_not_narrowed():
    calls = _message_calls()
    select = next(a[0] for n, a, _ in calls if n == "select")
    assert "conversations" not in select and not any(n == "or_" for n, _, _ in calls)


def test_on_the_legacy_columns_a_chat_waiting_by_queued_at_stays_blind():
    """The legacy projection (the supervisor columns gone, 034's queued_at still there) keeps the queue."""
    waiting = _chat(status="active", assigned_agent_id=None, queued_at="2026-11-20T09:00:00+00:00",
                    metadata={"utm_source": "kayak"}, leads=[{"origin_code": "JFK", "destination_code": "IST"}])
    q = _Recorder()
    client = MagicMock()
    client.table.return_value = q
    with patch.object(db, "get_client", return_value=client), \
            patch.object(db, "_supervisor_columns_available", return_value=False), \
            patch.object(db, "_queued_at_column_available", return_value=True), \
            patch.object(db, "_get_lead_completeness_inputs", new=AsyncMock(return_value={})), \
            patch.object(db, "enrich_conversations_agent_info", new=AsyncMock(side_effect=lambda r: r)):
        asyncio.run(db.get_conversations(filters={"source": "kayak"}, blind_viewer=ME))
    assert query_keeps(q.calls, waiting) is False
