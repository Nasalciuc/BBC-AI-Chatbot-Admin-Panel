"""The visit registry, the API's side (contracts/live.md "The visit registry"; migration 056).

bbc-live's Go suite holds visit.lua and close.lua against a real Redis (registry_test.go); here the queue they leave
is taken into rows: what a row holds, what is left out, and that the queue loses nothing whatever the database
says. Every id, address and place here is invented.
"""

import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.infra import redis_client  # noqa: E402
from app.services import visit_registry as reg  # noqa: E402
from tests.fake_redis import FakeRedis  # noqa: E402

VID = "3f1c2a7e-8b4d-4c1e-9a2f-6d5e4c3b2a10"
OTHER_VID = "4a2d3b8f-9c5e-4d2f-8b3a-7e6f5d4c3b21"
CHAT = "0b5e2c1a-7f3d-4e8a-9b6c-2d4f6a8c0e12"
LATER_CHAT = "9c8b7a6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
T0 = 1795600000
KEY = f"live:ended:{VID}:{T0}"


def iso(unix: int) -> str:
    return datetime.fromtimestamp(unix, tz=timezone.utc).isoformat()


def page(path, kind, s, seen, ev, st, at):
    return json.dumps({"path": path, "type": kind, "s": s, "seen": seen, "ev": ev, "st": st, "at": at})


def visit(**over):
    h = {
        "first_at": str(T0), "last_at": str(T0 + 95), "pages": "pg-first-1,pg-second-2",
        "pg:pg-first-1": page("/flights/new-york-istanbul", "route", 60, 50, ["card:kayak:card", "card_yes:yes"],
                              ["search:JFK-IST"], T0 + 60),
        "pg:pg-second-2": page("/quote", "quote", 30, 30, ["chat_msg*2"], ["quote_opened"], T0 + 95),
        "landing": "/flights/new-york-istanbul", "landing_type": "route", "last_page": "/quote",
        "source": "kayak", "channel": "kayak", "utm_source": "kayak", "utm_medium": "cpc",
        "utm_campaign": "us-route", "referrer": "www.kayak.com", "device": "phone", "site": "bbc",
        "lang": "en-US", "tz": "America/New_York", "country": "US", "ip": "203.0.113.7",
        "ua": "Mozilla/5.0 (iPhone)", "ph_session": "01937a2b-4c5d-7e6f-8a9b-0c1d2e3f4a5b",
        "from": "JFK", "to": "IST", "depart": "2026-12-14", "cabin": "business", "pax": "2",
        "card_trigger": "kayak", "card_variant": "beat", "card_surface": "card", "card_state": "engaged",
        "card_at": str(T0 + 5), "card_answer_at": str(T0 + 9), "card_answer": "yes",
        "has_chat": "1", "conversation_id": CHAT,
    }
    h.update(over)
    return {k: v for k, v in h.items() if v is not None}


@pytest.fixture(autouse=True)
def fresh_counters():
    for k in reg.VISIT_HEALTH:
        reg.VISIT_HEALTH[k] = None if k == "queued" else 0
    yield


@pytest.fixture
def fake():
    r = FakeRedis()
    redis_client.set_client_for_tests(r)
    with patch("config.settings.settings.redis_url", "redis://fake"):
        yield r
    redis_client.set_client_for_tests(None)


# ── the row ─────────────────────────────────────────────────────────


def test_a_visit_is_one_row_with_its_pages_in_order():
    row = reg.row_of(KEY, visit())
    assert row["visitor_id"] == VID
    assert row["started_at"] == iso(T0) and row["ended_at"] == iso(T0 + 95) and row["seconds_on_site"] == 95
    assert row["seconds_seen"] == 80 and row["pages"] == 2 and row["pages_over"] == 0
    assert [p["path"] for p in row["page_list"]] == ["/flights/new-york-istanbul", "/quote"]
    assert row["page_list"][0]["id"] == "pg-first-1"
    assert row["events"] == ["card:kayak:card", "card_yes:yes", "chat_msg*2"]
    assert row["steps"] == ["search:JFK-IST", "quote_opened"]
    assert {k: row[k] for k in ("landing_page", "landing_type", "last_page", "source", "channel", "utm_source",
                                "utm_medium", "utm_campaign", "referrer_host", "device", "site", "lang", "tz",
                                "country", "user_agent", "posthog_session_id")} == {
        "landing_page": "/flights/new-york-istanbul", "landing_type": "route", "last_page": "/quote",
        "source": "kayak", "channel": "kayak", "utm_source": "kayak", "utm_medium": "cpc",
        "utm_campaign": "us-route", "referrer_host": "www.kayak.com", "device": "phone", "site": "bbc",
        "lang": "en-US", "tz": "America/New_York", "country": "US", "user_agent": "Mozilla/5.0 (iPhone)",
        "posthog_session_id": "01937a2b-4c5d-7e6f-8a9b-0c1d2e3f4a5b"}
    assert row["ip"] == "203.0.113.7"
    assert (row["trip_from"], row["trip_to"], row["depart_date"], row["return_date"], row["cabin"], row["pax"]) == (
        "JFK", "IST", "2026-12-14", None, "business", 2)
    assert (row["card_trigger"], row["card_variant"], row["card_surface"], row["card_state"], row["card_answer"]) == (
        "kayak", "beat", "card", "engaged", "yes")
    assert row["card_at"] == iso(T0 + 5) and row["card_answer_at"] == iso(T0 + 9)
    assert row["has_chat"] is True and row["conversation_id"] == CHAT
    # No contact is copied: the view reads them from the chat and the lead.
    assert not {"visitor_name", "visitor_email", "visitor_phone", "email", "phone", "name"} & set(row)


