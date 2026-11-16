"""The departure task: a client who leaves after giving a contact, with nobody
on the chat, becomes a task for the free agent (app/services/departure.py).

Pins: every condition of "this chat gets a task" on both sides; the call /
email choice; the Ready sales agent with the fewest open tasks (counting the
ones this run gives out), Unowned "no_ready_agent" when nobody is Ready; one
departure task per chat, ever; off at merge; failures counted and logged
without names, phones or e-mails; the job registered every minute; migration
053 marked not applied.
"""

import logging
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.db import supabase as db
from app.services import departure
from app.services.departure import departure_title, seconds_since_left, serves_sales

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 15, 22, 0, tzinfo=timezone.utc)


def _conv(**over) -> dict:
    conv = {
        "id": "c-1",
        "visitor_name": "Test Client",
        "visitor_phone": "+12025550147",
        "visitor_email": "client@example.com",
        "mode": "ai",
        "assigned_agent_id": None,
        "metadata": {"pres_v": 2},
        "status": "active",
        "last_user_message_at": "2026-11-15T21:55:00+00:00",
    }
    conv.update(over)
    return conv


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    flags._reset_for_tests()
    monkeypatch.delenv("DEPARTURE_TASKS_ENABLED", raising=False)
    saved = dict(db.TASKS_HEALTH)
    for k in ("departure", "departure_unowned", "departure_failures"):
        db.TASKS_HEALTH[k] = 0
    yield
    db.TASKS_HEALTH.clear()
    db.TASKS_HEALTH.update(saved)
    flags._reset_for_tests()


def _left(seconds):
    return patch("app.services.departure.client_left", return_value=seconds)


# ══════════════════════════════════════════════════════════════
# 1. Which chat gets a task
# ══════════════════════════════════════════════════════════════

class TestWhichChat:
    def test_a_client_who_left_with_a_contact(self):
        with _left(150):
            assert seconds_since_left(_conv(), {"id": "L1"}, [], NOW) == 150

    @pytest.mark.parametrize("over", [
        {"last_user_message_at": None},                         # never wrote
        {"visitor_phone": None, "visitor_email": None},         # no contact
        {"visitor_phone": "  ", "visitor_email": ""},           # blank contact
        {"mode": "human"},                                      # a person has it
        {"assigned_agent_id": "agent-1"},                       # a person was given it
        {"metadata": {"suspect": "form_trap"}},                 # the form trap's bot
        {"metadata": {"locked": "abuse"}},                      # the behaviour lock
    ])
    def test_no_task(self, over):
        with _left(150):
            assert seconds_since_left(_conv(**over), {"id": "L1"}, [], NOW) is None

    def test_a_lead_already_in_the_crm(self):
        with _left(150):
            assert seconds_since_left(_conv(), {"id": "L1", "created_in_crm": True}, [], NOW) is None

    def test_still_there_or_left_too_long_ago(self):
        with _left(None):
            assert seconds_since_left(_conv(), {}, [], NOW) is None
        with _left(departure.MAX_LEFT_SECONDS + 1):
            assert seconds_since_left(_conv(), {}, [], NOW) is None
        with _left(departure.MAX_LEFT_SECONDS):
            assert seconds_since_left(_conv(), {}, [], NOW) == departure.MAX_LEFT_SECONDS

    def test_an_open_task_holds_it_a_done_one_does_not(self):
        with _left(150):
            assert seconds_since_left(_conv(), {}, [{"source": "caught", "done_at": None, "dismissed_at": None}], NOW) is None
            assert seconds_since_left(_conv(), {}, [{"source": "caught", "done_at": "x", "dismissed_at": None}], NOW) == 150
            assert seconds_since_left(_conv(), {}, [{"source": "caught", "done_at": None, "dismissed_at": "x"}], NOW) == 150

    def test_one_departure_task_per_chat_ever(self):
        with _left(150):
            done = {"source": "departure", "done_at": "2026-11-15T20:00:00+00:00", "dismissed_at": None}
            assert seconds_since_left(_conv(), {}, [done], NOW) is None

    def test_the_form_trap_check_counts_nothing(self):
        from app.security.form_trap import FORM_TRAP_HEALTH
        before = FORM_TRAP_HEALTH["crm_held"]
        with _left(150):
            seconds_since_left(_conv(metadata={"suspect": "form_trap"}), {}, [], NOW)
        assert FORM_TRAP_HEALTH["crm_held"] == before


