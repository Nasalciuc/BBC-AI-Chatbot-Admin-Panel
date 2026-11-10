"""Fewer trips to the database on the paths every open panel walks.

10 Nov 2026, the evening after the redesign went live: the API made 1.25
million requests to the database in 20 hours (40 a second at the peak), the
chat list took 300-450 ms where it had taken 120-150, and several times that
evening the database answered nothing for 5 seconds. None of the requests
below was needed:

  * the heartbeat read the user's row right after writing it (the write
    answers with the row);
  * the tab badges asked five counts in a row, three of them over the same
    few open chats;
  * the list read who has each chat, THEN each lead's trip: two independent
    reads, one after the other;
  * the abandoned sweep asked 200 conversations for their newest message and
    200 leads whether they are in the CRM, every 5 minutes, to learn that 199
    were done;
  * the presence tick read the day twice — and took a failed read for "the
    day has not started", resetting first_seen_at and the minutes.

Search by meaning (Qdrant) has answered 400 since August; the reason was
never logged. It is now, and counted.

And two answers that were not true: sign-in said "no such account" (and
counted a failed attempt) when the users table could not be read.
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import agent as agent_api  # noqa: E402
from app.api import cron  # noqa: E402
from app.db import qdrant  # noqa: E402
from app.db import supabase as sb  # noqa: E402
from app.services import presence  # noqa: E402

_AGENT = "11111111-1111-1111-1111-111111111111"
_ROW = {
    "id": _AGENT, "email": "op@bbc.test", "name": "Op", "role": "sales",
    "is_ready": True, "is_active": True, "chat_enabled": True,
    "tunnel_scope": "sales", "team_id": None,
}


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


# ══════════════════════════════════════════════════════════════
# 1. The heartbeat: the write answers with the row
# ══════════════════════════════════════════════════════════════

class TestHeartbeatReadsOnce:
    @staticmethod
    async def _beat(written, read=None):
        reader = AsyncMock(return_value=read)
        with patch.object(agent_api.db, "update_user_last_seen", new=AsyncMock(return_value=written)), \
             patch.object(agent_api.db, "get_user_by_id", new=reader), \
             patch.object(agent_api.db, "get_queue_for_operator", new=AsyncMock(return_value=[])), \
             patch("app.services.presence.record_presence_tick", new=AsyncMock()):
            out = await agent_api.heartbeat(agent_api.HeartbeatBody(), user={"id": _AGENT, "role": "owner"})
        return out, reader

    @pytest.mark.asyncio
    async def test_the_row_the_write_returned_is_the_row_used(self):
        out, reader = await self._beat(dict(_ROW))
        reader.assert_not_awaited()
        # Role and readiness are the database's (the row), never the token's.
        assert out["role"] == "sales" and out["is_ready"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("written", [None, {}, {"id": "someone-else", "role": "owner"}, MagicMock()])
    async def test_a_write_that_returned_nothing_usable_falls_back_to_the_read(self, written):
        out, reader = await self._beat(written, read=dict(_ROW, role="support"))
        reader.assert_awaited_once_with(_AGENT)
        assert out["role"] == "support"

    @pytest.mark.asyncio
    async def test_no_row_from_either_fails_closed(self):
        out, _ = await self._beat(None, read=None)
        assert out["role"] == "" and out["is_ready"] is False and out["queue_count"] == 0

    @pytest.mark.asyncio
    async def test_the_presence_write_returns_the_row_it_wrote(self):
        run = AsyncMock(return_value=SimpleNamespace(data=[dict(_ROW)]))
        with patch.object(sb, "get_client", return_value=MagicMock()), patch.object(sb, "_run_sync", new=run):
            assert await sb.update_user_last_seen(_AGENT, viewing_conversation_id=None) == _ROW
        assert run.await_count == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("data", [[], None])
    async def test_a_write_that_matched_nobody_returns_none(self, data):
        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", new=AsyncMock(return_value=SimpleNamespace(data=data))):
            assert await sb.update_user_last_seen(_AGENT) is None

    @pytest.mark.asyncio
    async def test_a_failed_write_returns_none_and_does_not_raise(self):
        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", new=AsyncMock(side_effect=RuntimeError("Server disconnected"))):
            assert await sb.update_user_last_seen(_AGENT) is None


# ══════════════════════════════════════════════════════════════
# 2. The tab badges: one read for the open chats
# ══════════════════════════════════════════════════════════════

class _Counts:
    """A conversations table that answers selects with rows and an exact count."""

    def __init__(self, rows, page=None):
        self.rows, self.page, self.executed = rows, page, []

    def table(self, _name):
        return _CountQuery(self)


class _CountQuery:
    def __init__(self, t):
        self.t, self.f, self.cap = t, [], None

    def select(self, cols, **_k):
        self.cols = cols
        return self

    def eq(self, col, val):
        self.f.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        self.f.append(lambda r: r.get(col) in vals)
        return self

    def is_(self, col, _null):
        self.f.append(lambda r: r.get(col) is None)
        return self

    def limit(self, n):
        self.cap = n
        return self

    def execute(self):
        hit = [r for r in self.t.rows if all(f(r) for f in self.f)]
        self.t.executed.append(self.cols)
        cap = self.cap if self.t.page is None or self.cap == 0 else min(self.cap, self.t.page)
        return SimpleNamespace(data=[dict(r) for r in hit[:cap]], count=len(hit))


def _conv(i, status="active", agent=None, tunnel="sales"):
    return {"id": f"c{i}", "status": status, "assigned_agent_id": agent, "tunnel": tunnel}


class TestTabCounts:
    @staticmethod
    async def _counts(table, tunnel=None):
        with patch.object(sb, "get_client", return_value=table), \
             patch.object(sb, "_run_sync", new=AsyncMock(side_effect=lambda fn, *a, **k: fn())):
            return await sb.get_conversation_counts("me", tunnel=tunnel)

    @pytest.mark.asyncio
    async def test_five_badges_from_three_reads(self):
        t = _Counts([
            _conv(1, agent="me"), _conv(2, "needs_agent", agent="me"), _conv(3, "needs_agent"),
            _conv(4), _conv(5, agent="other"),
            _conv(6, "closed", agent="me"), _conv(7, "closed", agent="other"), _conv(8, "closed"),
            _conv(9, "completed", agent="me"), _conv(10, "pending"),
        ])
        assert await self._counts(t) == {
            "my_active": 2, "queue": 2, "my_closed": 1, "all_active": 5, "all_closed": 3,
        }
        assert len(t.executed) == 3

    @pytest.mark.asyncio
    async def test_the_tunnel_narrows_every_badge(self):
        t = _Counts([
            _conv(1, agent="me"), _conv(2, agent="me", tunnel="support"), _conv(3, tunnel="support"),
            _conv(4, "closed", agent="me", tunnel="support"), _conv(5, "closed", agent="me"),
        ])
        assert await self._counts(t, tunnel="support") == {
            "my_active": 1, "queue": 1, "my_closed": 1, "all_active": 2, "all_closed": 1,
        }

    @pytest.mark.asyncio
    async def test_more_open_chats_than_the_page_are_counted_not_guessed(self):
        rows = [_conv(i, agent="me" if i % 2 else None) for i in range(10)]
        t = _Counts(rows, page=4)          # the read brings 4 of the 10
        got = await self._counts(t)
        assert (got["my_active"], got["queue"], got["all_active"]) == (5, 5, 10)
        assert len(t.executed) == 5        # the page, two closed, and the two it could not answer

    @pytest.mark.asyncio
    async def test_my_chats_are_mine_whatever_the_case_of_the_id(self):
        """The database compares uuids; the count here compares text."""
        t = _Counts([_conv(1, agent="ME"), _conv(2, agent="other"), _conv(3)])
        got = await self._counts(t)
        assert (got["my_active"], got["queue"], got["all_active"]) == (1, 1, 3)

    @pytest.mark.asyncio
    async def test_a_failed_read_answers_zeros_as_before(self):
        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", new=AsyncMock(side_effect=RuntimeError("Server disconnected"))):
            got = await sb.get_conversation_counts("me")
        assert got == {"my_active": 0, "queue": 0, "my_closed": 0, "all_active": 0, "all_closed": 0}


# ══════════════════════════════════════════════════════════════
# 3. The list: who has each chat and each lead's trip, together
# ══════════════════════════════════════════════════════════════

class TestListReadsTogether:
    @staticmethod
    def _page():
        return SimpleNamespace(count=1, data=[{
            "id": "c1", "status": "active", "mode": "ai", "assigned_agent_id": None,
            "engaged_agent_id": None, "widget_presence": None, "widget_last_event_at": None,
            "widget_pings": None, "agent_assigned_at": None, "created_at": _iso(5), "updated_at": _iso(1),
        }])

    @pytest.mark.asyncio
    async def test_neither_read_waits_for_the_other(self):
        started: list[str] = []
        both = asyncio.Event()

        async def _note(name):
            started.append(name)
            if len(started) == 2:
                both.set()
            # Each waits for the OTHER to have started: one after the other
            # would never finish.
            await asyncio.wait_for(both.wait(), timeout=2)

        async def _agents(rows):
            await _note("agents")
            return rows

        async def _leads(ids):
            await _note("leads")
            return {"c1": {"conversation_id": "c1", "origin_code": "JFK", "destination_code": "LHR"}}

        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", new=AsyncMock(return_value=self._page())), \
             patch.object(sb, "_supervisor_columns_available", return_value=True), \
             patch.object(sb, "enrich_conversations_agent_info", new=_agents), \
             patch.object(sb, "_get_lead_completeness_inputs", new=_leads):
            rows, total = await sb.get_conversations(tunnel="sales")
        assert sorted(started) == ["agents", "leads"]
        assert total == 1 and rows[0]["route"] == {"from": "JFK", "to": "LHR"}

    @pytest.mark.asyncio
    async def test_without_the_supervisor_columns_only_the_agents_are_read(self):
        leads = AsyncMock(return_value={})
        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", new=AsyncMock(return_value=self._page())), \
             patch.object(sb, "_supervisor_columns_available", return_value=False), \
             patch.object(sb, "enrich_conversations_agent_info", new=AsyncMock(side_effect=lambda rows: rows)), \
             patch.object(sb, "_get_lead_completeness_inputs", new=leads):
            rows, _ = await sb.get_conversations(tunnel="sales")
        leads.assert_not_awaited()
        assert [r["id"] for r in rows] == ["c1"] and "route" not in rows[0]


# ══════════════════════════════════════════════════════════════
# 4. The abandoned sweep: one read a tick
# ══════════════════════════════════════════════════════════════

_BASE = {
    "visitor_name": "Diana", "visitor_phone": "+12125550142", "visitor_email": "",
    "visitor_phone_country": "US", "tunnel": "sales", "message_count": 4,
    "metadata": {}, "visitor_id": "v1",
}


def _cand(cid, *, status="closed", mode="ai", last_msg_minutes=180, in_crm=None, created_minutes=240, **extra):
    row = dict(_BASE, id=cid, status=status, mode=mode, created_at=_iso(created_minutes), **extra)
    if last_msg_minutes is not _OMIT:
        row["messages"] = [] if last_msg_minutes is None else [{"created_at": _iso(last_msg_minutes), "role": "user"}]
    if in_crm is not _OMIT:
        row["leads"] = [] if in_crm is None else [{"created_in_crm": in_crm}]
    return row


_OMIT = object()


@pytest.fixture
def _scan_health():
    before = dict(sb.ABANDONED_SCAN_HEALTH)
    for k in sb.ABANDONED_SCAN_HEALTH:
        sb.ABANDONED_SCAN_HEALTH[k] = 0
    yield sb.ABANDONED_SCAN_HEALTH
    sb.ABANDONED_SCAN_HEALTH.update(before)


class TestSweepScan:
    @staticmethod
    async def _scan(*answers):
        run = AsyncMock(side_effect=list(answers))
        with patch.object(sb, "get_client", return_value=MagicMock()), patch.object(sb, "_run_sync", new=run):
            out = await sb.get_abandoned_conversations(30)
        return out, run

    @pytest.mark.asyncio
    async def test_a_page_costs_one_read(self, _scan_health):
        page = [
            _cand("done-closed", in_crm=True),
            _cand("quiet", in_crm=False),
            _cand("fresh", in_crm=False, last_msg_minutes=5),
            _cand("form-only", in_crm=None, last_msg_minutes=None),
            _cand("form-just-now", in_crm=None, last_msg_minutes=None, created_minutes=3),
        ]
        out, run = await self._scan(SimpleNamespace(data=page))
        assert run.await_count == 1
        assert [c["id"] for c in out] == ["done-closed", "quiet", "form-only"]
        by = {c["id"]: c for c in out}
        assert by["done-closed"]["_in_crm"] is True
        assert by["quiet"]["_in_crm"] is False
        assert by["form-only"]["_in_crm"] is False and by["form-only"]["_no_engagement"] is True
        # The row keeps the shape the sweep and the CRM payload always had.
        assert all("messages" not in c and "leads" not in c for c in out)
        assert _scan_health == {"scans": 1, "embedded": 1, "embedded_failed": 0, "message_reads": 0}

    @pytest.mark.asyncio
    async def test_a_lead_embedded_as_one_object_is_read_too(self):
        row = _cand("one", in_crm=_OMIT)
        row["leads"] = {"created_in_crm": True}
        none = _cand("none", in_crm=_OMIT)
        none["leads"] = None
        out, _ = await self._scan(SimpleNamespace(data=[row, none]))
        assert [(c["id"], c["_in_crm"]) for c in out] == [("one", True), ("none", False)]

    @pytest.mark.asyncio
    async def test_a_refused_read_falls_back_to_asking_each_conversation(self, _scan_health, caplog):
        plain = [_cand("a", last_msg_minutes=_OMIT, in_crm=_OMIT), _cand("b", last_msg_minutes=_OMIT, in_crm=_OMIT)]
        out, run = await self._scan(
            RuntimeError("PGRST200 could not find a relationship"),
            SimpleNamespace(data=plain),
            SimpleNamespace(data=[{"created_at": _iso(120), "role": "user"}]),   # a: quiet for 2 h
            SimpleNamespace(data=[{"created_at": _iso(2), "role": "user"}]),     # b: 2 minutes ago
        )
        assert [c["id"] for c in out] == ["a"]
        assert "_in_crm" not in out[0]          # unknown: the sweep reads the lead itself
        assert run.await_count == 4
        assert _scan_health["embedded_failed"] == 1 and _scan_health["message_reads"] == 2
        assert any("one-read scan was refused" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_both_reads_failing_answers_nothing_and_says_so(self, caplog):
        out, _ = await self._scan(RuntimeError("down"), RuntimeError("down"))
        assert out == []
        assert any("get_abandoned_conversations query error" in r.getMessage() for r in caplog.records)

    def test_the_scan_asks_for_the_newest_message_and_the_lead_flag(self):
        """The request itself, as PostgREST receives it."""
        from postgrest import SyncPostgrestClient

        seen = {}

        class _Sent(Exception):
            pass

        def _capture(fn, *a, **k):
            import postgrest._sync.request_builder as rb

            def _exec(self):
                seen["params"] = str(self.params)
                raise _Sent()

            with patch.object(rb.SyncSelectRequestBuilder, "execute", new=_exec):
                try:
                    fn()
                except _Sent:
                    pass
            return SimpleNamespace(data=[])

        client = MagicMock()
        client.table = SyncPostgrestClient("https://x.invalid/rest/v1", headers={}).from_
        with patch.object(sb, "get_client", return_value=client), \
             patch.object(sb, "_run_sync", new=AsyncMock(side_effect=_capture)):
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(sb.get_abandoned_conversations(30))
        from urllib.parse import unquote

        params = unquote(seen["params"])
        assert "messages(created_at, role)" in params
        assert "leads(created_in_crm)" in params
        # Newest message per conversation: order and limit INSIDE the embedded
        # rows, the way the list's previews ask (a related `order=` is a 400).
        assert "&messages.limit=1" in params and "&messages.order=created_at.desc" in params
        assert "order=messages(" not in params
        # The page itself is the one the sweep always read.
        assert "&order=created_at.desc&limit=200" in params
        assert "status=in.(active,needs_agent,closed)" in params and "mode=in.(ai,human)" in params
        assert "metadata->>closed_by_agent_id=is.null" in params and "tunnel=eq.sales" in params


class TestSweepSkipsWhatIsDone:
    @staticmethod
    async def _sweep(convs, lead):
        reader = AsyncMock(return_value=lead)
        with patch.object(cron.db, "get_abandoned_conversations", new=AsyncMock(return_value=convs)), \
             patch.object(cron, "get_or_create_lead", new=reader), \
             patch.object(cron, "_persist_refusal", new=AsyncMock()), \
             patch.object(cron, "_contacts_already_in_crm", new=AsyncMock(return_value=set())), \
             patch.object(cron, "submit_abandoned_to_crm", new=AsyncMock(return_value=SimpleNamespace(success=False, error="gate:test", request_id=None))), \
             patch.object(cron.db, "update_conversation", new=AsyncMock()), \
             patch.object(cron.db, "add_message", new=AsyncMock()), \
             patch.object(cron.settings, "abandoned_cron_enabled", True):
            out = await cron.run_abandoned_crm()
        return {r["id"]: r["status"] for r in out["results"]}, reader

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,mode", [("closed", "ai"), ("closed", "human"), ("active", "human")])
    async def test_in_the_crm_and_not_ours_to_close_needs_no_lead_read(self, status, mode):
        conv = dict(_BASE, id="c1", status=status, mode=mode, created_at=_iso(300), _in_crm=True)
        got, reader = await self._sweep([conv], {"id": "L1", "created_in_crm": True})
        assert got == {"c1": "already_done"}
        reader.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("hint", [False, None, "absent"])
    async def test_anything_else_reads_the_lead_as_before(self, hint):
        conv = dict(_BASE, id="c1", status="closed", mode="ai", created_at=_iso(300))
        if hint != "absent":
            conv["_in_crm"] = hint
        got, reader = await self._sweep([conv], {"id": "L1", "created_in_crm": True})
        reader.assert_awaited_once_with("c1")
        assert got == {"c1": "already_done"}

    @pytest.mark.asyncio
    async def test_an_open_ai_chat_already_in_the_crm_is_still_closed_by_the_sweep(self):
        """The zombie: CRM done, conversation still open. The hint must not
        skip the one row the sweep has work on."""
        conv = dict(_BASE, id="c1", status="active", mode="ai", created_at=_iso(300), _in_crm=True)
        got, reader = await self._sweep([conv], {"id": "L1", "created_in_crm": True})
        reader.assert_awaited_once_with("c1")
        assert got == {"c1": "closed_existing_crm"}

    @pytest.mark.asyncio
    async def test_a_bad_phone_is_still_reported_first(self):
        conv = dict(_BASE, id="c1", status="closed", mode="ai", created_at=_iso(300), _in_crm=True, visitor_phone="12")
        got, _ = await self._sweep([conv], {"id": "L1", "created_in_crm": True})
        assert got == {"c1": "skipped"}


# ══════════════════════════════════════════════════════════════
# 5. Online minutes: a failed read is not a new day
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def _presence_state():
    before = dict(presence.PRESENCE_HEALTH)
    for k in presence.PRESENCE_HEALTH:
        presence.PRESENCE_HEALTH[k] = 0
    presence._last_tick.clear()
    yield presence.PRESENCE_HEALTH
    presence.PRESENCE_HEALTH.update(before)
    presence._last_tick.clear()


def _presence_db(*, row=None, read_error=None, insert_error=None, update_error=None):
    db = MagicMock()
    db.read_presence_day = AsyncMock(return_value=row, side_effect=read_error)
    db.insert_presence_day = AsyncMock(side_effect=insert_error)
    db.update_presence_day = AsyncMock(side_effect=update_error)
    return db


class _DbSays(Exception):
    """Shaped like PostgREST's APIError: the database's code, and a text that
    can quote the row."""
    def __init__(self, code, text=""):
        super().__init__(text)
        self.code = code


class TestPresenceTick:
    @pytest.mark.asyncio
    async def test_the_first_tick_of_the_day_starts_it(self, _presence_state):
        db = _presence_db(row=None)
        await presence.record_presence_tick("u1", db)
        db.read_presence_day.assert_awaited_once()
        (user, day, fields), _ = db.insert_presence_day.await_args
        assert user == "u1" and day == datetime.now(timezone.utc).date().isoformat()
        assert fields["minutes_online"] == 0 and fields["heartbeat_ticks"] == 1
        assert fields["first_seen_at"] == fields["last_seen_at"]
        db.update_presence_day.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_later_tick_adds_to_the_day_and_never_moves_its_start(self, _presence_state):
        db = _presence_db(row={"first_seen_at": _iso(200), "last_seen_at": _iso(1.5), "minutes_online": 41, "heartbeat_ticks": 40})
        await presence.record_presence_tick("u1", db)
        db.read_presence_day.assert_awaited_once()          # one read, not two
        (_, _, fields), _ = db.update_presence_day.await_args
        assert "first_seen_at" not in fields
        assert fields["minutes_online"] == 43 and fields["heartbeat_ticks"] == 41   # 1.5 min → 2
        db.insert_presence_day.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_read_writes_nothing(self, _presence_state):
        """It used to be read as "no row yet": first_seen_at = now, minutes = 0,
        over a day already counted."""
        db = _presence_db(read_error=RuntimeError("Server disconnected"))
        await presence.record_presence_tick("u1", db)
        db.insert_presence_day.assert_not_awaited()
        db.update_presence_day.assert_not_awaited()
        assert _presence_state["read_failed"] == 1

    @pytest.mark.asyncio
    async def test_two_tabs_starting_the_same_day_leave_one_row(self, _presence_state):
        db = _presence_db(row=None, insert_error=_DbSays("23505", "duplicate key value violates unique constraint"))
        await presence.record_presence_tick("u1", db)
        assert _presence_state["started_twice"] == 1 and _presence_state["write_failed"] == 0

    @pytest.mark.asyncio
    async def test_another_error_that_only_quotes_those_digits_is_a_failed_write(self, _presence_state, caplog):
        """A foreign-key refusal quoting an id with 23505 in it is not "the day
        exists" — and the id it quotes stays out of the log."""
        quoted = 'Key (user_id)=(0a123505-77aa-4b1c-9d0e-5f6a7b8c9d0e) is not present in table "users"'
        db = _presence_db(row=None, insert_error=_DbSays("23503", quoted))
        with caplog.at_level("WARNING"):
            await presence.record_presence_tick("u1", db)
        assert _presence_state["write_failed"] == 1 and _presence_state["started_twice"] == 0
        line = "\n".join(r.getMessage() for r in caplog.records)
        assert "_DbSays 23503" in line and "0a123505" not in line

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kw", [{"row": None, "insert_error": RuntimeError("timeout")},
                                    {"row": {"last_seen_at": _iso(2), "minutes_online": 1, "heartbeat_ticks": 1}, "update_error": RuntimeError("timeout")}])
    async def test_a_failed_write_is_counted_not_raised(self, _presence_state, kw):
        await presence.record_presence_tick("u1", _presence_db(**kw))
        assert _presence_state["write_failed"] == 1

    @pytest.mark.asyncio
    async def test_ticks_inside_the_minute_cost_nothing(self, _presence_state):
        db = _presence_db(row=None)
        await presence.record_presence_tick("u1", db)
        await presence.record_presence_tick("u1", db)
        assert db.read_presence_day.await_count == 1

    @pytest.mark.asyncio
    async def test_the_strict_read_raises_where_the_lenient_one_answers_none(self):
        boom = AsyncMock(side_effect=RuntimeError("Server disconnected"))
        with patch.object(sb, "get_client", return_value=MagicMock()), patch.object(sb, "_run_sync", new=boom):
            assert await sb.get_presence_day("u1", "2026-11-10") is None
            with pytest.raises(RuntimeError):
                await sb.read_presence_day("u1", "2026-11-10")