def test_what_cannot_be_vouched_for_is_left_out():
    row = reg.row_of(KEY, visit(
        ip="not-an-address", depart="2026-02-30", **{"return": "14 Dec"}, pax="12", card_state="loved",
        card_at="soon", conversation_id="not-a-chat", has_chat=None, last_at="x",
        pages="pg-first-1,../etc,pg-second-2", **{"pg:pg-second-2": "{not json"}))
    assert row["ip"] is None and row["depart_date"] is None and row["return_date"] is None and row["pax"] is None
    assert row["card_state"] is None and row["card_at"] is None and row["conversation_id"] is None
    assert row["has_chat"] is False
    # A last time it can't read: the visit is as long as its start.
    assert row["ended_at"] == iso(T0) and row["seconds_on_site"] == 0
    # A page id it doesn't know is not a page; a page that isn't JSON is left out, counted.
    assert row["pages"] == 2 and [p["id"] for p in row["page_list"]] == ["pg-first-1"]
    assert reg.VISIT_HEALTH["bad_pages"] == 1


@pytest.mark.parametrize("key,h", [
    (KEY, {}),                                           # ran out in the queue
    (KEY, {"last_at": str(T0)}),                         # no start
    ("live:v:" + VID, visit()),                          # not one of the registry's keys
    (f"live:ended:{VID}:soon", visit()),
])
def test_nothing_to_store_is_no_row(key, h):
    assert reg.row_of(key, h) is None


def test_a_visit_ended_twice_in_one_second_keeps_its_visitor():
    assert reg.row_of(f"{KEY}:{T0 + 1800}", visit())["visitor_id"] == VID


# ── the job ─────────────────────────────────────────────────────────


def _queue(fake, *items):
    for key, h in items:
        if h:
            fake.kv[key] = dict(h)
        fake.kv.setdefault(reg.ENDED_KEY, []).append(key)


@pytest.mark.asyncio
async def test_the_job_stores_the_queue_and_takes_off_only_what_it_stored(fake):
    other = f"live:ended:{OTHER_VID}:{T0 + 10}"
    lost = f"live:ended:{OTHER_VID}:{T0 - 99}"
    _queue(fake, (KEY, visit()), (lost, None), (other, visit(has_chat=None, conversation_id=None)))
    store = AsyncMock(return_value="stored")
    with patch("app.db.supabase.store_site_visits", new=store), \
            patch("app.db.supabase.chats_of_visitors", new=AsyncMock()) as chats:
        out = await reg.run_visit_registry()
    assert out == {"state": "stored", "rows": 2, "lost": 1, "queued": 3}
    rows = store.await_args.args[0]
    assert [r["visitor_id"] for r in rows] == [VID, OTHER_VID]
    chats.assert_not_awaited()  # every chat was named, or there was none: nothing to look up
    assert fake.kv[reg.ENDED_KEY] == [] and KEY not in fake.kv and other not in fake.kv
    assert reg.VISIT_HEALTH["rows"] == 2 and reg.VISIT_HEALTH["lost"] == 1 and reg.VISIT_HEALTH["runs"] == 1


@pytest.mark.asyncio
async def test_what_is_queued_meanwhile_waits_for_the_next_run(fake):
    """Two schedulers run during a deploy: each takes off the queue only the keys it stored, never a range."""
    _queue(fake, (KEY, visit()))
    late = f"live:ended:{OTHER_VID}:{T0 + 50}"

    async def store(rows):
        _queue(fake, (late, visit()))  # bbc-live ends another visit while the database writes
        return "stored"

    with patch("app.db.supabase.store_site_visits", new=AsyncMock(side_effect=store)):
        await reg.run_visit_registry()
    assert fake.kv[reg.ENDED_KEY] == [late] and late in fake.kv


