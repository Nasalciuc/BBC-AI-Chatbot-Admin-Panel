"""The heartbeat's presence write: Redis on every beat, Supabase every HEARTBEAT_DB_EVERY_S."""

import time
from unittest.mock import AsyncMock, patch

import pytest

from app.infra import redis_client
from app.services import agent_presence as ap
from tests.fake_redis import FakeRedis

_OP = {"id": "u1", "role": "sales", "is_active": True, "chat_enabled": True, "tunnel_scope": "sales", "is_ready": True}


@pytest.fixture
def fake(monkeypatch):
    from config.settings import settings

    r = FakeRedis()
    monkeypatch.setattr(settings, "redis_url", "redis://fake")
    redis_client.set_client_for_tests(r)
    yield r


@pytest.fixture
def clock(monkeypatch):
    """time.monotonic as the test says."""
    now = [1000.0]
    monkeypatch.setattr(ap.time, "monotonic", lambda: now[0])
    return now


def _writes(row=None):
    return patch("app.db.supabase.update_user_last_seen", new=AsyncMock(return_value=dict(row or _OP)))


@pytest.mark.asyncio
async def test_beats_inside_the_window_reuse_the_row_of_the_last_write(fake, clock):
    with _writes() as w:
        first = await ap.beat("u1")
        clock[0] += 5
        second = await ap.beat("u1")
        clock[0] += 5
        third = await ap.beat("u1")
    assert w.await_count == 1, "one UPDATE for three beats in 10 seconds"
    assert first == second == third == _OP
    assert ap.AGENT_PRESENCE_HEALTH["db_skipped"] == 2


@pytest.mark.asyncio
async def test_the_database_hears_the_beat_again_after_the_window(fake, clock):
    with _writes() as w:
        await ap.beat("u1")
        clock[0] += 15
        await ap.beat("u1")
    assert w.await_count == 2


@pytest.mark.asyncio
async def test_opening_another_conversation_is_written_at_once(fake, clock):
    """The sweep's 120 s courtesy reads viewing_conversation_id: it never waits for the window."""
    with _writes() as w:
        await ap.beat("u1", "conv-a")
        clock[0] += 5
        await ap.beat("u1", "conv-b")
        clock[0] += 5
        await ap.beat("u1", None)
    assert w.await_count == 3
    assert [c.kwargs["viewing_conversation_id"] for c in w.await_args_list] == ["conv-a", "conv-b", None]


@pytest.mark.asyncio
async def test_zero_writes_every_beat_as_before(fake, clock, monkeypatch):
    monkeypatch.setenv("HEARTBEAT_DB_EVERY_S", "0")
    with _writes() as w:
        for _ in range(3):
            await ap.beat("u1")
            clock[0] += 5
    assert w.await_count == 3


@pytest.mark.asyncio
async def test_a_changed_user_row_is_read_on_the_next_beat(fake, clock):
    with _writes() as w:
        await ap.beat("u1")
        ap.forget("u1")
        clock[0] += 5
        await ap.beat("u1")
    assert w.await_count == 2


@pytest.mark.asyncio
async def test_update_user_forgets_the_row():
    from app.db import supabase as db

    ap._LAST_WRITE["u1"] = (_OP, time.monotonic(), None)

    class _Res:
        data = [dict(_OP, is_ready=False)]

    async def _sync(fn, *a, **k):
        return _Res()

    with patch.object(db, "get_client"), patch.object(db, "_run_sync", _sync):
        await db.update_user("u1", {"is_ready": False})
    assert "u1" not in ap._LAST_WRITE


@pytest.mark.asyncio
async def test_operators_who_may_take_chats_are_present_in_redis(fake, clock):
    with _writes():
        await ap.beat("u1")
    assert await fake.zscore(ap.PRESENCE_KEY, "u1") is not None
    assert await ap.present_operator_ids() == {"u1"}


@pytest.mark.parametrize("change", [{"role": "admin"}, {"is_active": False}, {"chat_enabled": False}])
@pytest.mark.asyncio
async def test_anyone_else_is_taken_out(fake, clock, change):
    """Rule 5: the beats that do NOT make someone present."""
    await fake.zadd(ap.PRESENCE_KEY, {"u1": int(time.time())})
    with _writes(dict(_OP, **change)):
        await ap.beat("u1")
    assert await fake.zscore(ap.PRESENCE_KEY, "u1") is None
    assert await ap.present_operator_ids() == set()


@pytest.mark.asyncio
async def test_a_beat_older_than_the_window_is_not_present(fake):
    await fake.zadd(ap.PRESENCE_KEY, {"u1": int(time.time()) - 91, "u2": int(time.time()) - 30})
    assert await ap.present_operator_ids() == {"u2"}
    assert await ap.present_operator_ids(window_s=120) == {"u1", "u2"}


