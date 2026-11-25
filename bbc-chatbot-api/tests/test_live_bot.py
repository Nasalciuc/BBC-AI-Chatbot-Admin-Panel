"""Skye's own hello in "On the site now" (app.services.live_bot; the owner, 25 Nov 2026): who she greets, the words,
the send in one step, her run, the endpoints and the tap that starts the chat as hers.

The scripts' real behaviour is tested in bbc-live's Go suite against a real Redis (TestTheBotSendScriptHoldsTheRules);
here the fake Redis is taught them line by line, so the keys and arguments the API passes are held to their order.
Every name here is invented.
"""

import json
import os
import pathlib
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.infra import redis_client  # noqa: E402
from app.services import live_bot, live_tokens, live_visitors  # noqa: E402
from app.services.live_visitors import SendRefused  # noqa: E402
from tests.fake_redis import FakeRedis  # noqa: E402
from tests.test_live_visitors import (  # noqa: E402
    AGENT, SALES, SALES_DB, STORED_AT, _init, _release_lua, _send_lua, _take_hello_lua,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
PANEL_VIEW = ROOT / "bbc-admin-app" / "src" / "features" / "live" / "view.ts"
VID = "5b3e4c9a-0d6f-4e3a-9c4b-8f7a6e5d4c32"
OTHER = "6c4f5dab-1e7a-4f4b-ad5c-9a8b7f6e5d43"
KEY = "bot-test-key"


# ── the contract ────────────────────────────────────────────────────


def test_the_scripts_are_the_contracts():
    for name in ("bot_send.lua", "list.lua"):
        mine = (pathlib.Path(live_bot.__file__).with_name("live_lua") / name).read_bytes()
        assert mine == (CONTRACTS / "live" / name).read_bytes(), f"app/services/live_lua/{name}: copy the contract"


def test_the_switch_is_off_at_merge():
    assert live_bot.run_enabled() is False


# ── the fake's scripts ──────────────────────────────────────────────


def _list_lua(fake, keys, args):
    """list.lua, line by line."""
    since, limit = float(args[0]), int(args[1])
    z = fake.kv.get(keys[0], {}) if fake._alive(keys[0]) else {}
    out: list = []
    for vid in sorted((m for m, s in z.items() if s >= since), key=lambda m: (z[m], m))[:limit]:
        if not fake._alive(f"live:v:{vid}") or not fake.kv[f"live:v:{vid}"]:
            continue
        claim = f"live:claim:{vid}"
        out += [vid, [x for kv in fake.kv[f"live:v:{vid}"].items() for x in kv],
                fake.kv[claim] if fake._alive(claim) else "", 1 if fake._alive(f"live:dismissed:{vid}") else 0]
    return out


def _bot_send_lua(fake, keys, args):
    """bot_send.lua, line by line."""
    visit, claim, dismissed, inbox, hello, said, bot = keys
    skye, vid, message, hello_json, ttl, once = args
    if not fake._alive(visit):
        return -1
    if fake.kv[visit].get("has_chat") == "1":
        return -2
    if fake._alive(dismissed):
        return -3
    if fake._alive(claim):
        return -4
    if fake._alive(bot):
        return -6
    if fake._alive(said):
        return -5
    for key, value, life in ((said, skye, ttl), (bot, "1", once), (claim, skye, ttl), (hello, hello_json, ttl)):
        fake.kv[key] = value
        fake._set_expiry(key, ex=int(life))
    fake._list(inbox).append(message)
    fake._set_expiry(inbox, ex=int(ttl))
    fake.published = getattr(fake, "published", []) + [("live:inbox", vid)]
    return 1


@pytest.fixture
def redis(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "live_token_secret", "live-test-secret")
    monkeypatch.setattr(settings, "live_service_url", "https://live.example.test/")
    monkeypatch.setattr(settings, "live_bot_key", KEY)
    monkeypatch.setenv("LIVE_VISITORS", "true")
    monkeypatch.setenv("AGENT_FIRST_MESSAGE", "true")
    monkeypatch.setenv("LIVE_BOT", "true")
    monkeypatch.setenv("AGENT_FIRST_TEST_PERCENT", "0")
    monkeypatch.setattr(live_bot, "_steps", {})
    for health in (live_bot.BOT_HEALTH, live_visitors.LIVE_HEALTH):
        for k in health:
            health[k] = 0
    fake = FakeRedis()
    fake.register(live_bot.LIST_LUA, _list_lua)
    fake.register(live_bot.BOT_SEND_LUA, _bot_send_lua)
    fake.register(live_visitors.SEND_LUA, _send_lua)
    fake.register(live_visitors.TAKE_HELLO_LUA, _take_hello_lua)
    fake.register(live_visitors.RELEASE_LUA, _release_lua)
    redis_client.set_client_for_tests(fake)
    with patch("config.settings.settings.redis_url", "redis://fake"):
        yield fake
    redis_client.set_client_for_tests(None)


def _visit(fake, vid=VID, *, card="shown", card_s=45, **fields):
    """A visitor on the site now, as bbc-live keeps them (live:v:<vid>), Skye's card `card_s` seconds ago."""
    now = int(time.time())
    h = {"first_at": str(now - 300), "last_at": str(now), "page": "/flights/new-york-istanbul", "source": "site",
         "device": "computer", "site": "bbc", "chat_open": "0", "has_chat": "0"}
    if card:
        h.update(alex_card=card, alex_card_at=str(now - card_s), alex_trigger="exit")
    h.update({k: str(v) for k, v in fields.items()})
    fake.kv[f"live:v:{vid}"] = h
    fake._zset("live:visitors")[vid] = float(now)


async def _rows(fake) -> list[dict]:
    return await live_bot._listed(fake)


async def _greet(vid=VID, **kw):
    with patch("app.db.supabase.log_audit", new=AsyncMock()) as audit:
        out = await live_bot.greet(vid, **kw)
    return out, audit


# ── who Skye greets: only who ignored her card (the owner) ──────────


@pytest.mark.asyncio
@pytest.mark.parametrize("card, card_s, fields, why", [
    ("shown", 30, {}, None),
    ("shown", 600, {"activity": "search:JFK-IST:2026-12-20"}, None),
    # Rule 5, the visitors the run passes by: the card not yet unanswered long enough, answered, or "Not now"
    # to it (the owner: only who ignored it), or no card at all.
    ("shown", 29, {}, "card"),
    ("dismissed", 120, {}, "card"),
    ("engaged", 120, {}, "card"),
    (None, 0, {}, "card"),
    ("shown", 120, {"has_chat": "1"}, "in_a_chat"),
    ("shown", 120, {"page_type": "thank_you"}, "page"),
    ("shown", 120, {"page_type": "legal"}, "page"),
    ("shown", 120, {"activity": "search:JFK-IST;quote_opened;quote_step:2;quote_submitted"}, "reached_out"),
    ("shown", 120, {"activity": "contact:phone;search:JFK-IST"}, "reached_out"),
    ("shown", 120, {"activity": "quote_opened;quote_step:2;quote_submitting"}, "filling_form"),
])
async def test_who_skye_greets(redis, card, card_s, fields, why):
    _visit(redis, card=card, card_s=card_s, **fields)
    (v,) = await _rows(redis)
    assert live_bot.why_not(v, time.time(), {}) == why


@pytest.mark.asyncio
async def test_a_visitor_held_or_who_said_not_now_is_left_alone(redis):
    _visit(redis)
    _visit(redis, OTHER)
    redis.kv[f"live:claim:{VID}"] = AGENT
    redis.kv[f"live:dismissed:{OTHER}"] = "1"
    rows = {v["visitor_id"]: v for v in await _rows(redis)}
    assert live_bot.why_not(rows[VID], time.time(), {}) == "taken"
    assert live_bot.why_not(rows[OTHER], time.time(), {}) == "declined"


@pytest.mark.asyncio
async def test_the_quote_form_holds_the_hello_while_it_moves(redis):
    """The panel's rule since 25 Nov: a step in the form seen less than FORM_QUIET_S ago holds the hello; a form left
    as it was longer is "stuck in the quote form", greeted with its words."""
    _visit(redis, activity="search:JFK-IST;quote_opened;quote_step:2")
    (v,) = await _rows(redis)
    now = time.time()
    live_bot.note_steps([v], now)
    assert live_bot.why_not(v, now, live_bot._steps) == "filling_form", "a step just seen"
    assert live_bot.why_not(v, now + live_bot.FORM_QUIET_S - 1, live_bot._steps) == "filling_form"
    assert live_bot.why_not(v, now + live_bot.FORM_QUIET_S, live_bot._steps) is None, "the form left as it was"
    moved = {**v, "activity": v["activity"] + ["quote_step:3"]}
    live_bot.note_steps([moved], now + live_bot.FORM_QUIET_S)
    assert live_bot.why_not(moved, now + live_bot.FORM_QUIET_S + 1, live_bot._steps) == "filling_form", "it moved"
    live_bot.note_steps([], now)
    assert live_bot._steps == {}, "a visitor no longer listed is forgotten"


def _sides():
    from app.experiments import in_arm

    ids = [f"visitor-{i:04d}-side" for i in range(200)]
    agent = next(i for i in ids if in_arm("agent_first", i, 50))
    alex = next(i for i in ids if not in_arm("agent_first", i, 50))
    return agent, alex


@pytest.mark.asyncio
async def test_the_agent_first_tests_alex_side_stays_the_comparison(redis, monkeypatch):
    """The owner's test: Alex's side is never listed, so nobody greets it; Skye neither, should a beat of it land in
    the list. The agent's side is greeted as anyone (rule 5)."""
    monkeypatch.setenv("AGENT_FIRST_TEST_PERCENT", "50")
    agent_side, alex_side = _sides()
    _visit(redis, agent_side)
    _visit(redis, alex_side)
    rows = {v["visitor_id"]: v for v in await _rows(redis)}
    assert live_bot.why_not(rows[alex_side], time.time(), {}) == "test_alex"
    assert live_bot.why_not(rows[agent_side], time.time(), {}) is None


# ── the words: the panel's ready hello, said as Skye ────────────────

NOW_MS = int(datetime(2026, 11, 25, 18, tzinfo=timezone.utc).timestamp() * 1000)
WHO = "Hi, I'm Skye from the travel team."


@pytest.mark.parametrize("v, words, group", [
    ({"activity": ["search:JFK-IST", "quote_opened"]},
     f"{WHO} Need a hand with the quote form? I can take your trip details right here, it only takes a minute.", "form"),
    ({"activity": ["quote_opened", "quote_failed:server"]},
     f"{WHO} Did the quote form give you trouble? I can take your request right here.", "form"),
    ({"device": "computer", "tz": "Australia/Sydney", "trip": {"to": "LHR"}},
     f"{WHO} Planning the long trip to LHR? I can find business class options for your dates and send them right here.",
     "anz"),
    ({"device": "computer", "tz": "Pacific/Auckland"},
     f"{WHO} Planning a long trip? I can find business class options for your dates and send them right here.", "anz"),
    ({"device": "computer", "trip": {"to": "IST", "depart": "2026-12-20"}},
     f"{WHO} Flying to IST on Dec 20? A good moment to book business class. Want me to check options for your dates?",
     "soon"),
    ({"returning": True, "trip": {"to": "IST"}},
     f"{WHO} Welcome back! Want me to pick up your search for IST and check business class options for you?", "back"),
    # No group: the page's words (rule 5: a phone in Australia, a flight 60 days away).
    ({"device": "phone", "tz": "Australia/Sydney", "trip": {"to": "IST", "depart": "2027-01-24"}},
     f"{WHO} Looking at flights to IST? I can check business class fares for your dates.", None),
    ({"activity": ["quote_opened", "quote_submitted"]},
     f"{WHO} Thanks for your request. Anything you'd like me to know while I prepare your options?", None),
    ({}, f"{WHO} Can I help you find a business class fare?", None),
])
def test_the_ready_hello_is_the_panels_said_as_skye(v, words, group):
    from app.ai.output_guard import foreign_contact_kinds, has_price

    row = {"visitor_id": VID, "activity": [], "device": "computer", **v}
    assert live_bot.ready_hello(row, NOW_MS) == (words, group)
    assert "—" not in words and not has_price(words) and not foreign_contact_kinds(words)
    assert len(words) <= live_visitors.MAX_TEXT


def test_the_words_are_still_the_panels():
    """The hello an agent has ready in the panel (view.ts) and Skye's are the same words: a change there shows here."""
    view = PANEL_VIEW.read_text(encoding="utf-8")
    for piece in (
        "from the travel team.",
        "Need a hand with the quote form? I can take your trip details right here, it only takes a minute.",
        "Need a hand with the quote form? I can take your trip details right here.",
        "Did the quote form give you trouble? I can take your request right here.",
        "Planning the long trip to ${to}? I can find business class options for your dates and send them right here.",
        "Planning a long trip? I can find business class options for your dates and send them right here.",
        "? A good moment to book business class. Want me to check options for your dates?",
        "Welcome back! Want me to pick up your search",
        "and check business class options for you?",
        "Thanks for your request. Anything you'd like me to know while I prepare your options?",
        "Looking at flights to ${t.to}? I can check business class fares for your dates.",
        "Can I help you find a business class fare?",
        "export const FORM_QUIET_S = 120",
        "export const ALEX_GAVE_UP_S = 30",
    ):
        assert piece in view, piece


# ── the send ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skyes_hello_reaches_the_inbox_as_hers(redis):
    _visit(redis, returning="1", to="IST", depart="2027-03-01")
    (v,) = await _rows(redis)
    out, audit = await _greet(visitor=v)
    (msg_raw,) = redis.kv[f"live:inbox:{VID}"]
    msg = json.loads(msg_raw)
    example = json.loads((CONTRACTS / "live_inbox_message_bot.json").read_text())
    assert set(msg) == set(example), "the widget reads the contract's fields"
    assert msg["agent"] == "Skye" and msg["bot"] is True and msg["kind"] == "hello"
    assert msg["text"] == out["text"] == f"{WHO} Welcome back! Want me to pick up your search for IST and check " \
        "business class options for you?"
    tap, _ = live_tokens.read_tap(msg["tap"])
    assert tap["v"] == VID and tap["a"] == "skye" and tap["h"] == out["hello_id"] == msg["id"]
    hello = json.loads(redis.kv[f"live:hello:{out['hello_id']}"])
    assert hello == {"id": out["hello_id"], "visitor_id": VID, "agent_id": "skye", "text": msg["text"],
                     "sent_at": msg["sent_at"], "bot": True, "group": "back"}
    assert redis.kv[f"live:claim:{VID}"] == "skye", "the panel says Skye holds them"
    assert redis.published == [("live:inbox", VID)]
    assert audit.await_args.args[:5] == (None, None, "live_hello_sent", "live_visitors", VID)
    assert audit.await_args.args[5]["bot"] is True and audit.await_args.args[5]["via"] == "run"
    assert live_bot.BOT_HEALTH["sent"] == 1


def test_the_bot_contract_differs_from_an_agents_by_bot_only():
    agent = json.loads((CONTRACTS / "live_inbox_message.json").read_text())
    bot = json.loads((CONTRACTS / "live_inbox_message_bot.json").read_text())
    assert set(bot) - set(agent) == {"bot"} and set(agent) <= set(bot)
    assert bot["bot"] is True and bot["agent"] == live_bot.BOT_NAME
    assert live_tokens.decode(bot["tap"], "t1", b"x", now=0)[1] == "invalid", "a sample, never a working tap"


@pytest.mark.asyncio
@pytest.mark.parametrize("setup, code, status", [
    (lambda r: r.kv.pop(f"live:v:{VID}"), "gone", 410),
    (lambda r: r.kv[f"live:v:{VID}"].update(has_chat="1"), "in_a_chat", 409),
    (lambda r: r.kv.__setitem__(f"live:dismissed:{VID}", "1"), "declined", 409),
    (lambda r: r.kv.__setitem__(f"live:claim:{VID}", AGENT), "taken", 409),
    (lambda r: r.kv.__setitem__(f"live:said:{VID}", AGENT), "already", 409),
    (lambda r: r.kv.__setitem__(f"live:bot:{VID}", "1"), "skye_already", 409),
])
async def test_the_script_settles_a_race_with_its_reason(redis, setup, code, status):
    """The run read the visitor due; between the list and the send the script checks again, and an agent who claimed
    them first keeps them."""
    _visit(redis)
    (v,) = await _rows(redis)
    setup(redis)
    with pytest.raises(SendRefused) as e:
        await _greet(visitor=v)
    assert (e.value.code, e.value.status) == (code, status)
    assert f"live:inbox:{VID}" not in redis.kv
    assert live_bot.BOT_HEALTH["refused"] == 1 and live_bot.BOT_HEALTH["sent"] == 0


@pytest.mark.asyncio
async def test_one_of_skyes_hellos_a_visitor_a_day(redis):
    _visit(redis)
    await _greet()
    redis.kv.pop(f"live:said:{VID}")  # the hello ran out (15 min)
    redis.kv.pop(f"live:claim:{VID}")
    with pytest.raises(SendRefused) as e:
        await _greet()
    assert e.value.code == "skye_already"
    assert redis.expires[f"live:bot:{VID}"] - time.monotonic() == pytest.approx(live_bot.ONCE_S, abs=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("text, code", [
    ("Call me on +1 214 208 5185", "contact"), ("see https://scam.example.test/deal", "contact"),
    ("Fares to IST from $1,200 this week", "price"), ("", "length"), ("x" * 301, "length"),
])
async def test_words_named_by_the_caller_are_held_to_the_clients_rules(redis, text, code):
    _visit(redis)
    with pytest.raises(SendRefused) as e:
        await _greet(text=text)
    assert e.value.code == code and e.value.status == 422
    assert "eval" not in redis.calls


@pytest.mark.asyncio
async def test_words_named_by_the_caller_go_without_a_long_dash(redis):
    _visit(redis)
    out, _ = await _greet(text="Hi — I'm Skye. Want me to check your dates?", group="back")
    assert out["text"] == "Hi, I'm Skye. Want me to check your dates?" and out["group"] == "back"
    out2_group = json.loads(redis.kv[f"live:hello:{out['hello_id']}"])["group"]
    assert out2_group == "back"


@pytest.mark.asyncio
async def test_a_visitor_named_by_the_caller_is_held_to_the_same_rules(redis):
    """POST /api/live/bot/hello names the visitor: the run's rules hold for them too (rule 5: one due is greeted)."""
    _visit(redis, card_s=10)
    _visit(redis, OTHER)
    with pytest.raises(SendRefused) as e:
        await _greet()
    assert (e.value.code, e.value.status) == ("card", 409) and "eval" not in redis.calls
    out, audit = await _greet(OTHER, text="Hi, I'm Skye. Anything I can check for you?")
    assert out["hello_id"] and audit.await_args.args[5]["via"] == "run"
    with pytest.raises(SendRefused) as e:
        await _greet("not-on-the-site-at-all")
    assert (e.value.code, e.value.status) == ("gone", 410)


@pytest.mark.asyncio
async def test_nothing_is_sent_while_off(redis, monkeypatch):
    monkeypatch.setenv("AGENT_FIRST_MESSAGE", "false")
    _visit(redis)
    with pytest.raises(SendRefused) as e:
        await _greet()
    assert e.value.code == "off" and "eval" not in redis.calls


# ── the run ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_run_greets_who_is_due_and_no_one_else(redis):
    _visit(redis)
    _visit(redis, OTHER, card_s=5)
    with patch("app.db.supabase.log_audit", new=AsyncMock()):
        first = await live_bot.run_live_bot()
        second = await live_bot.run_live_bot()
    assert first == {"state": "ok", "listed": 2, "due": 1, "sent": 1, "refused": 0, "failed": 0}
    assert len(redis.kv[f"live:inbox:{VID}"]) == 1 and f"live:inbox:{OTHER}" not in redis.kv
    assert second["sent"] == 0 and second["due"] == 0, "held by Skye now: never twice"
    assert live_bot.BOT_HEALTH["runs"] == 2 and live_bot.BOT_HEALTH["sent"] == 1


@pytest.mark.asyncio
async def test_the_run_is_off_without_its_switch(redis, monkeypatch):
    """Rule 5: LIVE_BOT off, the run reads nothing and greets nobody; the hello itself still works for agents."""
    monkeypatch.setenv("LIVE_BOT", "false")
    _visit(redis)
    assert await live_bot.run_live_bot() == {"state": "off"}
    assert "eval" not in redis.calls and live_bot.BOT_HEALTH["runs"] == 0


@pytest.mark.asyncio
async def test_a_list_that_cannot_be_read_is_counted(redis):
    redis.down = True
    assert await live_bot.run_live_bot() == {"state": "list_failed"}
    assert live_bot.BOT_HEALTH["run_failed"] == 1


def test_the_scheduler_runs_it_every_few_seconds_and_quiet_when_idle():
    from app.services import scheduler

    rule = scheduler._QUIET_WHEN["live_bot"]
    assert rule({"state": "off"}) and rule({"state": "ok", "sent": 0, "failed": 0})
    assert not rule({"state": "ok", "sent": 1, "failed": 0}) and not rule({"state": "ok", "sent": 0, "failed": 1})
    assert not rule({"state": "list_failed"}) and not rule({"state": "no_redis"})
    source = pathlib.Path(scheduler.__file__).read_text(encoding="utf-8")
    assert '("live_bot", run_live_bot, 5)' in source


# ── the tap: the chat starts as Skye's ──────────────────────────────


@pytest.mark.asyncio
async def test_the_tapped_chat_is_skyes_with_the_hello_first(redis):
    from app.api.chat import ChatInitRequest

    _visit(redis, returning="1")
    out, _ = await _greet()
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap))
    assert res.live_agent == "Skye" and res.live_hello == out["text"] and res.live_at == STORED_AT
    m["claim"].assert_not_awaited()  # nobody to claim it for: AI mode
    m["user"].assert_not_awaited()
    conv_id, role, content = m["add"].await_args.args[:3]
    assert (conv_id, role, content) == ("c1", "ai", out["text"])
    assert m["add"].await_args.args[3] == live_bot.MODEL_USED
    patched = m["meta"].await_args.args[1]
    assert set(patched) == {"live_hello"}, "no agent engaged, no clock"
    assert patched["live_hello"]["bot"] is True and patched["live_hello"]["group"] == "back"
    assert f"live:claim:{VID}" not in redis.kv, "the tap lets Skye's hold go"
    assert live_bot.BOT_HEALTH["chats"] == 1