@pytest.mark.parametrize("db_says,state", [("table_missing", "table_missing"), ("failed", "store_failed")])
@pytest.mark.asyncio
async def test_a_table_not_there_or_a_failed_write_keeps_the_queue(fake, db_says, state):
    _queue(fake, (KEY, visit()))
    with patch("app.db.supabase.store_site_visits", new=AsyncMock(return_value=db_says)):
        out = await reg.run_visit_registry()
    assert out["state"] == state and reg.VISIT_HEALTH[state] == 1
    assert fake.kv[reg.ENDED_KEY] == [KEY] and KEY in fake.kv, "the next run tries again"
    assert reg.VISIT_HEALTH["rows"] == 0


@pytest.mark.asyncio
async def test_an_empty_queue_is_quiet_and_asks_no_database(fake):
    with patch("app.db.supabase.store_site_visits", new=AsyncMock()) as store:
        assert await reg.run_visit_registry() == {"state": "empty"}
    store.assert_not_awaited()
    assert reg.VISIT_HEALTH["runs"] == 0 and reg.VISIT_HEALTH["queued"] == 0


@pytest.mark.asyncio
async def test_a_queue_that_cannot_be_read_is_counted(fake):
    _queue(fake, (KEY, visit()))
    with patch.object(fake, "lrange", new=AsyncMock(side_effect=ConnectionError("down"))):
        assert await reg.run_visit_registry() == {"state": "redis_failed"}
    assert reg.VISIT_HEALTH["redis_failed"] == 1 and fake.kv[reg.ENDED_KEY] == [KEY]


@pytest.mark.asyncio
async def test_no_redis_reads_nothing():
    with patch("app.services.visit_registry.get_redis", return_value=None):
        assert await reg.run_visit_registry() == {"state": "no_redis"}


@pytest.mark.asyncio
async def test_a_chat_the_widget_did_not_name_is_the_visitors_chat_within_the_visit(fake):
    _queue(fake, (KEY, visit(conversation_id=None)))
    found = [
        {"id": LATER_CHAT, "visitor_id": VID, "created_at": iso(T0 + 95 + 3600)},   # after the visit: another one
        {"id": CHAT, "visitor_id": VID, "created_at": iso(T0 + 40).replace("+00:00", "Z")},
    ]
    store = AsyncMock(return_value="stored")
    with patch("app.db.supabase.store_site_visits", new=store), \
            patch("app.db.supabase.chats_of_visitors", new=AsyncMock(return_value=found)) as chats:
        await reg.run_visit_registry()
    # The lookup reaches as far back as a visit takes a chat (CodeRabbit #282, round 3), and no further on than one
    # (round 7: it reads every page, so only what can be a visit's).
    assert chats.await_args.args == ([VID], iso(T0 - 120), iso(T0 + 95 + 1800))
    assert store.await_args.args[0][0]["conversation_id"] == CHAT and reg.VISIT_HEALTH["linked"] == 1


@pytest.mark.asyncio
async def test_a_chat_begun_just_before_the_first_snapshot_is_the_visits_and_one_before_that_is_not(fake):
    _queue(fake, (KEY, visit(conversation_id=None)))
    found = [
        {"id": LATER_CHAT, "visitor_id": VID, "created_at": iso(T0 - 121)},   # Rule 5: before the window
        {"id": CHAT, "visitor_id": VID, "created_at": iso(T0 - 60)},
    ]
    store = AsyncMock(return_value="stored")
    with patch("app.db.supabase.store_site_visits", new=store), \
            patch("app.db.supabase.chats_of_visitors", new=AsyncMock(return_value=found)):
        await reg.run_visit_registry()
    assert store.await_args.args[0][0]["conversation_id"] == CHAT


@pytest.mark.asyncio
async def test_a_lookup_that_fails_stores_the_visit_without_its_chat(fake):
    _queue(fake, (KEY, visit(conversation_id=None)))
    store = AsyncMock(return_value="stored")
    with patch("app.db.supabase.store_site_visits", new=store), \
            patch("app.db.supabase.chats_of_visitors", new=AsyncMock(return_value=None)):
        out = await reg.run_visit_registry()
    assert out["rows"] == 1 and store.await_args.args[0][0]["conversation_id"] is None
    assert reg.VISIT_HEALTH["link_failed"] == 1


# ── the switch ──────────────────────────────────────────────────────


def test_the_registry_is_off_at_merge(monkeypatch):
    monkeypatch.delenv("VISIT_REGISTRY", raising=False)
    with patch("app.services.live_tokens.enabled", return_value=True):
        assert reg.registry_enabled() is False
    monkeypatch.setenv("VISIT_REGISTRY", "true")
    with patch("app.services.live_tokens.enabled", return_value=True):
        assert reg.registry_enabled() is True
    with patch("app.services.live_tokens.enabled", return_value=False):
        assert reg.registry_enabled() is False, "no secret or no bbc-live: nothing to hand out"


