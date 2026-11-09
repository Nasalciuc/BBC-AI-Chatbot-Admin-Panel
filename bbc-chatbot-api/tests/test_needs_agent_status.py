"""needs_agent: the client asked for a person (migration 052).

Production refused every needs_agent write for months ('needs_agent' does not
fit varchar(10) and was outside the CHECK) while the logs said "queued". This
file pins the three things that make accepting it safe:

1. The write answers what happened — queued, not open, or failed — and a
   failure is counted in /health with the database's code, never the row.
2. Every read that means "open" sees needs_agent: the widget's restore, the
   visitor's open-chat lookup, the abandoned sweep (the CRM push), the queue
   list and the tab counts. A read that misses it loses exactly the client
   who asked for a human.
The migration itself (052) has its own file: test_migration_052.py.
"""

import logging
import os
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import supabase as sb  # noqa: E402
from app.services import routing  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OPEN = ["active", "needs_agent"]


class _DbError(Exception):
    """Shaped like PostgREST's APIError: a code, and a message that can carry the row."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Table:
    """Rows of one table; honours the filters these reads and writes use and
    records every query, so a test can say which statuses a read asked for."""

    def __init__(self, rows=None, raise_on_update=None, raise_on_select=None):
        self.rows = rows or []
        self.queries: list[dict] = []
        self.raise_on_update = raise_on_update
        self.raise_on_select = raise_on_select
        self.inserted: list[dict] = []

    def table(self, _name):
        return _Query(self)


class _Query:
    def __init__(self, t: _Table):
        self.t = t
        self.q = {"filters": [], "update": None, "insert": None, "select": None}
        t.queries.append(self.q)

    def select(self, cols, **_k):
        self.q["select"] = cols
        return self

    def update(self, payload):
        self.q["update"] = payload
        return self

    def insert(self, payload):
        self.q["insert"] = payload
        return self

    def eq(self, col, val):
        self.q["filters"].append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.q["filters"].append(("in", col, list(vals)))
        return self

    def is_(self, col, val):
        self.q["filters"].append(("is", col, val))
        return self

    def gte(self, col, val):
        self.q["filters"].append(("gte", col, val))
        return self

    def or_(self, expr):
        self.q["filters"].append(("or", expr, None))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def single(self):
        return self

    def _match(self, row):
        for kind, col, val in self.q["filters"]:
            have = row.get(col)
            if kind == "eq" and have != val:
                return False
            if kind == "in" and have not in val:
                return False
        return True

    def execute(self):
        if self.q["update"] is not None:
            if self.t.raise_on_update:
                raise self.t.raise_on_update
            hit = [r for r in self.t.rows if self._match(r)]
            for r in hit:
                r.update(self.q["update"])
            return MagicMock(data=[dict(r) for r in hit])
        if self.q["insert"] is not None:
            row = {"id": "new-conv", **self.q["insert"]}
            self.t.inserted.append(row)
            self.t.rows.append(row)
            return MagicMock(data=[dict(row)])
        if self.t.raise_on_select:
            raise self.t.raise_on_select
        return MagicMock(data=[dict(r) for r in self.t.rows if self._match(r)], count=0)


def _wire(table: _Table):
    return [
        patch.object(sb, "get_client", return_value=table),
        patch.object(sb, "_run_sync", new=AsyncMock(side_effect=lambda fn, *a, **k: fn())),
    ]


class _Patched:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


def _status_filters(table: _Table) -> list:
    return [f for q in table.queries for f in q["filters"] if f[1] == "status"]


@pytest.fixture(autouse=True)
def _fresh_counters():
    before = dict(routing.QUEUE_HEALTH)
    for k in ("queued", "not_open", "write_failed", "put_back_failed"):
        routing.QUEUE_HEALTH[k] = 0
    routing.QUEUE_HEALTH["last_failed_at"] = None
    routing.QUEUE_HEALTH["last_failed_code"] = None
    yield
    routing.QUEUE_HEALTH.update(before)


# ══════════════════════════════════════════════════════════════
# 1. The write says what happened
# ══════════════════════════════════════════════════════════════

class TestMarkNeedsAgent:
    @pytest.mark.asyncio
    async def test_an_open_conversation_is_queued(self):
        t = _Table([{"id": "c1", "status": "active"}])
        with _Patched(_wire(t)):
            assert await sb.mark_needs_agent("c1") == ("queued", "")
        assert t.rows[0]["status"] == "needs_agent"
        # Only an open conversation can be queued: a closed or completed one
        # is never reopened by a "talk to a person" turn.
        assert ("in", "status", OPEN) in t.queries[0]["filters"]

    @pytest.mark.asyncio
    async def test_queueing_twice_is_still_queued(self):
        t = _Table([{"id": "c1", "status": "needs_agent"}])
        with _Patched(_wire(t)):
            assert await sb.mark_needs_agent("c1") == ("queued", "")

    @pytest.mark.parametrize("status", ["closed", "completed", "pending"])
    @pytest.mark.asyncio
    async def test_a_conversation_that_is_not_open_is_not_a_failure(self, status):
        t = _Table([{"id": "c1", "status": status}])
        with _Patched(_wire(t)):
            assert await sb.mark_needs_agent("c1") == ("not_open", "")
        assert t.rows[0]["status"] == status

    @pytest.mark.asyncio
    async def test_a_refused_write_is_failed_with_the_code_and_never_the_row(self, caplog):
        refused = _DbError(
            "23514",
            'new row for relation "conversations" violates check constraint '
            '"conversations_status_check" — Failing row contains (c1, Jane Roe, '
            "jane@example.com, +12105550100)",
        )
        t = _Table([{"id": "c1", "status": "active"}], raise_on_update=refused)
        with _Patched(_wire(t)), caplog.at_level(logging.INFO):
            assert await sb.mark_needs_agent("c1") == ("failed", "23514")
        logged = caplog.text
        assert "23514" in logged
        for pii in ("Jane", "jane@example.com", "2105550100"):
            assert pii not in logged

    @pytest.mark.asyncio
    async def test_today_s_production_error_is_22001(self):
        t = _Table([{"id": "c1", "status": "active"}],
                   raise_on_update=_DbError("22001", "value too long for type character varying(10)"))
        with _Patched(_wire(t)):
            assert await sb.mark_needs_agent("c1") == ("failed", "22001")

    @pytest.mark.asyncio
    async def test_an_error_that_is_not_the_database_s_logs_no_invented_code(self):
        t = _Table([{"id": "c1", "status": "active"}], raise_on_update=RuntimeError("pool closed"))
        with _Patched(_wire(t)):
            assert await sb.mark_needs_agent("c1") == ("failed", "none")


# ══════════════════════════════════════════════════════════════
# 1b. The one door: counted, honest, dispatches only what was queued
# ══════════════════════════════════════════════════════════════

class TestQueueForPerson:
    @pytest.mark.asyncio
    async def test_queued_counts_and_dispatches(self):
        dispatch = MagicMock(name="dispatch_needs_agent")
        with patch.object(routing.db, "mark_needs_agent", new=AsyncMock(return_value=("queued", ""))), \
             patch.object(routing, "dispatch_needs_agent", new=dispatch), \
             patch("app.pipeline.orchestrator._fire_and_forget") as fire:
            assert await routing.queue_for_person("c1", reason="asked") is True
        assert routing.QUEUE_HEALTH["queued"] == 1
        dispatch.assert_called_once_with("c1")
        fire.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_false_queues_without_dispatching(self):
        dispatch = MagicMock(name="dispatch_needs_agent")
        with patch.object(routing.db, "mark_needs_agent", new=AsyncMock(return_value=("queued", ""))), \
             patch.object(routing, "dispatch_needs_agent", new=dispatch), \
             patch("app.pipeline.orchestrator._fire_and_forget") as fire:
            assert await routing.queue_for_person("c1", reason="nobody free", dispatch=False) is True
        dispatch.assert_not_called()
        fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_open_is_counted_apart_from_failures(self):
        with patch.object(routing.db, "mark_needs_agent", new=AsyncMock(return_value=("not_open", ""))), \
             patch("app.pipeline.orchestrator._fire_and_forget") as fire:
            assert await routing.queue_for_person("c1", reason="asked") is False
        assert routing.QUEUE_HEALTH["not_open"] == 1
        assert routing.QUEUE_HEALTH["write_failed"] == 0
        fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failed_write_is_counted_logged_and_never_says_queued(self, caplog):
        with patch.object(routing.db, "mark_needs_agent", new=AsyncMock(return_value=("failed", "22001"))), \
             patch("app.pipeline.orchestrator._fire_and_forget") as fire, \
             caplog.at_level(logging.INFO):
            assert await routing.queue_for_person("c1", reason="asked") is False
        assert routing.QUEUE_HEALTH["write_failed"] == 1
        assert routing.QUEUE_HEALTH["last_failed_code"] == "22001"
        assert routing.QUEUE_HEALTH["last_failed_at"]
        fire.assert_not_called()
        assert "NOT in the line" in caplog.text
        assert "status=needs_agent (" not in caplog.text


class TestPutBack:
    """A dispatch claims the chat, the handoff fails, the chat goes back in the line."""

    def _claimed(self):
        conv = {"id": "c1", "status": "needs_agent", "assigned_agent_id": None, "tunnel": "sales"}
        t = _Table([dict(conv)])
        return conv, t

    @pytest.mark.asyncio
    async def test_a_failed_put_back_is_counted(self, caplog):
        conv, t = self._claimed()
        with patch.object(routing.settings, "auto_dispatch_enabled", True), \
             patch.object(routing.db, "get_conversation_simple", new=AsyncMock(return_value=conv)), \
             patch.object(routing, "route_conversation",
                          new=AsyncMock(return_value={"agent_id": "a1", "agent_name": "Ana"})), \
             patch.object(routing.db, "get_client", return_value=t), \
             patch.object(routing.db, "_run_sync", new=AsyncMock(side_effect=lambda fn, *a, **k: fn())), \
             patch("app.services.handoff.perform_handoff_to_agent",
                   new=AsyncMock(side_effect=RuntimeError("handoff broke"))), \
             patch.object(routing.db, "mark_needs_agent", new=AsyncMock(return_value=("failed", "08006"))), \
             caplog.at_level(logging.INFO):
            assert await routing.dispatch_needs_agent("c1") is False
        assert routing.QUEUE_HEALTH["put_back_failed"] == 1
        assert routing.QUEUE_HEALTH["last_failed_code"] == "08006"
        assert "could not be put back" in caplog.text

    @pytest.mark.asyncio
    async def test_a_put_back_that_held_counts_nothing(self):
        conv, t = self._claimed()
        put_back = AsyncMock(return_value=("queued", ""))
        with patch.object(routing.settings, "auto_dispatch_enabled", True), \
             patch.object(routing.db, "get_conversation_simple", new=AsyncMock(return_value=conv)), \
             patch.object(routing, "route_conversation",
                          new=AsyncMock(return_value={"agent_id": "a1", "agent_name": "Ana"})), \
             patch.object(routing.db, "get_client", return_value=t), \
             patch.object(routing.db, "_run_sync", new=AsyncMock(side_effect=lambda fn, *a, **k: fn())), \
             patch("app.services.handoff.perform_handoff_to_agent",
                   new=AsyncMock(side_effect=RuntimeError("handoff broke"))), \
             patch.object(routing.db, "mark_needs_agent", new=put_back):
            assert await routing.dispatch_needs_agent("c1") is False
        put_back.assert_awaited_once_with("c1")
        assert routing.QUEUE_HEALTH["put_back_failed"] == 0


# ══════════════════════════════════════════════════════════════
# 2. Every "open" read sees needs_agent
# ══════════════════════════════════════════════════════════════

class TestOpenReads:
    def test_open_means_active_or_needs_agent(self):
        assert sb.OPEN_STATUSES == ("active", "needs_agent")

    @pytest.mark.asyncio
    async def test_the_widget_keeps_a_chat_waiting_for_a_person(self):
        from app.api.chat import get_active_conversation_for_visitor

        t = _Table([{"id": "c-wait", "visitor_id": "v1", "status": "needs_agent"}])
        with _Patched(_wire(t)):
            out = await get_active_conversation_for_visitor("v1", x_visitor_token=None)
        assert out == {"success": True, "conversation_id": "c-wait"}
        assert ("in", "status", OPEN) in _status_filters(t)

    @pytest.mark.asyncio
    async def test_a_failed_lookup_is_not_no_conversation(self):
        """A null makes the widget drop its cached chat; a 503 makes it keep it."""
        from app.api.chat import get_active_conversation_for_visitor

        t = _Table([], raise_on_select=_DbError("57014", "canceling statement due to statement timeout"))
        with _Patched(_wire(t)), pytest.raises(HTTPException) as err:
            await get_active_conversation_for_visitor("v1", x_visitor_token=None)
        assert err.value.status_code == 503

    @pytest.mark.asyncio
    async def test_a_failed_lookup_logs_the_code_never_the_error_text(self, caplog):
        from app.api.chat import get_active_conversation_for_visitor

        t = _Table([], raise_on_select=_DbError(
            "22P02", 'invalid input syntax — "jane@example.com" / +12105550100'))
        with _Patched(_wire(t)), caplog.at_level(logging.INFO), pytest.raises(HTTPException):
            await get_active_conversation_for_visitor("v1", x_visitor_token=None)
        assert "22P02" in caplog.text
        for pii in ("jane@example.com", "2105550100"):
            assert pii not in caplog.text

    @pytest.mark.asyncio
    async def test_the_visitor_s_open_chat_is_found_not_duplicated(self):
        t = _Table([{"id": "c-wait", "visitor_id": "v1", "status": "needs_agent", "metadata": {}}])
        visitor = SimpleNamespace(name="", email="", phone="", country_code=None)
        with _Patched(_wire(t)):
            conv = await sb.get_or_create_conversation(None, "sales", visitor, visitor_id="v1")
        assert conv["id"] == "c-wait"
        assert t.inserted == []

    @pytest.mark.asyncio
    async def test_the_abandoned_sweep_pushes_a_client_who_asked_for_a_person(self):
        t = _Table([])
        with _Patched(_wire(t)):
            await sb.get_abandoned_conversations(30)
        assert ("in", "status", ["active", "needs_agent", "closed"]) in _status_filters(t)

    @pytest.mark.asyncio
    async def test_the_client_who_left_is_closed_as_before(self):
        t = _Table([])
        with _Patched(_wire(t)):
            await sb.get_stale_presence_left_conversations(5)
        assert ("in", "status", OPEN) in _status_filters(t)

    @pytest.mark.asyncio
    async def test_tab_counts_match_their_lists(self):
        t = _Table([])
        with _Patched(_wire(t)):
            await sb.get_conversation_counts("a1")
        statuses = [f[2] for f in _status_filters(t)]
        # my_active, the queue and all_active count open chats; the two
        # closed tabs count closed ones.
        assert statuses.count(OPEN) == 3
        assert statuses.count(["closed"]) == 2

    @pytest.mark.parametrize("assigned_to", ["none", "all", "me"])
    def test_an_active_list_returns_open_chats(self, assigned_to):
        """The queue list (assigned_to=none) must return the needs_agent rows
        the heartbeat counts, or the panel counts a client it cannot show."""
        from app.api.conversations import router
        from app.security.auth import get_current_user

        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: {
            "id": "u1", "role": "owner", "tunnel_scope": "sales"}
        lister = AsyncMock(return_value=([], 0))
        with patch.object(sb, "get_conversations", new=lister), \
             patch.object(sb, "get_last_message_previews", new=AsyncMock(return_value={})):
            r = TestClient(app).get(f"/api/conversations?assigned_to={assigned_to}&status=active")
        assert r.status_code == 200
        kwargs = lister.await_args.kwargs
        assert kwargs["status_in"] == OPEN
        assert kwargs["status"] is None

    def test_a_closed_list_is_still_closed(self):
        from app.api.conversations import router
        from app.security.auth import get_current_user

        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: {
            "id": "u1", "role": "owner", "tunnel_scope": "sales"}
        lister = AsyncMock(return_value=([], 0))
        with patch.object(sb, "get_conversations", new=lister):
            r = TestClient(app).get("/api/conversations?assigned_to=all&status=closed")
        assert r.status_code == 200
        assert lister.await_args.kwargs["status"] == "closed"
        assert lister.await_args.kwargs["status_in"] is None

    def test_no_read_means_open_with_active_alone(self):
        """Every conversations read in the db layer that filters on 'active'
        alone is a read that would lose the client who asked for a person."""
        src = (ROOT / "app" / "db" / "supabase.py").read_text(encoding="utf-8")
        assert '.eq("status", "active")' not in src
        assert '["active", "needs_agent"]' not in src


# ══════════════════════════════════════════════════════════════
# 3. /health
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_the_full_health_payload_shows_them_too():
    """?full=true is what an operator reads while debugging: the same counters."""
    from app.api import health

    routing.QUEUE_HEALTH["write_failed"] = 3
    with patch.object(health, "check_connection", new=AsyncMock(return_value=True)), \
         patch.object(sb, "get_queue_stats", new=AsyncMock(return_value={"waiting_now": 0})), \
         patch.object(sb, "get_operator_load", new=AsyncMock(return_value={"per_agent": {}})):
        out = await health.health_detail(full=True)
    assert out["needs_agent"]["write_failed"] == 3


@pytest.mark.asyncio
async def test_health_detail_shows_the_queue_writes():
    from app.api.health import health_detail

    routing.QUEUE_HEALTH["write_failed"] = 2
    routing.QUEUE_HEALTH["last_failed_code"] = "22001"
    out = await health_detail(full=False)
    assert out["needs_agent"]["write_failed"] == 2
    assert out["needs_agent"]["last_failed_code"] == "22001"
    assert set(out["needs_agent"]) >= {"queued", "not_open", "write_failed", "put_back_failed"}