class TestWords:
    def test_titles(self):
        assert departure_title("call", "Maria", "New York → London") == "Call Maria back about New York → London"
        assert departure_title("call", "", None) == "Call the client back"
        assert departure_title("email", "Maria", "JFK → LHR") == "Email Maria about JFK → LHR"
        assert departure_title("email", None, None) == "Email the client back"

    @pytest.mark.parametrize("user,ok", [
        ({"role": "sales", "tunnel_scope": None}, True),
        ({"role": "sales", "tunnel_scope": "sales"}, True),
        ({"role": "support", "tunnel_scope": "all"}, True),
        ({"role": "support", "tunnel_scope": None}, False),
        ({"role": "sales", "tunnel_scope": "support"}, False),
    ])
    def test_who_takes_a_sales_task(self, user, ok):
        assert serves_sales(user) is ok


# ══════════════════════════════════════════════════════════════
# 2. The job
# ══════════════════════════════════════════════════════════════

def _job(convs, *, tasks=(), leads=None, ready=(), counts=None, insert=None, left=150):
    """Patches for one run of the job, with the switch on."""
    insert = insert or AsyncMock(side_effect=lambda row: {"id": f"t-{row['conversation_id']}", **row})
    return [
        patch("app.services.departure.enabled", return_value=True),
        patch.object(db, "get_departure_candidates", new=AsyncMock(return_value=list(convs))),
        patch.object(db, "tasks_of_conversations", new=AsyncMock(return_value=list(tasks))),
        patch.object(db, "get_follow_up_context", new=AsyncMock(return_value=({}, leads or {}))),
        patch.object(db, "ready_operators", new=AsyncMock(return_value=list(ready))),
        patch.object(db, "open_task_counts", new=AsyncMock(return_value=counts or {})),
        patch.object(db, "insert_follow_up", new=insert),
        patch("app.services.tasks_service.note", new=AsyncMock()),
        _left(left),
    ]


class _Patched:
    def __init__(self, patches):
        self.patches = patches
        self.mocks = []

    def __enter__(self):
        self.mocks = [p.__enter__() for p in self.patches]
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.__exit__(*exc)