def test_the_job_runs_every_minute_and_is_quiet_when_empty():
    from app.services import scheduler

    assert scheduler._QUIET_WHEN["visit_registry"]({"state": "empty"}) is True
    assert scheduler._QUIET_WHEN["visit_registry"]({"state": "stored", "rows": 3}) is False
    assert scheduler._QUIET_WHEN["visit_registry"]({"state": "table_missing"}) is False


# ── the database ────────────────────────────────────────────────────


class _Err(Exception):
    def __init__(self, code):
        super().__init__(f"error {code}")
        self.code = code


def _client(execute):
    """A supabase client whose table(...)… chain ends in `execute`; records the chain's calls."""
    calls = []

    class Q:
        def __getattr__(self, name):
            def step(*a, **kw):
                calls.append((name, a, kw))
                return self
            return step

        def execute(self):
            return execute()

    class C:
        def table(self, name):
            calls.append(("table", (name,), {}))
            return Q()

    return C(), calls


@pytest.mark.asyncio
async def test_a_visit_stored_twice_is_one_row():
    from app.db import supabase as db

    client, calls = _client(lambda: type("R", (), {"data": []})())
    with patch.object(db, "get_client", return_value=client):
        assert await db.store_site_visits([{"visitor_id": VID, "started_at": iso(T0)}]) == "stored"
    upsert = next(c for c in calls if c[0] == "upsert")
    assert upsert[2] == {"on_conflict": "visitor_id,started_at", "ignore_duplicates": True}
    assert ("table", ("site_visits",), {}) in calls


@pytest.mark.parametrize("code,state", [("PGRST205", "table_missing"), ("42P01", "table_missing"),
                                        ("23502", "failed"), (None, "failed")])
@pytest.mark.asyncio
async def test_the_database_says_whether_the_table_is_missing_or_the_write_failed(code, state):
    from app.db import supabase as db

    def boom():
        raise _Err(code)

    client, _ = _client(boom)
    with patch.object(db, "get_client", return_value=client):
        assert await db.store_site_visits([{"visitor_id": VID, "started_at": iso(T0)}]) == state


@pytest.mark.asyncio
async def test_nothing_to_store_asks_no_database():
    from app.db import supabase as db

    with patch.object(db, "get_client", side_effect=AssertionError("no call")):
        assert await db.store_site_visits([]) == "stored"
        assert await db.chats_of_visitors([], iso(T0)) == []


@pytest.mark.asyncio
async def test_a_chat_lookup_that_fails_is_none_not_empty():
    """Rule 7: "no chat" and "could not say" are two answers."""
    from app.db import supabase as db

    def boom():
        raise _Err("57014")

    client, _ = _client(boom)
    with patch.object(db, "get_client", return_value=client):
        assert await db.chats_of_visitors([VID], iso(T0)) is None
    found = [{"id": CHAT, "visitor_id": VID, "created_at": iso(T0)}, {"id": None, "visitor_id": VID}]
    client, calls = _client(lambda: type("R", (), {"data": found})())
    with patch.object(db, "get_client", return_value=client):
        assert await db.chats_of_visitors([VID], iso(T0)) == found[:1]
    assert ("in_", ("visitor_id", [VID]), {}) in calls and ("gte", ("created_at", iso(T0)), {}) in calls
    assert not any(c[0] == "lte" for c in calls), "no end asked for, none set"


@pytest.mark.asyncio
async def test_a_chat_lookup_reads_every_page(monkeypatch):
    """CodeRabbit #282, round 7: a batch after a backlog may have more chats than one page: every page is read, oldest
    first, and no more than _CHATS_MOST."""
    from app.db import supabase as db

    pages = [[{"id": f"c{i}", "visitor_id": VID, "created_at": iso(T0 + i)} for i in range(n)] for n in (500, 500, 3)]
    client, calls = _client(lambda: type("R", (), {"data": pages.pop(0)})())
    with patch.object(db, "get_client", return_value=client):
        out = await db.chats_of_visitors([VID], iso(T0), iso(T0 + 3600))
    assert len(out) == 1003 and not pages
    assert [c[1] for c in calls if c[0] == "range"] == [(0, 499), (500, 999), (1000, 1499)]
    assert ("lte", ("created_at", iso(T0 + 3600)), {}) in calls
    assert [c[1] for c in calls if c[0] == "order"][:2] == [("created_at",), ("id",)], "a page's order is stable"
    # Rule 5: past the most it reads, it stops (and says so in the log).
    monkeypatch.setattr(db, "_CHATS_MOST", 1000)
    full = [{"id": f"c{i}", "visitor_id": VID, "created_at": iso(T0 + i)} for i in range(500)]
    client, calls = _client(lambda: type("R", (), {"data": full})())
    with patch.object(db, "get_client", return_value=client):
        assert len(await db.chats_of_visitors([VID], iso(T0))) == 1000
    assert len([c for c in calls if c[0] == "range"]) == 2