@pytest.mark.asyncio
async def test_redis_down_never_breaks_the_beat(fake, clock):
    fake.down = True
    with _writes() as w:
        row = await ap.beat("u1")
    assert row == _OP and w.await_count == 1
    assert ap.AGENT_PRESENCE_HEALTH["redis_failed"] == 1
    assert await ap.present_operator_ids() is None, "cannot say: the caller asks the database"


@pytest.mark.asyncio
async def test_no_redis_configured_answers_none():
    assert await ap.present_operator_ids() is None


@pytest.mark.asyncio
async def test_a_failed_write_is_tried_again_and_redis_still_hears_the_panel(fake, clock):
    with _writes() as w:
        await ap.beat("u1")
    with patch("app.db.supabase.update_user_last_seen", new=AsyncMock(return_value=None)) as w2:
        clock[0] += 15
        assert await ap.beat("u1") is None, "the heartbeat reads the row itself"
        clock[0] += 5
        assert await ap.beat("u1") is None
    assert w2.await_count == 2, "the beat after a failure writes again, inside the window too"
    assert await fake.zscore(ap.PRESENCE_KEY, "u1") is not None


@pytest.mark.asyncio
async def test_the_heartbeat_endpoint_writes_through_the_presence_beat(fake, clock):
    from app.api import agent as agent_api

    with _writes() as w, \
         patch.object(agent_api.db, "get_queue_for_operator", new=AsyncMock(return_value=[])), \
         patch.object(agent_api.db, "get_user_by_id", new=AsyncMock()) as read, \
         patch("app.services.presence.record_presence_tick", new=AsyncMock()):
        for _ in range(3):
            out = await agent_api.heartbeat(agent_api.HeartbeatBody(), user={"id": "u1"})
            clock[0] += 5
    assert out["success"] and out["role"] == "sales" and out["is_ready"] is True
    assert w.await_count == 1 and read.await_count == 0
    assert await ap.present_operator_ids() == {"u1"}



# ── more than one process (CodeRabbit #273) ─────────────────────


@pytest.mark.asyncio
async def test_a_row_changed_in_another_process_is_read_again_on_the_next_beat(fake, clock):
    with _writes() as w:
        await ap.beat("u1")
        await fake.hset(ap.SHARED_KEY + "u1", "v", "renewed-by-another-process")
        clock[0] += 5
        await ap.beat("u1")
    assert w.await_count == 2, "another process's forget() renewed v: this one's cached row is stale"


@pytest.mark.asyncio
async def test_another_viewing_id_written_elsewhere_is_written_again(fake, clock):
    with _writes() as w:
        await ap.beat("u1", "conv-c")
        await fake.hset(ap.SHARED_KEY + "u1", "view", "conv-d")   # another process wrote D since
        clock[0] += 5
        await ap.beat("u1", "conv-c")
    assert w.await_count == 2, "Supabase holds D: C is written again, not taken as written"
    assert (await fake.hget(ap.SHARED_KEY + "u1", "view")) == "conv-c"


@pytest.mark.asyncio
async def test_forget_renews_the_version_every_process_checks(fake, clock):
    import asyncio

    with _writes() as w:
        await ap.beat("u1")
        before = await fake.hget(ap.SHARED_KEY + "u1", "v")
        ap.forget("u1")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert (await fake.hget(ap.SHARED_KEY + "u1", "v")) != before
        clock[0] += 5
        await ap.beat("u1")
    assert w.await_count == 2


@pytest.mark.asyncio
async def test_a_row_forgotten_while_its_write_was_out_is_not_cached(fake, clock):
    async def write_then_forget(user_id, viewing_conversation_id=None):
        ap.forget(user_id)   # a role change lands while this UPDATE is out
        return dict(_OP)

    with patch("app.db.supabase.update_user_last_seen", new=AsyncMock(side_effect=write_then_forget)) as w:
        await ap.beat("u1")
        clock[0] += 5
        await ap.beat("u1")
    assert w.await_count == 2, "the row from before the change is not reused"


@pytest.mark.asyncio
async def test_without_redis_to_ask_every_beat_writes(fake, clock):
    fake.down = True
    with _writes() as w:
        await ap.beat("u1")
        clock[0] += 5
        await ap.beat("u1")
    assert w.await_count == 2, "what other processes did can't be known: as before this change"


@pytest.mark.asyncio
async def test_a_row_forgotten_while_redis_was_read_is_not_reused(fake, clock):
    """forget() (a role changed) lands while the beat awaits Redis, which still answers the version from before
    it: the row captured before the await is not reused (CodeRabbit #273)."""
    with _writes() as w:
        await ap.beat("u1")
        before = await ap._shared("u1")
        clock[0] += 5

        async def read_then_forget(user_id):
            ap.forget(user_id)
            return before

        with patch.object(ap, "_shared", new=AsyncMock(side_effect=read_then_forget)):
            await ap.beat("u1")
    assert w.await_count == 2, "the row from before the change is read again"