class TestJob:
    @pytest.mark.asyncio
    async def test_off_at_merge_reads_nothing(self):
        with patch.object(db, "get_departure_candidates", new=AsyncMock(side_effect=AssertionError("read"))):
            assert await departure.run_departure_tasks() == {"enabled": False}

    @pytest.mark.asyncio
    async def test_the_switch_is_a_flag(self, monkeypatch):
        monkeypatch.setenv("DEPARTURE_TASKS_ENABLED", "true")
        assert departure.enabled() is True

    @pytest.mark.asyncio
    async def test_a_call_and_an_email_for_the_ready_sales_agent_with_fewest(self):
        convs = [
            _conv(id="c-1"),
            _conv(id="c-2", visitor_phone=None, visitor_name="Ana Pop"),
        ]
        leads = {"c-1": {"id": "L1", "route_display": "ATL → ICN"}}
        ready = [
            {"id": "agent-a", "role": "sales", "tunnel_scope": "sales"},
            {"id": "agent-b", "role": "sales", "tunnel_scope": None},
            {"id": "agent-s", "role": "support", "tunnel_scope": "support"},  # not for a sales task
        ]
        with _Patched(_job(convs, leads=leads, ready=ready, counts={"agent-a": 3, "agent-b": 1})) as p:
            with patch("app.services.departure.datetime") as clock:
                clock.now.return_value = NOW
                out = await departure.run_departure_tasks()
            insert = p.mocks[6]
            counts_call = p.mocks[5]
        assert out == {"candidates": 2, "due": 2, "created": 2, "unowned": 0}
        counts_call.assert_awaited_once_with(["agent-a", "agent-b"])
        rows = [c.args[0] for c in insert.await_args_list]
        assert rows[0]["kind"] == "call" and rows[0]["title"] == "Call Test back about ATL → ICN"
        assert rows[1]["kind"] == "email" and rows[1]["title"] == "Email Ana back"
        # agent-b had 1, takes the first (now 2), and still has fewer than agent-a's 3.
        assert [r["assignee_id"] for r in rows] == ["agent-b", "agent-b"]
        for r in rows:
            assert r["source"] == "departure" and r["due_kind"] == "default"
            assert r["unowned_reason"] is None and r["unowned_at"] is None
            assert r["due_at"] == (NOW - timedelta(seconds=150) + timedelta(minutes=15)).isoformat()
        assert rows[0]["lead_id"] == "L1" and rows[1]["lead_id"] is None
        assert db.TASKS_HEALTH["departure"] == 2

    @pytest.mark.asyncio
    async def test_a_burst_is_spread(self):
        convs = [_conv(id=f"c-{i}") for i in range(4)]
        ready = [{"id": "agent-a", "role": "sales"}, {"id": "agent-b", "role": "sales"}]
        with _Patched(_job(convs, ready=ready, counts={"agent-a": 0, "agent-b": 1})) as p:
            await departure.run_departure_tasks()
            rows = [c.args[0] for c in p.mocks[6].await_args_list]
        assert [r["assignee_id"] for r in rows] == ["agent-a", "agent-a", "agent-b", "agent-a"]

    @pytest.mark.asyncio
    async def test_nobody_ready_goes_unowned(self):
        with _Patched(_job([_conv()], ready=[])) as p:
            out = await departure.run_departure_tasks()
            row = p.mocks[6].await_args.args[0]
            note = p.mocks[7]
        assert out["created"] == 1 and out["unowned"] == 1
        assert row["assignee_id"] is None and row["unowned_reason"] == "no_ready_agent" and row["unowned_at"]
        assert db.TASKS_HEALTH["departure_unowned"] == 1
        args = note.await_args.args
        assert args[1] == "departure" and args[3]["given"] == "unowned"

    @pytest.mark.asyncio
    async def test_who_is_ready_unreadable_still_makes_the_tasks(self):
        patches = _job([_conv()], ready=[])
        patches[4] = patch.object(db, "ready_operators", new=AsyncMock(side_effect=RuntimeError("down")))
        with _Patched(patches) as p:
            out = await departure.run_departure_tasks()
            row = p.mocks[6].await_args.args[0]
        assert out["created"] == 1 and row["unowned_reason"] == "no_ready_agent"
        assert db.TASKS_HEALTH["departure_failures"] == 1

    @pytest.mark.asyncio
    async def test_a_refused_row_is_counted_without_pii_and_the_next_chat_goes_on(self, caplog):
        class APIError(Exception):
            code = "23514"

        calls = {"n": 0}

        async def insert(row):
            calls["n"] += 1
            if calls["n"] == 1:
                raise APIError('Failing row contains (Call Test back, +12025550147, client@example.com)')
            return {"id": "t-2", **row}

        with _Patched(_job([_conv(id="c-1"), _conv(id="c-2")], insert=insert)), caplog.at_level(logging.INFO):
            out = await departure.run_departure_tasks()
        assert out["created"] == 1
        assert db.TASKS_HEALTH["departure_failures"] == 1
        assert "APIError 23514" in caplog.text
        for pii in ("Test", "2025550147", "client@example.com"):
            assert pii not in caplog.text

    @pytest.mark.asyncio
    async def test_an_insert_without_a_row_is_counted(self, caplog):
        with _Patched(_job([_conv(id="c-1")], insert=AsyncMock(return_value=None))) as p, caplog.at_level(logging.ERROR):
            out = await departure.run_departure_tasks()
            note = p.mocks[7]
        assert out["created"] == 0
        assert db.TASKS_HEALTH["departure_failures"] == 1 and db.TASKS_HEALTH["departure"] == 0
        assert "the insert returned no row" in caplog.text
        note.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_history_line_carries_the_due_minutes(self):
        with _Patched(_job([_conv()], ready=[{"id": "agent-a", "role": "sales"}])) as p:
            await departure.run_departure_tasks()
            details = p.mocks[7].await_args.args[3]
        assert details["due_minutes"] == 15 and details["given"] == "ready" and details["kind"] == "call"

    @pytest.mark.asyncio
    async def test_a_failed_read_is_counted(self):
        patches = _job([_conv()])
        patches[1] = patch.object(db, "get_departure_candidates", new=AsyncMock(side_effect=RuntimeError("down")))
        with _Patched(patches):
            assert await departure.run_departure_tasks() == {"failed": "read"}
        assert db.TASKS_HEALTH["departure_failures"] == 1

    @pytest.mark.asyncio
    async def test_before_migration_051_it_stops_quietly(self):
        patches = _job([_conv()])
        patches[2] = patch.object(db, "tasks_of_conversations", new=AsyncMock(side_effect=db.TasksNotSetUp("x")))
        with _Patched(patches):
            assert await departure.run_departure_tasks() == {"tasks_not_set_up": True}

    @pytest.mark.asyncio
    async def test_nothing_due_reads_no_agents(self):
        patches = _job([_conv()], left=None)
        with _Patched(patches) as p:
            out = await departure.run_departure_tasks()
            ready = p.mocks[4]
        assert out == {"candidates": 1, "created": 0}
        ready.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_candidates(self):
        with _Patched(_job([])):
            assert await departure.run_departure_tasks() == {"candidates": 0, "created": 0}