@pytest.mark.asyncio
async def test_an_agents_tap_still_starts_the_agents_chat(redis):
    """Rule 5: the branch takes Skye's hellos only."""
    from app.api.chat import ChatInitRequest

    redis.kv[f"live:claim:{VID}"] = AGENT
    with patch("app.db.supabase.log_audit", new=AsyncMock()):
        await live_visitors.send_hello(SALES, SALES_DB, VID, "Hi, I'm Ana. Can I help with your trip?")
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap))
    assert res.live_agent == "Ana"
    m["claim"].assert_awaited_once_with("c1", AGENT)
    assert m["add"].await_args.args[1] == "agent" and live_bot.BOT_HEALTH["taps"] == 0


@pytest.mark.asyncio
async def test_a_hello_that_cannot_be_stored_starts_an_ordinary_chat(redis):
    from app.api.chat import ChatInitRequest

    _visit(redis)
    await _greet()
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap), stored=False)
    assert res.live_agent is None and res.live_at is None
    m["meta"].assert_not_awaited()
    assert live_bot.BOT_HEALTH["start_failed"] == 1 and live_bot.BOT_HEALTH["chats"] == 0


# ── the endpoints ───────────────────────────────────────────────────


def _client():
    from app.api import live_bot as live_bot_api

    app = FastAPI()
    app.include_router(live_bot_api.router, prefix="/api")
    return TestClient(app)