# ══════════════════════════════════════════════════════════════
# 6. Search by meaning: the refusal says why
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def _qdrant_state():
    before = dict(qdrant.QDRANT_HEALTH)
    qdrant.QDRANT_HEALTH.update({"searches": 0, "failed": 0, "last_status": None, "last_error": None, "refused_because": None})
    logged = qdrant._QUERY_BODY_LOGGED
    qdrant._QUERY_BODY_LOGGED = False
    yield qdrant.QDRANT_HEALTH
    qdrant.QDRANT_HEALTH.update(before)
    qdrant._QUERY_BODY_LOGGED = logged


def _qdrant_client(*answers):
    """A client whose posts are answered in turn: (status, body) pairs."""
    client = MagicMock()
    request = httpx.Request("POST", "https://q.invalid/collections/kb_entries/points/query")
    client.post.side_effect = [httpx.Response(st, text=body, request=request) for st, body in answers]
    return client


def _sent_texts(client):
    return [c.kwargs["json"]["query"]["text"] for c in client.post.call_args_list]


_INDEX = '{"status":{"error":"Bad request: Index required but not found for \\"tunnel\\""},"time":0.0}'


class TestKbSearchSaysWhy:
    def test_a_400_is_counted_and_the_reason_is_asked_for_once(self, _qdrant_state, caplog):
        client = _qdrant_client((400, _INDEX), (400, _INDEX), (400, _INDEX))
        with patch.object(qdrant, "_get_client", return_value=client), caplog.at_level("WARNING"):
            assert qdrant._search_sync("do you fly to Rome", tunnel="sales") == []
            assert qdrant._search_sync("do you fly to Rome", tunnel="sales") == []
        assert _qdrant_state["searches"] == 2 and _qdrant_state["failed"] == 2
        assert _qdrant_state["last_status"] == 400 and _qdrant_state["last_error"] == "HTTP 400"
        assert _qdrant_state["refused_because"].startswith("HTTP 400: ") and "Index required" in _qdrant_state["refused_because"]
        # Two searches and ONE probe; the probe carries our text, and the same filter.
        assert _sent_texts(client) == ["do you fly to Rome", qdrant._PROBE_TEXT, "do you fly to Rome"]
        assert client.post.call_args_list[1].kwargs["json"]["filter"] == client.post.call_args_list[0].kwargs["json"]["filter"]
        said = [r.getMessage() for r in caplog.records if "what Qdrant answers" in r.getMessage()]
        assert len(said) == 1 and "Index required" in said[0]

    def test_what_qdrant_says_about_the_visitors_message_is_never_kept(self, _qdrant_state, caplog):
        """The answer to the visitor's request can quote it in another shape
        (spaces, case, cut short): it is not cleaned, it is not used."""
        echo = '{"status":{"error":"cannot embed: MY PHONE IS 212-555-0142, i am Dana"}}'
        client = _qdrant_client((400, echo), (400, _INDEX))
        with patch.object(qdrant, "_get_client", return_value=client), caplog.at_level("DEBUG"):
            qdrant._search_sync("my phone is 212 555 0142, I am Dana", tunnel="sales")
        everything = "\n".join(r.getMessage() for r in caplog.records) + repr(dict(_qdrant_state))
        for words in ("212", "0142", "Dana", "DANA", "my phone", "MY PHONE"):
            assert words not in everything
        assert "<35 chars>" in everything                 # what was sent, by its length
        assert "Index required" in _qdrant_state["refused_because"]

    def test_our_text_accepted_means_the_refusal_was_about_the_text(self, _qdrant_state):
        client = _qdrant_client((400, '{"status":{"error":"too long"}}'), (200, '{"result":{"points":[]}}'))
        with patch.object(qdrant, "_get_client", return_value=client):
            qdrant._search_sync("x" * 9000, tunnel="sales")
        assert _qdrant_state["refused_because"].startswith("HTTP 200 for our own text")

    def test_a_probe_that_cannot_be_sent_is_said_and_does_not_break_the_turn(self, _qdrant_state):
        client = MagicMock()
        request = httpx.Request("POST", "https://q.invalid/collections/kb_entries/points/query")
        client.post.side_effect = [httpx.Response(400, text=_INDEX, request=request), httpx.ConnectError("down")]
        with patch.object(qdrant, "_get_client", return_value=client):
            assert qdrant._search_sync("do you fly to Rome", tunnel="sales") == []
        assert _qdrant_state["refused_because"] == "probe not sent (ConnectError)"

    def test_a_working_search_logs_how_long_the_question_was_not_what_it_said(self, _qdrant_state, caplog):
        ok = '{"result":{"points":[{"id":"e1","score":0.91,"payload":{"title":"Payment","content":"Visa"}}]}}'
        with patch.object(qdrant, "_get_client", return_value=_qdrant_client((200, ok))), caplog.at_level("INFO"):
            qdrant._search_sync("call me on 212 555 0142", tunnel="sales")
        line = "\n".join(r.getMessage() for r in caplog.records)
        assert "query of 23 chars" in line and "212" not in line

    def test_a_search_that_works_counts_no_failure(self, _qdrant_state):
        ok = '{"result":{"points":[{"id":"e1","score":0.91,"payload":{"title":"Payment","content":"Visa"}}]}}'
        with patch.object(qdrant, "_get_client", return_value=_qdrant_client((200, ok))):
            got = qdrant._search_sync("can I pay by card", tunnel="sales")
        assert [g["id"] for g in got] == ["e1"]
        assert _qdrant_state == {"searches": 1, "failed": 0, "last_status": None, "last_error": None, "refused_because": None}

    def test_a_timeout_is_counted_without_a_status(self, _qdrant_state):
        client = MagicMock()
        client.post.side_effect = httpx.ReadTimeout("timed out")
        with patch.object(qdrant, "_get_client", return_value=client):
            assert qdrant._search_sync("anything", tunnel="sales") == []
        assert _qdrant_state["failed"] == 1 and _qdrant_state["last_status"] is None
        assert _qdrant_state["last_error"] == "ReadTimeout" and _qdrant_state["refused_because"] is None
        assert client.post.call_count == 1                 # a timeout asks no second question

    def test_not_configured_is_not_a_search(self, _qdrant_state):
        with patch.object(qdrant, "_get_client", return_value=None):
            assert qdrant._search_sync("anything") == []
        assert _qdrant_state["searches"] == 0