# ══════════════════════════════════════════════════════════════
# 3. Wiring
# ══════════════════════════════════════════════════════════════

class TestWiring:
    def test_registered_every_minute_and_quiet_when_nothing_made(self):
        src = (ROOT / "app" / "services" / "scheduler.py").read_text(encoding="utf-8")
        assert '("departure_tasks", run_departure_tasks, 60),' in src
        from app.services.scheduler import _QUIET_WHEN
        assert _QUIET_WHEN["departure_tasks"]({"created": 0}) is True
        assert _QUIET_WHEN["departure_tasks"]({"created": 1}) is False

    def test_the_candidates_read_is_open_sales_chats_with_a_contact(self):
        src = (ROOT / "app" / "db" / "supabase.py").read_text(encoding="utf-8")
        body = src[src.index("async def get_departure_candidates"):src.index("async def tasks_of_conversations")]
        assert '.in_("status", list(OPEN_STATUSES))' in body
        assert '.eq("tunnel", "sales")' in body
        assert '.or_("visitor_phone.not.is.null,visitor_email.not.is.null")' in body

    def test_health_carries_the_counters(self):
        for k in ("departure", "departure_unowned", "departure_failures"):
            assert k in db.TASKS_HEALTH


class TestMigration053:
    sql = (ROOT / "migrations" / "053_departure_tasks.sql").read_text(encoding="utf-8")

    def test_it_is_marked_not_applied(self):
        assert "APPLIED: nu" in self.sql

    def test_it_accepts_the_new_source_and_reason(self):
        assert "CHECK (source IN ('caught', 'client_asked', 'added', 'dashboard', 'departure'))" in self.sql
        assert "unowned_reason IN ('ai_only', 'left_shift', 'no_ready_agent')" in self.sql

    def test_all_or_nothing_and_never_queues_writes_for_long(self):
        body = self.sql[self.sql.index("BEGIN;"):]
        assert body.rstrip().endswith("COMMIT;")
        assert "SET LOCAL lock_timeout" in body

    def test_the_code_uses_the_same_words(self):
        assert departure.SOURCE == "departure"
        assert departure.UNOWNED_REASON == "no_ready_agent"


class TestPanelWords:
    """What the Tasks page shows for a departure task (app/services/tasks_view.py)."""

    def test_the_source_has_a_label(self):
        from app.services.tasks_view import SOURCE_LABEL
        assert SOURCE_LABEL["departure"] == "Client left"

    def test_the_history_line(self):
        from app.services.tasks_view import history_of
        given = history_of(
            [{"kind": "departure", "at": "2026-11-15T22:00:00+00:00",
              "details": {"left_minutes_ago": 3, "given": "ready", "due_minutes": 15}}],
            {}, client="Maria R.",
        )
        assert given[0]["text"] == (
            "Maria R. left the site after leaving a contact, with nobody on the chat. "
            "The task was made 3 min later. Due 15 minutes after they left."
        )
        unowned = history_of(
            [{"kind": "departure", "at": "x", "details": {"left_minutes_ago": 0, "given": "unowned", "due_minutes": 15}}],
            {}, client="",
        )
        assert unowned[0]["text"] == (
            "The client left the site after leaving a contact, with nobody on the chat. "
            "Due 15 minutes after they left. Nobody was Ready, so it went to Unowned."
        )

    def test_the_due_words_come_from_the_task_not_from_today(self):
        from app.services.tasks_view import history_of
        line = history_of(
            [{"kind": "departure", "at": "x", "details": {"left_minutes_ago": 2, "given": "ready", "due_minutes": 10}}],
            {}, client="",
        )
        assert line[0]["text"].endswith("The task was made 2 min later. Due 10 minutes after they left.")
        bare = history_of([{"kind": "departure", "at": "x", "details": {"given": "ready"}}], {}, client="")
        assert bare[0]["text"] == "The client left the site after leaving a contact, with nobody on the chat."

    def test_unowned_reason_reads(self):
        from app.services.tasks_view import history_of
        line = history_of([{"kind": "unowned", "at": "x", "details": {"reason": "no_ready_agent"}}], {}, client="")
        assert line[0]["text"] == "Went to Unowned: nobody was Ready when the client left."
