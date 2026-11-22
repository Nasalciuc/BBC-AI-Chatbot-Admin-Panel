"""The conversation lock: in this process always, across processes through Redis when it answers."""

import asyncio
import shutil
import socket
import subprocess
import time
from unittest.mock import patch

import pytest

from app.infra import redis_client
from app.services import conversation_lock as cl
from tests.fake_redis import FakeRedis, compare_and_delete


@pytest.fixture
def fake(monkeypatch):
    from config.settings import settings

    r = FakeRedis()
    r.register(cl.RELEASE_LUA, compare_and_delete)
    monkeypatch.setattr(settings, "redis_url", "redis://fake")
    redis_client.set_client_for_tests(r)
    for k in cl.LOCK_HEALTH:
        cl.LOCK_HEALTH[k] = 0
    yield r


def _held_elsewhere(fake: FakeRedis, cid: str, ms: int, name: str = "lead") -> str:
    """Another process holds the key: its own token, its own expiry."""
    key = f"lock:{name}:{cid}"
    fake.kv[key] = "their-token"
    fake.expires[key] = time.monotonic() + ms / 1000.0
    return key


@pytest.mark.asyncio
async def test_the_key_is_set_while_the_work_runs_and_gone_after(fake):
    async with cl.conversation_lock("lead", "c1"):
        token = await fake.get("lock:lead:c1")
        assert token and token != "their-token" and len(token) == 32
    assert await fake.get("lock:lead:c1") is None
    assert cl.LOCK_HEALTH["redis_held"] == 1


@pytest.mark.asyncio
async def test_a_second_process_waits_until_the_first_lets_go(fake):
    _held_elsewhere(fake, "c1", 300)
    started = time.monotonic()
    async with cl.conversation_lock("lead", "c1"):
        waited = time.monotonic() - started
    assert waited >= 0.25, "it went in while the other process still held the key"
    assert cl.LOCK_HEALTH["redis_held"] == 1 and cl.LOCK_HEALTH["max_wait_ms"] >= 250


@pytest.mark.asyncio
async def test_another_conversation_never_waits(fake):
    """Rule 5: the message that does NOT take this branch. c1 is held elsewhere; c2 goes straight in."""
    _held_elsewhere(fake, "c1", 5_000)
    started = time.monotonic()
    async with cl.conversation_lock("lead", "c2"):
        pass
    assert time.monotonic() - started < 0.2
    assert cl.LOCK_HEALTH["wait_timeouts"] == 0


@pytest.mark.asyncio
async def test_a_token_that_is_not_ours_is_never_released(fake):
    async with cl.conversation_lock("lead", "c1"):
        # Our lock expired mid-work and another process took the key.
        fake.kv["lock:lead:c1"] = "their-token"
    assert await fake.get("lock:lead:c1") == "their-token"
    assert cl.LOCK_HEALTH["expired_before_release"] == 1


@pytest.mark.asyncio
async def test_a_holder_that_died_is_waited_out_by_its_ttl(fake):
    """Nobody releases it: the expiry does."""
    _held_elsewhere(fake, "c1", 150)
    async with cl.conversation_lock("lead", "c1"):
        assert await fake.get("lock:lead:c1") not in (None, "their-token")


@pytest.mark.asyncio
async def test_held_past_the_wait_the_work_still_goes_on(fake, monkeypatch):
    """A write is never dropped for want of a lock: counted, logged, done under the in-process lock."""
    monkeypatch.setattr(cl, "WAIT_S", 0.15)
    _held_elsewhere(fake, "c1", 60_000)
    ran = []
    async with cl.conversation_lock("lead", "c1"):
        ran.append(1)
    assert ran == [1]
    assert cl.LOCK_HEALTH["wait_timeouts"] == 1
    assert await fake.get("lock:lead:c1") == "their-token", "someone else's key is left alone"


@pytest.mark.asyncio
async def test_redis_down_means_the_in_process_lock_alone_and_a_cooldown(fake):
    fake.down = True
    async with cl.conversation_lock("lead", "c1"):
        pass
    assert cl.LOCK_HEALTH["redis_errors"] == 1
    assert redis_client.REDIS_HEALTH["errors"] >= 1
    calls = len(fake.calls)
    async with cl.conversation_lock("lead", "c1"):
        pass
    assert len(fake.calls) == calls, "Redis is skipped while it cools down"
    assert cl.LOCK_HEALTH["local_only"] == 1


@pytest.mark.asyncio
async def test_no_redis_configured_is_todays_behaviour(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "redis_url", "")
    before = cl.LOCK_HEALTH["local_only"]
    async with cl.conversation_lock("lead", "c1"):
        pass
    assert cl.LOCK_HEALTH["local_only"] == before + 1