# ══════════════════════════════════════════════════════════════
# 7. Sign-in: "could not check" is not "no such account"
# ══════════════════════════════════════════════════════════════

class _NoRows(Exception):
    """Shaped like PostgREST's APIError for .single() with no match."""
    code = "PGRST116"


@pytest.fixture
def _lookup_health():
    before = dict(sb.USER_LOOKUP_HEALTH)
    sb.USER_LOOKUP_HEALTH["failed"] = 0
    yield sb.USER_LOOKUP_HEALTH
    sb.USER_LOOKUP_HEALTH.update(before)


def _users_read(answer):
    """The users table answering one way: a row, nobody, or an error."""
    run = AsyncMock(side_effect=answer) if isinstance(answer, Exception) else AsyncMock(return_value=SimpleNamespace(data=answer))
    return [patch.object(sb, "get_client", return_value=MagicMock()), patch.object(sb, "_run_sync", new=run)]


class TestUserLookup:
    @pytest.mark.asyncio
    async def test_nobody_at_that_address_is_not_an_error(self, _lookup_health, caplog):
        p = _users_read(_NoRows("JSON object requested, multiple (or no) rows returned"))
        with p[0], p[1]:
            assert await sb.get_user_by_email("ghost@bbc.test") is None
            assert sb.user_lookup_failed() is False
        assert _lookup_health["failed"] == 0
        assert not [r for r in caplog.records if r.levelname == "ERROR"]

    @pytest.mark.asyncio
    async def test_a_read_that_failed_says_so(self, _lookup_health):
        p = _users_read(RuntimeError("Server disconnected"))
        with p[0], p[1]:
            assert await sb.get_user_by_email("real@bbc.test") is None
            assert sb.user_lookup_failed() is True
        assert _lookup_health["failed"] == 1

    @pytest.mark.asyncio
    async def test_the_error_line_says_the_kind_never_the_text(self, _lookup_health, caplog):
        """An error about this request can quote the address that was asked for."""
        p = _users_read(_DbSays("57014", "canceling statement: ...email=eq.real@bbc.test"))
        with p[0], p[1], caplog.at_level("ERROR"):
            assert await sb.get_user_by_email("real@bbc.test") is None
            assert sb.user_lookup_failed() is True
        line = "\n".join(r.getMessage() for r in caplog.records)
        assert "get_user_by_email error: _DbSays 57014" in line and "real@bbc.test" not in line

    @pytest.mark.asyncio
    async def test_a_text_that_only_mentions_no_rows_is_still_a_failed_read(self, _lookup_health):
        p = _users_read(RuntimeError("upstream said PGRST116 somewhere in its page"))
        with p[0], p[1]:
            assert await sb.get_user_by_email("real@bbc.test") is None
            assert sb.user_lookup_failed() is True

    @pytest.mark.asyncio
    async def test_the_next_lookup_starts_clean(self, _lookup_health):
        p = _users_read(RuntimeError("Server disconnected"))
        with p[0], p[1]:
            await sb.get_user_by_email("real@bbc.test")
        p = _users_read({"id": "u1", "email": "real@bbc.test"})
        with p[0], p[1]:
            assert (await sb.get_user_by_email("real@bbc.test"))["id"] == "u1"
            assert sb.user_lookup_failed() is False