def test_the_endpoints_take_the_key_only(redis, monkeypatch):
    from config.settings import settings

    c = _client()
    assert c.get("/api/live/bot/candidates").status_code == 401
    wrong = c.post("/api/live/bot/hello", json={"visitor_id": VID}, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401 and wrong.json()["code"] == "key"
    assert live_bot.BOT_HEALTH["key_refused"] == 2
    monkeypatch.setattr(settings, "live_bot_key", "")
    off = c.get("/api/live/bot/candidates", headers={"Authorization": "Bearer "})
    assert off.status_code == 503 and off.json()["code"] == "not_configured"
    assert "eval" not in redis.calls


def test_the_candidates_are_who_skye_would_greet_with_her_words(redis):
    _visit(redis, to="IST")
    _visit(redis, OTHER, card="dismissed")
    res = _client().get("/api/live/bot/candidates", headers={"Authorization": f"Bearer {KEY}"})
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True and body["count"] == 1
    (row,) = body["data"]
    assert row["visitor_id"] == VID and row["group"] is None
    assert row["text"] == f"{WHO} Looking at flights to IST? I can check business class fares for your dates."
    assert f"live:inbox:{VID}" not in redis.kv, "reading the candidates greets nobody"


def test_the_hello_endpoint_greets_one_visitor_or_says_why_not(redis):
    _visit(redis)
    _visit(redis, OTHER, card_s=3)
    c = _client()
    auth = {"Authorization": f"Bearer {KEY}"}
    with patch("app.db.supabase.log_audit", new=AsyncMock()) as audit:
        ok = c.post("/api/live/bot/hello", json={"visitor_id": VID}, headers=auth)
        no = c.post("/api/live/bot/hello", json={"visitor_id": OTHER}, headers=auth)
    assert ok.status_code == 200 and ok.json()["success"] is True and ok.json()["count"] == 1
    assert ok.json()["data"]["text"] == f"{WHO} Can I help you find a business class fare?"
    assert audit.await_args.args[5]["via"] == "endpoint"
    assert no.status_code == 409 and no.json() == {"success": False, "data": None, "count": 0, "code": "card",
                                                   "error": live_bot._WHY_NOT["card"]}