@pytest.mark.asyncio
async def test_the_switch_turns_the_redis_half_off(fake, monkeypatch):
    monkeypatch.setenv("LOCK_REDIS", "false")
    async with cl.conversation_lock("lead", "c1"):
        assert await fake.get("lock:lead:c1") is None
    assert cl.LOCK_HEALTH["local_only"] == 1


@pytest.mark.asyncio
async def test_one_process_still_takes_turns(fake):
    order = []

    async def writer(tag):
        async with cl.conversation_lock("lead", "c1"):
            order.append(f"{tag}-in")
            await asyncio.sleep(0.02)
            order.append(f"{tag}-out")

    await asyncio.gather(writer("a"), writer("b"))
    assert order in (["a-in", "a-out", "b-in", "b-out"], ["b-in", "b-out", "a-in", "a-out"])


@pytest.mark.asyncio
async def test_a_sync_release_lets_the_key_go_a_moment_later(fake):
    """The asyncio.Lock calls the lead writer uses: acquire, then a plain release()."""
    lock = cl.conversation_lock("lead", "c1")
    await lock.acquire()
    assert lock.locked() and await fake.get("lock:lead:c1")
    lock.release()
    assert not lock.locked()
    for _ in range(20):
        if await fake.get("lock:lead:c1") is None:
            break
        await asyncio.sleep(0.01)
    assert await fake.get("lock:lead:c1") is None


@pytest.mark.asyncio
async def test_cancelled_while_waiting_leaves_nothing_held(fake):
    _held_elsewhere(fake, "c1", 60_000)
    lock = cl.conversation_lock("lead", "c1")
    task = asyncio.create_task(lock.acquire())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked()


def test_the_lead_writers_use_this_lock():
    from app.api import chat
    from app.services import lead_service

    assert isinstance(lead_service._lead_lock("c1"), cl.ConversationLock)
    assert lead_service._LEAD_LOCKS is cl.local_locks("lead")
    assert isinstance(chat._post_sale_lock("c1"), cl.ConversationLock)
    assert chat._POST_SALE_LOCKS is cl.local_locks("post_sale")


# ── against a real Redis, when this machine has one (CI does not) ──────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def real_redis(monkeypatch):
    if not shutil.which("redis-server"):
        pytest.skip("no redis-server on this machine")
    from config.settings import settings

    port = _free_port()
    proc = subprocess.Popen(
        ["redis-server", "--port", str(port), "--save", "", "--appendonly", "no", "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    monkeypatch.setattr(settings, "redis_url", f"redis://127.0.0.1:{port}/0")
    redis_client.set_client_for_tests(None)
    try:
        yield port
    finally:
        redis_client.set_client_for_tests(None)
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_the_release_script_on_a_real_redis(real_redis):
    import redis.asyncio as aioredis

    other = aioredis.from_url(f"redis://127.0.0.1:{real_redis}/0", decode_responses=True)
    try:
        async with cl.conversation_lock("lead", "c-real"):
            mine = await other.get("lock:lead:c-real")
            assert mine and len(mine) == 32
            assert await other.pttl("lock:lead:c-real") > 0
        assert await other.get("lock:lead:c-real") is None
        # Someone else's key: the script leaves it.
        await other.set("lock:lead:c-real", "theirs", px=5_000)
        assert await other.eval(cl.RELEASE_LUA, 1, "lock:lead:c-real", "mine") == 0
        assert await other.get("lock:lead:c-real") == "theirs"
    finally:
        await other.aclose()


# ── what turns Redis off for everyone (CodeRabbit #273) ─────────


def test_a_refused_command_does_not_turn_redis_off_for_the_locks():
    from redis.exceptions import ResponseError

    from app.infra import redis_client

    fake = object()
    redis_client.set_client_for_tests(fake)
    try:
        with patch("config.settings.settings.redis_url", "redis://fake"):
            before = redis_client.REDIS_HEALTH["command_errors"]
            redis_client.redis_failed("recent", ResponseError("WRONGTYPE Operation against a key"))
            assert redis_client.get_redis() is fake, "one refused command is no outage"
            assert redis_client.REDIS_HEALTH["command_errors"] == before + 1
            redis_client.redis_failed("lock", ConnectionError("connection refused"))
            assert redis_client.get_redis() is None, "an unreachable Redis cools down for everyone"
    finally:
        redis_client.set_client_for_tests(None)


def test_a_redis_url_redis_py_cannot_read_is_no_crash():
    from app.infra import redis_client

    redis_client.set_client_for_tests(None)
    try:
        with patch("config.settings.settings.redis_url", "redis://localhost:6379/0?socket_timeout=soon"), \
                patch("redis.asyncio.from_url", side_effect=ValueError("bad option")):
            assert redis_client.get_redis() is None
            assert "client" in (redis_client.REDIS_HEALTH["last_error"] or "")
    finally:
        redis_client.set_client_for_tests(None)