class TestSignInTellsTheTruth:
    @pytest.fixture
    def login(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api import auth_routes
        from app.security import login_guard
        from app.security.rate_limiter import check_rate_limit

        login_guard._reset_for_tests()
        monkeypatch.setattr("app.services.response_cache._get_redis", lambda: None)
        noted = AsyncMock()
        monkeypatch.setattr("app.security.ban_guard.note_login_failure", noted)
        app = FastAPI()
        app.include_router(auth_routes.router)
        app.dependency_overrides[check_rate_limit] = lambda: None
        yield TestClient(app), noted, login_guard
        login_guard._reset_for_tests()

    def test_login_while_the_users_table_cannot_be_read_is_a_503_and_no_strike(self, login, _lookup_health):
        client, noted, guard = login
        p = _users_read(RuntimeError("Server disconnected"))
        with p[0], p[1]:
            for _ in range(8):      # more than the backoff allows for wrong passwords
                r = client.post("/api/auth/login", json={"email": "real@bbc.test", "password": "right-one"})
                assert r.status_code == 503 and "try again" in r.json()["detail"]
        noted.assert_not_awaited()
        assert guard.retry_after("real@bbc.test", "testclient") == 0
        assert _lookup_health["failed"] == 8

    def test_login_with_an_unknown_address_is_still_a_401_and_a_strike(self, login):
        client, noted, _ = login
        p = _users_read(_NoRows("no rows"))
        with p[0], p[1]:
            r = client.post("/api/auth/login", json={"email": "ghost@bbc.test", "password": "whatever"})
        assert r.status_code == 401 and r.json()["detail"] == "Invalid email or password"
        noted.assert_awaited_once()

    @pytest.fixture
    def sso(self):
        import jwt as _jwt
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api import auth_routes
        from app.security.rate_limiter import check_rate_limit
        from config.settings import settings

        app = FastAPI()
        app.include_router(auth_routes.router)
        app.dependency_overrides[check_rate_limit] = lambda: None
        now = datetime.now(timezone.utc)
        token = _jwt.encode(
            {"iss": "crm", "sub": "123", "email": "agent@bbc.test", "iat": now,
             "nbf": now - timedelta(seconds=5), "exp": now + timedelta(seconds=900)},
            "shared-with-the-crm", algorithm="HS256",
        )
        before = dict(auth_routes.SSO_HEALTH)
        with patch.object(settings, "chat_sso_secret", "shared-with-the-crm"), \
             patch.object(settings, "jwt_secret", "ours-only"):
            yield TestClient(app), token, auth_routes.SSO_HEALTH, before
        auth_routes.SSO_HEALTH.update(before)

    def test_the_crm_sign_in_says_try_again_not_no_account(self, sso, _lookup_health):
        client, token, health, before = sso
        p = _users_read(RuntimeError("EOF occurred in violation of protocol"))
        with p[0], p[1]:
            r = client.post("/api/auth/sso/crm-exchange", json={"token": token})
        # 502 on purpose: the panel words a 503 from this endpoint as "sign-in
        # from the CRM isn't switched on" (bbc-admin-app features/auth/view.ts).
        assert r.status_code == 502 and "No matching" not in r.json()["detail"]
        assert health["lookup_failed"] == before["lookup_failed"] + 1
        assert health["unknown_user"] == before["unknown_user"]

    def test_the_crm_sign_in_for_someone_without_an_account_is_still_a_401(self, sso):
        client, token, health, before = sso
        p = _users_read(_NoRows("no rows"))
        with p[0], p[1]:
            r = client.post("/api/auth/sso/crm-exchange", json={"token": token})
        assert r.status_code == 401 and r.json()["detail"] == "No matching BBC account for this user"
        assert health["unknown_user"] == before["unknown_user"] + 1
        assert health["lookup_failed"] == before["lookup_failed"]


# ══════════════════════════════════════════════════════════════
# 8. /health/detail carries the new counters
# ══════════════════════════════════════════════════════════════

def test_health_detail_reports_the_new_counters():
    from app.api import health

    with patch.object(health, "get_scheduler_health", return_value={}), \
         patch.object(health, "supervisor_columns_status", return_value={}):
        out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(health.health_detail(full=False))
    assert set(out["kb_search"]) == {"searches", "failed", "last_status", "last_error", "refused_because"}
    assert set(out["presence_ticks"]) == {"ticks", "read_failed", "write_failed", "started_twice"}
    assert set(out["abandoned_scan"]) == {"scans", "embedded", "embedded_failed", "message_reads"}
    assert set(out["user_lookup"]) == {"failed"}
    assert "lookup_failed" in out["sso"]
