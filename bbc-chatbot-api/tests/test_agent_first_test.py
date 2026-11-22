"""The owner's agent-first test (22 Nov 2026, app.services.agent_first_test): half the visitors are the agent's,
listed in "On the site now", and Alex's card waits two minutes for an agent who may say hello; the other half is
Alex's, as before, and never listed. Each side named on every answer, on the chat, in /health."""

from __future__ import annotations

import os
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://ci.invalid.supabase.co")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import flags  # noqa: E402
from app.experiments import in_arm  # noqa: E402
from app.infra import redis_client  # noqa: E402
from app.services import agent_first_test as aft  # noqa: E402
from app.services import live_visitors  # noqa: E402
from tests.fake_redis import FakeRedis  # noqa: E402

AGENT_ID = "aaaaaaaa-0000-4000-8000-000000000001"
SALES = {"id": AGENT_ID, "role": "sales", "tunnel_scope": "sales", "email": "ana@example.com", "name": "Ana Agent"}
T0 = 1_800_000_000.0


def _first(side: str, percent: int = 50) -> str:
    """A visitor id that lands on the side asked for (the same sha256 buckets the service reads)."""
    for i in range(1000):
        vid = f"visitor-{i:04d}-3f1c2a7e"
        if in_arm(aft.EXPERIMENT, vid, percent) == (side == aft.AGENT):
            return vid
    raise AssertionError("no id on that side")


AGENT_VID = _first(aft.AGENT)
ALEX_VID = _first(aft.ALEX)


class _Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def time(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    flags._reset_for_tests()
    for k in ("AGENT_FIRST_TEST_PERCENT", "AGENT_FIRST_HOLD_S", "LIVE_VISITORS", "AGENT_FIRST_MESSAGE"):
        monkeypatch.delenv(k, raising=False)
    saved = dict(aft.AGENT_FIRST_HEALTH)
    for k in aft.AGENT_FIRST_HEALTH:
        aft.AGENT_FIRST_HEALTH[k] = 0
    yield
    aft.AGENT_FIRST_HEALTH.update(saved)
    redis_client.set_client_for_tests(None)
    flags._reset_for_tests()


@pytest.fixture
def on(monkeypatch):
    """The hello on (both live switches, the token secret, bbc-live's address): the test runs at its default."""
    from config.settings import settings

    monkeypatch.setattr(settings, "live_token_secret", "live-test-secret")
    monkeypatch.setattr(settings, "live_service_url", "https://live.example.test/")
    monkeypatch.setenv("LIVE_VISITORS", "true")
    monkeypatch.setenv("AGENT_FIRST_MESSAGE", "true")


@pytest.fixture
def clock(monkeypatch):
    c = _Clock(T0)
    monkeypatch.setattr(aft, "time", types.SimpleNamespace(time=c.time))
    return c


@pytest.fixture
def redis():
    fake = FakeRedis()
    redis_client.set_client_for_tests(fake)
    with patch("config.settings.settings.redis_url", "redis://fake"):
        yield fake
    redis_client.set_client_for_tests(None)


async def _agent_at_desk(fake: FakeRedis, at: float = T0) -> None:
    await fake.zadd(aft.GREETERS_KEY, {AGENT_ID: at})


# ── the switches ────────────────────────────────────────────────────


def test_the_owners_numbers_are_the_defaults_and_nothing_runs_at_merge():
    """50 % and two minutes (the owner, 22 Nov); the test runs only while the hello runs, off at merge."""
    assert aft.percent() == 50 and aft.hold_seconds() == 120
    assert aft.running() is False and aft.side(AGENT_VID) is None and aft.panel_info() is None


def test_it_runs_with_the_hello_and_zero_ends_it(on, monkeypatch):
    assert aft.running() is True and aft.panel_info() == {"percent": 50, "hold_s": 120}
    monkeypatch.setenv("AGENT_FIRST_TEST_PERCENT", "0")
    assert aft.running() is False and aft.side(AGENT_VID) is None
    monkeypatch.setenv("AGENT_FIRST_TEST_PERCENT", "50")
    monkeypatch.setenv("AGENT_FIRST_MESSAGE", "false")
    assert aft.running() is False, "the list without the hello: no test"


def test_a_bad_value_keeps_the_owners_numbers_and_out_of_range_is_clamped(on, monkeypatch):
    """As every switch (app.flags): a value that isn't a number keeps the default, never raises."""
    monkeypatch.setenv("AGENT_FIRST_TEST_PERCENT", "half")
    monkeypatch.setenv("AGENT_FIRST_HOLD_S", "soon")
    assert aft.percent() == 50 and aft.hold_seconds() == 120
    monkeypatch.setenv("AGENT_FIRST_TEST_PERCENT", "250")
    monkeypatch.setenv("AGENT_FIRST_HOLD_S", "99999")
    assert aft.percent() == 100 and aft.hold_seconds() == aft.MAX_HOLD_S
    monkeypatch.setenv("AGENT_FIRST_TEST_PERCENT", "-5")
    monkeypatch.setenv("AGENT_FIRST_HOLD_S", "-1")
    assert aft.percent() == 0 and aft.hold_seconds() == 0 and aft.running() is False


def test_a_visitor_keeps_their_side_and_the_sides_are_halves(on):
    ids = [f"v-{i:05d}-0a1b2c3d" for i in range(4000)]
    sides = [aft.side(v) for v in ids]
    assert sides == [aft.side(v) for v in ids], "the same side every time"
    share = sides.count(aft.AGENT) / len(sides)
    assert 0.46 < share < 0.54, share
    assert aft.side(AGENT_VID) == aft.AGENT and aft.side(ALEX_VID) == aft.ALEX
    assert aft.side("x") is None and aft.side(None) is None, "an id the live list doesn't take has no side"


# ── the offer: wait, quiet, or the card as usual ─────────────────────


@pytest.mark.asyncio
async def test_alex_waits_two_minutes_for_an_agent_at_the_desk(on, redis, clock):
    await _agent_at_desk(redis)
    first = await aft.offer_hold(AGENT_VID, "kayak")
    assert first.answer == {"show": False, "arm": "agent_first", "trigger": "kayak", "message": None,
                            "quick_replies": [], "reason": "waiting", "test": "agent", "retry_after_s": 120,
                            "alex_at": int(T0) + 120}
    assert redis.kv[aft.HOLD_KEY + AGENT_VID] == str(int(T0)) and aft.AGENT_FIRST_HEALTH["held"] == 1
    clock.t = T0 + 75.5
    again = await aft.offer_hold(AGENT_VID, "reader")
    assert again.answer["retry_after_s"] == 45, "the wait is the visitor's, not the moment's"
    assert again.answer["alex_at"] == int(T0) + 120, "the same time Alex comes"
    clock.t = T0 + 121
    after = await aft.offer_hold(AGENT_VID, "kayak")
    assert after == aft.Hold(None, after_hold=True) and aft.AGENT_FIRST_HEALTH["after_hold"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("key, reason, counter", [
    (aft.SAID_KEY, "greeted", "quiet_greeted"), (aft.DISMISSED_KEY, "declined", "quiet_declined")])
async def test_a_hello_or_a_not_now_keeps_alex_quiet(on, redis, clock, key, reason, counter):
    await _agent_at_desk(redis)
    await redis.set(key + AGENT_VID, "1", ex=900)
    clock.t = T0 + 500  # long after any wait
    out = await aft.offer_hold(AGENT_VID, "kayak")
    assert out.answer["show"] is False and out.answer["reason"] == reason and "retry_after_s" not in out.answer
    assert aft.AGENT_FIRST_HEALTH[counter] == 1


@pytest.mark.asyncio
async def test_nobody_at_the_desk_alex_comes_as_usual(on, redis, clock):
    await _agent_at_desk(redis, at=T0 - 600)  # beat ten minutes ago: gone
    assert await aft.offer_hold(AGENT_VID, "kayak") == aft.Hold(None)
    assert aft.HOLD_KEY + AGENT_VID not in redis.kv and aft.AGENT_FIRST_HEALTH["no_agent"] == 1


@pytest.mark.asyncio
async def test_rule_5_the_note_and_alexs_side_are_never_held(on, redis, clock):
    await _agent_at_desk(redis)
    assert await aft.offer_hold(AGENT_VID, "note") == aft.Hold(None), "the visitor tapped: their choice"
    assert await aft.offer_hold(ALEX_VID, "kayak") == aft.Hold(None)
    assert not [k for k in redis.kv if k.startswith(aft.HOLD_KEY)], "no wait started for either"


@pytest.mark.asyncio
@pytest.mark.parametrize("env, trigger", [
    ({"PROACTIVE_ENABLED": "false"}, "kayak"),   # the card is off
    ({}, "exit"),                                 # that moment is off (PROACTIVE_TRIGGERS' default)
    ({"PROACTIVE_PERCENT": "0"}, "kayak"),        # PROACTIVE_PERCENT's control arm
])
async def test_rule_5_a_moment_with_no_card_waits_for_nothing(on, redis, clock, monkeypatch, env, trigger):
    await _agent_at_desk(redis)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert await aft.offer_hold(AGENT_VID, trigger) == aft.Hold(None)
    assert aft.HOLD_KEY + AGENT_VID not in redis.kv and aft.AGENT_FIRST_HEALTH["held"] == 0


@pytest.mark.asyncio
async def test_a_zero_wait_lists_the_side_and_holds_nobody(on, redis, clock, monkeypatch):
    monkeypatch.setenv("AGENT_FIRST_HOLD_S", "0")
    await _agent_at_desk(redis)
    assert await aft.offer_hold(AGENT_VID, "kayak") == aft.Hold(None)


@pytest.mark.asyncio
async def test_redis_failing_holds_nobody_and_says_so(on, redis, clock):
    await _agent_at_desk(redis)
    redis.down = True
    assert await aft.offer_hold(AGENT_VID, "kayak") == aft.Hold(None)
    assert aft.AGENT_FIRST_HEALTH["redis_failed"] == 1, "rule 7: a miss is told from 'not the case'"
    redis_client.set_client_for_tests(None)
    with patch.object(aft, "get_redis", return_value=None):
        assert await aft.offer_hold(AGENT_VID, "kayak") == aft.Hold(None)
    assert aft.AGENT_FIRST_HEALTH["redis_failed"] == 2


# ── the routes ──────────────────────────────────────────────────────


def _chat_app():
    from app.api import chat as chat_api
    from app.security.rate_limiter import check_rate_limit

    app = FastAPI()
    app.include_router(chat_api.router)
    app.dependency_overrides[check_rate_limit] = lambda: None
    return TestClient(app)


def _offer(vid: str, trigger: str = "kayak"):
    trip = {"origin_code": "JFK", "destination_code": "LHR", "depart_date": "2027-03-14"}
    return _chat_app().post("/chat/proactive/offer", json={"visitor_id": vid, "trigger": trigger, "trip": trip,
                                                           "device": "computer"}).json()


@pytest.fixture
def cards(monkeypatch):
    monkeypatch.setenv("PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("PROACTIVE_PERCENT", "100")
    from app.services import note_bandit
    monkeypatch.setattr(note_bandit, "refresh_soon", MagicMock())


def test_the_offer_route_holds_the_agents_side_and_names_alexs(on, redis, clock, cards):
    import asyncio

    asyncio.run(_agent_at_desk(redis))
    held = _offer(AGENT_VID)
    assert held["show"] is False and held["retry_after_s"] == 120 and held["test"] == "agent"
    card = _offer(ALEX_VID)
    assert card["show"] is True and card["test"] == "alex" and "after_hold" not in card
    clock.t = T0 + 130
    late = _offer(AGENT_VID)
    assert late["show"] is True and late["test"] == "agent" and late["after_hold"] is True


def test_rule_5_with_the_test_off_the_offer_is_as_before(on, redis, clock, cards, monkeypatch):
    monkeypatch.setenv("AGENT_FIRST_TEST_PERCENT", "0")
    out = _offer(AGENT_VID)
    assert out["show"] is True and "test" not in out and "after_hold" not in out
    assert aft.HOLD_KEY + AGENT_VID not in redis.kv


def test_alexs_side_is_never_listed_and_the_agents_gets_its_pass(on, redis):
    redis.register(live_visitors.PASS_LUA, lambda fake, keys, args: 1)
    with patch("app.api.chat.client_ip", return_value="203.0.113.7"):
        alex = _chat_app().post("/chat/live", json={"visitor_id": ALEX_VID})
        agent = _chat_app().post("/chat/live", json={"visitor_id": AGENT_VID})
    assert alex.json() == {"enabled": False, "test": "alex"} and alex.headers["cache-control"] == "no-store"
    out = agent.json()
    assert out["enabled"] is True and out["pass"] and out["test"] == "agent" and out["hold_s"] == 120


def test_rule_5_the_live_answer_has_no_side_while_the_test_is_off(on, redis, monkeypatch):
    monkeypatch.setenv("AGENT_FIRST_TEST_PERCENT", "0")
    redis.register(live_visitors.PASS_LUA, lambda fake, keys, args: 1)
    with patch("app.api.chat.client_ip", return_value="203.0.113.7"):
        out = _chat_app().post("/chat/live", json={"visitor_id": ALEX_VID}).json()
    assert out["enabled"] is True and "test" not in out and "hold_s" not in out


# ── the chat, the panel, the hello, /health ─────────────────────────


def test_the_chat_carries_its_side_and_the_widget_cannot_write_it(on):
    from app.security.metadata import allowed_widget_metadata

    meta: dict = {}
    aft.tag(meta, AGENT_VID)
    assert meta == {"agent_test": "agent"}
    assert "agent_test" not in allowed_widget_metadata({"agent_test": "agent"}), "server-made only"
    for vid in (ALEX_VID, "x"):
        m: dict = {}
        aft.tag(m, vid)
        assert m == ({"agent_test": "alex"} if vid == ALEX_VID else {})


def test_the_chat_routes_tag_their_side():
    """All three doors that make a chat: /chat/init, /chat/start and the first message."""
    import inspect

    from app.api import chat as chat_api

    src = inspect.getsource(chat_api)
    assert src.count("agent_first_test.tag(_meta, payload.visitor_id)") == 1
    assert src.count("agent_first_test.tag(_meta, visitor_id)") == 1
    assert src.count("agent_first_test.tag(_meta, req.visitor_id)") == 1


def test_the_panel_hears_of_the_test_and_an_agent_at_the_desk_is_counted(on, redis, clock):
    import asyncio

    live = live_visitors.heartbeat_live(SALES)
    assert live["can_say_hello"] is True and live["test"] == {"percent": 50, "hold_s": 120}
    asyncio.run(aft.greeter_beat(AGENT_ID))
    assert redis.kv[aft.GREETERS_KEY][AGENT_ID] == float(int(T0))
    sup = live_visitors.heartbeat_live({"id": "s1", "role": "supervisor", "tunnel_scope": "all"})
    assert sup["can_say_hello"] is False and sup["test"]["percent"] == 50, "a supervisor sees the test too"


def test_the_heartbeat_marks_only_who_may_say_hello():
    import inspect

    from app.api import agent

    src = inspect.getsource(agent.heartbeat)
    assert 'if _live and _live.get("can_say_hello"):' in src and "agent_first_test.greeter_beat(user_id)" in src
    assert '"live": _live,' in src


@pytest.mark.asyncio
async def test_a_hello_is_counted_before_alex_or_after_his_wait(on, redis, clock):
    await redis.set(aft.HOLD_KEY + AGENT_VID, str(int(T0)), ex=900)
    clock.t = T0 + 30
    await aft.note_hello(AGENT_VID)
    clock.t = T0 + 300
    await aft.note_hello(AGENT_VID)
    await aft.note_hello(ALEX_VID)
    assert {k: aft.AGENT_FIRST_HEALTH[k] for k in ("hellos_in_hold", "hellos_after_hold", "hellos_no_hold")} == {
        "hellos_in_hold": 1, "hellos_after_hold": 1, "hellos_no_hold": 1}


def test_health_shows_the_test(on):
    import inspect

    from app.api import health

    h = aft.health()
    assert h["running"] is True and h["percent"] == 50 and h["hold_s"] == 120 and "held" in h
    assert '"agent_first_test": agent_first_test.health(),' in inspect.getsource(health)
