"""An agent's own bot in "On the site now" (app.services.live_bot; the owner, 25 Nov 2026): the key the agent takes in
the panel, the visitors the bot sees, its hello in the agent's name without the three-at-a-time limit, the chat its
hello starts as the agent's, and its answers there.

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

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.infra import redis_client  # noqa: E402
from app.services import live_bot, live_tokens, live_visitors  # noqa: E402
from app.services.live_bot import BotRefused  # noqa: E402
from tests.fake_redis import FakeRedis  # noqa: E402
from tests.test_live_visitors import (  # noqa: E402
    AGENT, COLLEAGUE, SALES, SALES_DB, STORED_AT, _init, _release_lua, _send_lua, _take_hello_lua,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
PANEL_VIEW = ROOT / "bbc-admin-app" / "src" / "features" / "live" / "view.ts"
VID = "5b3e4c9a-0d6f-4e3a-9c4b-8f7a6e5d4c32"
OTHER = "6c4f5dab-1e7a-4f4b-ad5c-9a8b7f6e5d43"
# A made-up JWT secret, built at run time (.gitleaksignore: its first version), long enough for HS256.
SECRET = "-".join(["jwt", "test", "only"] * 4)
ROW = {**SALES_DB, "email": "ana@example.com", "token_version": 0, "avatar_url": None}
CONV = "c0000000-0000-4000-8000-0000000000c1"


# ── the contract ────────────────────────────────────────────────────


def test_the_scripts_are_the_contracts():
    for name in ("bot_send.lua", "list.lua"):
        mine = (pathlib.Path(live_bot.__file__).with_name("live_lua") / name).read_bytes()
        assert mine == (CONTRACTS / "live" / name).read_bytes(), f"app/services/live_lua/{name}: copy the contract"


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
    holder, vid, message, hello_json, ttl, once, agent, steps = args
    if not fake._alive(visit):
        return -1
    if fake.kv[visit].get("has_chat") == "1":
        return -2
    if fake._alive(dismissed):
        return -3
    if fake._alive(claim) and fake.kv[claim] != agent:
        return -4
    if fake._alive(bot):
        return -6
    if fake.kv[visit].get("activity", "") != steps:
        return -7
    if fake._alive(said):
        return -5
    for key, value, life in ((said, holder, ttl), (bot, "1", once), (claim, holder, ttl), (hello, hello_json, ttl)):
        fake.kv[key] = value
        fake._set_expiry(key, ex=int(life))
    fake._list(inbox).append(message)
    fake._set_expiry(inbox, ex=int(ttl))
    fake.published = getattr(fake, "published", []) + [("live:inbox", vid)]
    return 1


@pytest.fixture
def redis(monkeypatch):
    from app.security import ban_store
    from config.settings import settings

    monkeypatch.setattr(settings, "live_token_secret", "live-test-secret")
    monkeypatch.setattr(settings, "live_service_url", "https://live.example.test/")
    monkeypatch.setattr(settings, "jwt_secret", SECRET)
    monkeypatch.setenv("LIVE_VISITORS", "true")
    monkeypatch.setenv("AGENT_FIRST_MESSAGE", "true")
    monkeypatch.setenv("AGENT_FIRST_TEST_PERCENT", "0")
    monkeypatch.setattr(live_bot, "_steps", {})
    monkeypatch.setattr(ban_store, "_MEM_REVOKED", {})
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
    with patch("config.settings.settings.redis_url", "redis://fake"), \
            patch("app.db.supabase.get_user_by_id", new=AsyncMock(return_value=dict(ROW))) as user, \
            patch("app.db.supabase.log_audit", new=AsyncMock()) as audit:
        fake.user, fake.audit = user, audit
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


async def _key(user=SALES) -> str:
    return (await live_bot.issue_key(user))["key"]


async def _agent(fake) -> dict:
    return await live_bot.agent_for(f"Bearer {await _key()}")


# ── the key: the agent takes it in the panel, their bot works as them by it ─────────


@pytest.mark.asyncio
async def test_a_key_works_as_its_agent(redis):
    out = await live_bot.issue_key(SALES)
    claims = jwt.decode(out["key"], SECRET, algorithms=["HS256"])
    assert claims["sub"] == AGENT and claims["scope"] == "live_bot"
    # Kept: the key's id, when it was made and the agent's token_version then, never the key itself.
    assert json.loads(redis.kv[f"live:botkey:{AGENT}"]) == {"id": claims["jti"], "iat": claims["iat"], "tv": 0}
    assert out["key"] not in redis.kv[f"live:botkey:{AGENT}"]
    assert claims["exp"] - claims["iat"] == live_bot.KEY_TTL_S
    agent = await live_bot.agent_for(f"Bearer {out['key']}")
    assert agent["id"] == AGENT and agent["first_name"] == "Ana" and agent["role"] == "sales"
    assert redis.audit.await_args.args[2] == "live_bot_key_issued"
    assert live_bot.BOT_HEALTH["keys_issued"] == 1
    assert (await live_bot.key_status(SALES))["active"] is True


@pytest.mark.asyncio
async def test_a_new_key_replaces_the_one_before_and_revoke_ends_it(redis):
    first = await _key()
    second = await _key()
    with pytest.raises(BotRefused) as e:
        await live_bot.agent_for(f"Bearer {first}")
    assert (e.value.status, e.value.code) == (401, "key_revoked")
    assert (await live_bot.agent_for(f"Bearer {second}"))["id"] == AGENT
    assert (await live_bot.revoke_key(SALES)) == {"revoked": True}
    with pytest.raises(BotRefused) as e:
        await live_bot.agent_for(f"Bearer {second}")
    assert e.value.code == "key_revoked"
    assert (await live_bot.key_status(SALES)) == {"active": False, "expires_at": None}
    assert (await live_bot.revoke_key(SALES)) == {"revoked": False}
    assert live_bot.BOT_HEALTH["keys_revoked"] == 1


@pytest.mark.asyncio
async def test_what_is_no_key(redis):
    """Rule 5: no header, junk, another secret's token, a panel session (no scope), a key run out."""
    key = await _key()
    session = jwt.encode({"sub": AGENT, "iat": int(time.time()), "exp": int(time.time()) + 60, "jti": "x"}, SECRET,
                         algorithm="HS256")
    expired = jwt.encode({"sub": AGENT, "scope": "live_bot", "jti": json.loads(redis.kv[f"live:botkey:{AGENT}"])["id"],
                          "iat": int(time.time()) - 100, "exp": int(time.time()) - 1}, SECRET, algorithm="HS256")
    for header, code in ((None, "key"), ("", "key"), (key, "key"), ("Bearer junk", "key"),
                         (f"Bearer {jwt.encode({'sub': AGENT}, 'other-secret-' * 4, algorithm='HS256')}", "key"),
                         (f"Bearer {session}", "key"), (f"Bearer {expired}", "key_expired")):
        with pytest.raises(BotRefused) as e:
            await live_bot.agent_for(header)
        assert (e.value.status, e.value.code) == (401, code), header


def test_the_panel_never_takes_a_bot_key_as_a_session(redis):
    """Any scope is refused by the panel's own check: the bot key reaches the bot's endpoints alone."""
    from app.api import live as live_api

    app = FastAPI()
    app.include_router(live_api.router, prefix="/api")
    bot_key = jwt.encode({"sub": AGENT, "scope": "live_bot", "jti": "k", "token_version": 0,
                          "iat": int(time.time()), "exp": int(time.time()) + 60}, SECRET, algorithm="HS256")
    res = TestClient(app).get("/api/live/bot-key", headers={"Authorization": f"Bearer {bot_key}"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_a_key_ends_with_the_agents_access(redis, monkeypatch):
    """A role change or a deactivation (token_version), "sign out everywhere" after it, an agent who no longer may say
    hello or take chats, an agent that can't be read."""
    from app.security import ban_store

    key = f"Bearer {await _key()}"
    redis.user.return_value = {**ROW, "token_version": 1}
    with pytest.raises(BotRefused) as e:
        await live_bot.agent_for(key)
    assert (e.value.status, e.value.code) == (401, "key_revoked")
    redis.user.return_value = dict(ROW)
    ban_store._MEM_REVOKED[AGENT] = int(time.time()) + 1
    with pytest.raises(BotRefused) as e:
        await live_bot.agent_for(key)
    assert e.value.code == "key_revoked"
    ban_store._MEM_REVOKED.clear()
    redis.kv[f"revoked_before:{AGENT}"] = str(int(time.time()) + 1)
    with pytest.raises(BotRefused) as e:
        await live_bot.agent_for(key)
    assert e.value.code == "key_revoked"
    redis.kv.pop(f"revoked_before:{AGENT}")
    for row in ({**ROW, "chat_enabled": False}, {**ROW, "is_active": False}, {**ROW, "role": "support"},
                {**ROW, "tunnel_scope": "support"}):
        redis.user.return_value = row
        with pytest.raises(BotRefused) as e:
            await live_bot.agent_for(key)
        assert (e.value.status, e.value.code) == (403, "not_for_you"), row
    redis.user.return_value = None
    with pytest.raises(BotRefused) as e:
        await live_bot.agent_for(key)
    assert (e.value.status, e.value.code) == (503, "agent_unreadable")
    assert live_bot.BOT_HEALTH["agent_unreadable"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("row, code, status", [
    ({**ROW, "chat_enabled": False}, "not_for_you", 403),
    ({**ROW, "role": "supervisor", "tunnel_scope": "all"}, "not_for_you", 403),
])
async def test_only_who_may_say_hello_takes_a_key(redis, row, code, status):
    redis.user.return_value = row
    with pytest.raises(BotRefused) as e:
        await live_bot.issue_key(SALES)
    assert (e.value.code, e.value.status) == (code, status)
    assert f"live:botkey:{AGENT}" not in redis.kv


@pytest.mark.asyncio
async def test_the_panel_says_a_key_stopped_when_the_bot_would_be_refused(redis):
    """CodeRabbit #282, round 7: the key's state checks it as agent_for does: a "sign out everywhere" after it was
    made, or a role or access change since (token_version), and it no longer works."""
    from app.security import ban_store

    key = f"Bearer {await _key()}"
    assert (await live_bot.key_status(SALES))["active"] is True
    # Rule 5: a sign-out before the key was made leaves it working, here and for the bot.
    redis.kv[f"revoked_before:{AGENT}"] = str(int(time.time()) - 60)
    assert (await live_bot.key_status(SALES))["active"] is True
    assert (await live_bot.agent_for(key))["id"] == AGENT
    redis.kv[f"revoked_before:{AGENT}"] = str(int(time.time()) + 1)
    assert await live_bot.key_status(SALES) == {"active": False, "expires_at": None}
    with pytest.raises(BotRefused):
        await live_bot.agent_for(key)
    redis.kv.pop(f"revoked_before:{AGENT}")
    ban_store._MEM_REVOKED[AGENT] = int(time.time()) + 1
    assert (await live_bot.key_status(SALES))["active"] is False
    ban_store._MEM_REVOKED.clear()
    redis.user.return_value = {**ROW, "token_version": 1}
    assert (await live_bot.key_status(SALES))["active"] is False
    redis.user.return_value = None
    with pytest.raises(BotRefused) as e:
        await live_bot.key_status(SALES)
    assert (e.value.status, e.value.code) == (503, "agent_unreadable")


@pytest.mark.asyncio
@pytest.mark.parametrize("kept", ["{id}", '{{"id": "{id}"}}', "not json at all"])
async def test_a_key_kept_in_another_shape_is_no_key_anywhere(redis, kept):
    """CodeRabbit #282, round 7, second look: a key kept as its id alone (never released) or in any other shape is no
    key for the bot and none in the panel, so the two never disagree; the agent makes a new one."""
    key = f"Bearer {await _key()}"
    key_id = json.loads(redis.kv[f"live:botkey:{AGENT}"])["id"]
    redis.kv[f"live:botkey:{AGENT}"] = kept.format(id=key_id)
    assert await live_bot.key_status(SALES) == {"active": False, "expires_at": None}
    with pytest.raises(BotRefused) as e:
        await live_bot.agent_for(key)
    assert (e.value.status, e.value.code) == (401, "key_revoked")
    # Rule 5: a new key is kept whole, and works in both.
    key = f"Bearer {await _key()}"
    assert (await live_bot.key_status(SALES))["active"] is True
    assert (await live_bot.agent_for(key))["id"] == AGENT


@pytest.mark.asyncio
async def test_the_agents_row_is_read_at_every_call(redis):
    """CodeRabbit #282, round 7: nothing of the agent is kept between calls: a switch-off reaches the bot's next one."""
    key = f"Bearer {await _key()}"
    assert (await live_bot.agent_for(key))["id"] == AGENT
    redis.user.return_value = {**ROW, "chat_enabled": False}
    with pytest.raises(BotRefused) as e:
        await live_bot.agent_for(key)
    assert (e.value.status, e.value.code) == (403, "not_for_you")


@pytest.mark.asyncio
async def test_no_key_while_off_or_unconfigured(redis, monkeypatch):
    from config.settings import settings

    monkeypatch.setenv("AGENT_FIRST_MESSAGE", "false")
    with pytest.raises(BotRefused) as e:
        await live_bot.issue_key(SALES)
    assert e.value.code == "off"
    monkeypatch.setenv("AGENT_FIRST_MESSAGE", "true")
    monkeypatch.setattr(settings, "jwt_secret", "")
    with pytest.raises(BotRefused) as e:
        await live_bot.issue_key(SALES)
    assert e.value.code == "not_configured"
    with pytest.raises(BotRefused) as e:
        await live_bot.agent_for("Bearer anything")
    assert e.value.code == "not_configured"


# ── the visitors the bot sees: the panel's list for its agent ───────


@pytest.mark.asyncio
async def test_the_bot_sees_the_panels_list_with_the_hello_ready_in_the_agents_name(redis):
    _visit(redis, to="IST")                                     # Skye's card unanswered 45 s: listed, may greet
    _visit(redis, OTHER, card="dismissed")                      # "Not now" to the card: listed, may greet
    _visit(redis, "visitor-card-new", card_s=10)                # the card too new: not listed (rule 5)
    _visit(redis, "visitor-no-card", card=None)                 # no card: not listed (rule 5)
    _visit(redis, "visitor-held-ok", card=None)
    redis.kv["live:claim:visitor-held-ok"] = AGENT              # held by the agent from the panel: listed, may greet
    agent = await _agent(redis)
    rows = {r["visitor_id"]: r for r in await live_bot.visitors(agent)}
    assert set(rows) == {VID, OTHER, "visitor-held-ok"}
    assert rows[VID]["may_greet"] is True and rows[VID]["state"] == "free" and rows[VID]["why_not"] is None
    assert rows[VID]["ready_hello"] == ("Hi, I'm Ana from the travel team. Looking at flights to IST? I can check "
                                        "business class fares for your dates.")
    assert rows["visitor-held-ok"]["state"] == "mine" and rows["visitor-held-ok"]["may_greet"] is True
    assert rows[OTHER]["may_greet"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("setup, why", [
    (lambda r: r.kv.__setitem__(f"live:claim:{VID}", COLLEAGUE), "taken"),
    (lambda r: r.kv.__setitem__(f"live:claim:{VID}", f"bot:{COLLEAGUE}"), "taken"),
    (lambda r: r.kv.__setitem__(f"live:claim:{VID}", f"bot:{AGENT}"), "already"),
    (lambda r: r.kv.__setitem__(f"live:dismissed:{VID}", "1"), "declined"),
    (lambda r: r.kv[f"live:v:{VID}"].update(activity="search:JFK-IST;quote_opened"), "filling_form"),
    # CodeRabbit #287: what the script would refuse, the list says first (an agent's hello whose claim ran out, a
    # bot's hello today whose hold ran out).
    (lambda r: r.kv.__setitem__(f"live:said:{VID}", COLLEAGUE), "already"),
    (lambda r: r.kv.__setitem__(f"live:bot:{VID}", "1"), "bot_already"),
])
async def test_why_the_bot_may_not_greet_a_listed_visitor(redis, setup, why):
    _visit(redis)
    setup(redis)
    agent = await _agent(redis)
    (row,) = await live_bot.visitors(agent)
    assert (row["may_greet"], row["why_not"]) == (False, why)
    with pytest.raises(BotRefused) as e:
        await live_bot.greet(agent, VID)
    assert (e.value.status, e.value.code) == (409, why)
    assert f"live:inbox:{VID}" not in redis.kv


@pytest.mark.asyncio
async def test_the_quote_form_holds_the_hello_until_they_stop_for_two_minutes(redis):
    _visit(redis, activity="search:JFK-IST;quote_opened;quote_step:2")
    agent = await _agent(redis)
    (v,) = await live_bot._listed(redis)
    now = time.time()
    live_bot.note_steps([v], now)
    assert live_bot.why_not(v, AGENT, now, live_bot._steps) == "filling_form"
    assert live_bot.why_not(v, AGENT, now + live_bot.FORM_QUIET_S, live_bot._steps) is None
    moved = {**v, "activity": v["activity"] + ["quote_step:3"]}
    live_bot.note_steps([moved], now + live_bot.FORM_QUIET_S)
    assert live_bot.why_not(moved, AGENT, now + live_bot.FORM_QUIET_S + 1, live_bot._steps) == "filling_form"
    assert agent["id"] == AGENT


@pytest.mark.asyncio
async def test_the_agent_first_tests_alex_side_stays_the_comparison(redis, monkeypatch):
    from app.experiments import in_arm

    monkeypatch.setenv("AGENT_FIRST_TEST_PERCENT", "50")
    ids = [f"visitor-{i:04d}-side" for i in range(200)]
    agent_side = next(i for i in ids if in_arm("agent_first", i, 50))
    alex_side = next(i for i in ids if not in_arm("agent_first", i, 50))
    _visit(redis, agent_side)
    _visit(redis, alex_side)
    agent = await _agent(redis)
    assert [r["visitor_id"] for r in await live_bot.visitors(agent)] == [agent_side]
    with pytest.raises(BotRefused) as e:
        await live_bot.greet(agent, alex_side)
    assert e.value.code == "test_alex"


# ── the hello, in the agent's name, without the three at a time ─────


@pytest.mark.asyncio
async def test_the_bots_hello_is_the_agents(redis):
    # No departure day: one 15 to 45 days off would make them "soon" (CodeRabbit #282, round 7).
    _visit(redis, returning="1", to="IST")
    agent = await _agent(redis)
    out = await live_bot.greet(agent, VID)
    (msg_raw,) = redis.kv[f"live:inbox:{VID}"]
    msg = json.loads(msg_raw)
    example = json.loads((CONTRACTS / "live_inbox_message.json").read_text())
    assert set(msg) == set(example), "the visitor's widget gets an agent's hello, nothing more"
    assert msg["agent"] == "Ana" and msg["text"] == out["text"] == (
        "Hi, I'm Ana from the travel team. Welcome back! Want me to pick up your search for IST and check business "
        "class options for you?")
    tap, _ = live_tokens.read_tap(msg["tap"])
    assert tap["v"] == VID and tap["a"] == AGENT and tap["h"] == out["hello_id"] == msg["id"]
    hello = json.loads(redis.kv[f"live:hello:{out['hello_id']}"])
    assert hello == {"id": out["hello_id"], "visitor_id": VID, "agent_id": AGENT, "text": msg["text"],
                     "sent_at": msg["sent_at"], "bot": True, "group": "back"}
    assert redis.kv[f"live:claim:{VID}"] == f"bot:{AGENT}", "the panel says the agent's bot holds them"
    assert redis.expires[f"live:bot:{VID}"] - time.monotonic() == pytest.approx(live_bot.ONCE_S, abs=5)
    assert redis.audit.await_args.args[:5] == (AGENT, "ana@example.com", "live_hello_sent", "live_visitors", VID)
    assert redis.audit.await_args.args[5]["bot"] is True
    assert live_bot.BOT_HEALTH["sent"] == 1


@pytest.mark.asyncio
async def test_no_limit_of_three_through_the_bot(redis):
    """The owner: the panel holds three visitors at a time (bbc-live MaxClaims); the bot's hellos hold none of them."""
    held = [f"visitor-held-{i}" for i in range(3)]
    for vid in held:
        _visit(redis, vid)
        redis.kv[f"live:claim:{vid}"] = AGENT
    redis.kv[f"live:claims:{AGENT}"] = set(held)
    others = [f"visitor-more-{i}" for i in range(6)]
    for vid in others:
        _visit(redis, vid)
    agent = await _agent(redis)
    for vid in others:
        await live_bot.greet(agent, vid, text="Hi, I'm Ana. Can I help you find a business class fare?")
    assert all(len(redis.kv[f"live:inbox:{vid}"]) == 1 for vid in others)
    assert redis.kv[f"live:claims:{AGENT}"] == set(held), "the panel's three stay the agent's to use"
    assert live_bot.BOT_HEALTH["sent"] == 6


@pytest.mark.asyncio
async def test_a_visitor_the_agent_holds_from_the_panel_is_theirs_to_greet_by_their_bot(redis):
    _visit(redis)
    redis.kv[f"live:claim:{VID}"] = AGENT
    agent = await _agent(redis)
    await live_bot.greet(agent, VID)
    assert redis.kv[f"live:claim:{VID}"] == f"bot:{AGENT}"


@pytest.mark.asyncio
@pytest.mark.parametrize("code, rc", [("already", -5), ("bot_already", -6), ("taken", -4), ("declined", -3),
                                      ("in_a_chat", -2), ("gone", -1), ("changed", -7)])
async def test_the_script_settles_what_changed_after_the_read(redis, code, rc):
    """Between the visitor read and the send, the script checks again: whatever it refuses comes back with its code."""
    _visit(redis)
    redis.register(live_bot.BOT_SEND_LUA, lambda fake, keys, args: rc)
    agent = await _agent(redis)
    with pytest.raises(BotRefused) as e:
        await live_bot.greet(agent, VID)
    assert e.value.code == code
    assert f"live:inbox:{VID}" not in redis.kv and live_bot.BOT_HEALTH["refused"] == 1


@pytest.mark.asyncio
async def test_a_step_taken_after_the_read_holds_the_hello(redis):
    """CodeRabbit #282, round 7: the script gets the steps why_not judged; one taken since (the quote form opened)
    refuses the hello, nothing sent, and read again the visitor waits as the panel's rule says."""
    _visit(redis, activity="search:JFK-IST")
    agent = await _agent(redis)
    read = live_bot._one

    async def form_opened_meanwhile(r, vid):
        v = await read(r, vid)
        redis.kv[f"live:v:{vid}"]["activity"] = "search:JFK-IST;quote_opened"
        return v

    with patch.object(live_bot, "_one", new=form_opened_meanwhile):
        with pytest.raises(BotRefused) as e:
            await live_bot.greet(agent, VID)
    assert (e.value.status, e.value.code) == (409, "changed")
    assert f"live:inbox:{VID}" not in redis.kv and f"live:said:{VID}" not in redis.kv
    with pytest.raises(BotRefused) as e:
        await live_bot.greet(agent, VID)
    assert e.value.code == "filling_form"
    # Rule 5: the steps it read are the visitor's steps: the hello goes.
    _visit(redis, OTHER, activity="search:JFK-IST")
    await live_bot.greet(agent, OTHER)
    assert len(redis.kv[f"live:inbox:{OTHER}"]) == 1


@pytest.mark.asyncio
async def test_a_hello_whose_answer_was_lost_says_how_to_tell(redis):
    """A 503 may come after the hello went (the reply lost): the bot reads the visitors (state my_bot) before trying
    again (CodeRabbit #282, round 7)."""
    _visit(redis)
    agent = await _agent(redis)

    def sent_then_lost(fake, keys, args):
        _bot_send_lua(fake, keys, args)
        raise ConnectionError("the reply was lost")

    redis.register(live_bot.BOT_SEND_LUA, sent_then_lost)
    with pytest.raises(BotRefused) as e:
        await live_bot.greet(agent, VID)
    assert e.value.status == 503 and "my_bot" in e.value.detail
    redis_client.set_client_for_tests(redis)  # Redis is back after its cooldown
    (row,) = [v for v in await live_bot.visitors(agent) if v["visitor_id"] == VID]
    assert row["state"] == "my_bot" and row["may_greet"] is False


@pytest.mark.asyncio
async def test_a_visitor_who_left_is_gone(redis):
    agent = await _agent(redis)
    with pytest.raises(BotRefused) as e:
        await live_bot.greet(agent, "not-on-the-site-at-all")
    assert (e.value.code, e.value.status) == ("gone", 410)


@pytest.mark.asyncio
@pytest.mark.parametrize("text, code", [
    ("Call me on +1 214 208 5185", "contact"), ("see https://scam.example.test/deal", "contact"),
    ("Fares to IST from $1,200 this week", "price"), ("", "length"), ("x" * 301, "length"),
])
async def test_the_bots_own_words_are_held_to_the_clients_rules(redis, text, code):
    _visit(redis)
    agent = await _agent(redis)
    with pytest.raises(BotRefused) as e:
        await live_bot.greet(agent, VID, text=text)
    assert e.value.code == code and e.value.status == 422
    assert f"live:inbox:{VID}" not in redis.kv


@pytest.mark.asyncio
async def test_the_bots_own_words_go_without_a_long_dash_and_keep_their_group(redis):
    _visit(redis)
    agent = await _agent(redis)
    out = await live_bot.greet(agent, VID, text="Hi — I'm Ana. Want me to check your dates?", group="back")
    assert out["text"] == "Hi, I'm Ana. Want me to check your dates?" and out["group"] == "back"
    _visit(redis, OTHER)
    other = await live_bot.greet(agent, OTHER, text="Hi, I'm Ana.", group="vip")
    assert other["group"] is None, "a group the panel doesn't name is left out, the hello goes"


@pytest.mark.asyncio
@pytest.mark.parametrize("to, code", [("Call +1 214 208 5185", "contact"), ("IST from $1,200", "price")])
async def test_the_ready_hello_is_held_to_the_clients_rules_too(redis, to, code):
    """CodeRabbit #282, round 7: the ready hello carries the page's destination (the visitor's own page) and the
    agent's name: the same rules as the bot's own words."""
    _visit(redis, returning="1", to=to)
    agent = await _agent(redis)
    with pytest.raises(BotRefused) as e:
        await live_bot.greet(agent, VID)
    assert (e.value.status, e.value.code) == (422, code)
    assert f"live:inbox:{VID}" not in redis.kv and live_bot.BOT_HEALTH["refused"] == 1


@pytest.mark.asyncio
async def test_the_ready_hello_goes_without_a_long_dash(redis):
    _visit(redis, returning="1", to="Paris — CDG")
    agent = await _agent(redis)
    out = await live_bot.greet(agent, VID)
    assert "—" not in out["text"] and "Paris" in out["text"]


@pytest.mark.asyncio
async def test_nothing_is_sent_while_off(redis, monkeypatch):
    _visit(redis)
    agent = await _agent(redis)
    monkeypatch.setenv("AGENT_FIRST_MESSAGE", "false")
    with pytest.raises(BotRefused) as e:
        await live_bot.greet(agent, VID)
    assert e.value.code == "off" and "eval" not in redis.calls


# ── the tap: the chat is the agent's, and the bot's to answer ───────


@pytest.mark.asyncio
async def test_the_tapped_chat_is_the_agents_and_marked_the_bots(redis):
    from app.api.chat import ChatInitRequest

    _visit(redis, returning="1")
    agent = await _agent(redis)
    out = await live_bot.greet(agent, VID)
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap))
    assert res.live_agent == "Ana" and res.live_hello == out["text"] and res.live_at == STORED_AT
    m["claim"].assert_awaited_once_with("c1", AGENT)
    assert m["add"].await_args.args[1:3] == ("agent", out["text"])
    live_hello = m["meta"].await_args.args[1]["live_hello"]
    assert live_hello["bot"] is True and live_hello["agent_id"] == AGENT and live_hello["group"] == "back"


@pytest.mark.asyncio
@pytest.mark.parametrize("given_back, reread, theirs", [
    (True, None, False),
    (False, None, True),                                          # can't be read: the agent's, as for a lost hello
    (False, {"id": "c1", "assigned_agent_id": AGENT}, True),
    (False, {"id": "c1", "assigned_agent_id": "someone-else"}, False),
])
async def test_a_bots_chat_that_cannot_be_marked_goes_to_skye(redis, given_back, reread, theirs):
    """CodeRabbit #282, round 7: without live_hello.bot the bot never sees the chat: twice not written, it is given back
    (Skye answers the visitor who tapped); one that can't be given back stays the agent's, counted. Round 8: read
    again first, so a chat someone else took meanwhile is never named the agent's."""
    from app.api.chat import ChatInitRequest

    _visit(redis)
    agent = await _agent(redis)
    await live_bot.greet(agent, VID)
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap), marked=False, given_back=given_back,
                         reread=reread)
    assert m["meta"].await_count == 2 and live_visitors.LIVE_HEALTH["mark_failed"] == 1
    m["give_back"].assert_awaited_once_with("c1", AGENT)
    assert res.live_agent == ("Ana" if theirs else None)
    assert live_visitors.LIVE_HEALTH["release_failed"] == (0 if given_back else 1)
    assert m["reread"].await_count == (0 if given_back else 1)


@pytest.mark.asyncio
async def test_a_hello_from_the_panel_is_never_marked_the_bots(redis):
    """Rule 5: the mark comes with the bot's hello only."""
    from app.api.chat import ChatInitRequest

    redis.kv[f"live:claim:{VID}"] = AGENT
    await live_visitors.send_hello(SALES, SALES_DB, VID, "Hi, I'm Ana. Can I help with your trip?")
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    _, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap))
    assert "bot" not in m["meta"].await_args.args[1]["live_hello"]


# ── the bot in its chats ────────────────────────────────────────────


def _conv(**over):
    return {"id": CONV, "assigned_agent_id": AGENT, "mode": "human", "status": "active", "tunnel": "sales",
            "metadata": {"live_hello": {"id": "h1", "agent_id": AGENT, "bot": True}}, **over}


@pytest.mark.asyncio
async def test_the_bots_chats_say_who_wrote_last(redis):
    agent = await _agent(redis)
    rows = [{"id": CONV, "visitor_id": VID, "status": "active", "created_at": "2026-11-25T18:00:00+00:00",
             "updated_at": "2026-11-25T18:03:00+00:00", "live_hello": {"id": "h1", "tapped_at": "2026-11-25T18:00:01+00:00"},
             "last_user_message_at": "2026-11-25T18:03:00+00:00", "last_agent_message_at": "2026-11-25T18:02:00+00:00"},
            {"id": "c2", "visitor_id": OTHER, "status": "active", "created_at": "x", "updated_at": "y", "live_hello": None,
             "last_user_message_at": None, "last_agent_message_at": "2026-11-25T18:02:00+00:00"}]
    with patch("app.db.supabase.list_live_bot_chats", new=AsyncMock(return_value=rows)) as q:
        out = await live_bot.chats(agent)
    q.assert_awaited_once_with(AGENT, live_bot.CHATS_LIMIT)
    assert [(c["conversation_id"], c["waiting"]) for c in out] == [(CONV, True), ("c2", False)]
    assert out[0]["started_at"] == "2026-11-25T18:00:01+00:00" and out[0]["hello_id"] == "h1"
    with patch("app.db.supabase.list_live_bot_chats", new=AsyncMock(return_value=None)):
        with pytest.raises(BotRefused) as e:
            await live_bot.chats(agent)
    assert (e.value.status, e.value.code) == (503, "db") and live_bot.BOT_HEALTH["read_failed"] == 1


@pytest.mark.asyncio
async def test_the_bot_reads_its_chat(redis):
    agent = await _agent(redis)
    msgs = [{"id": "m1", "role": "agent", "content": "Hi, I'm Ana.", "created_at": "t1", "cost": 0.1},
            {"id": "m2", "role": "user", "content": "Hi! Two seats to IST?", "created_at": "t2", "model_used": None}]
    with patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=_conv())), \
            patch("app.db.supabase.get_messages_after", new=AsyncMock(return_value=msgs)) as read:
        out = await live_bot.messages(agent, CONV, "2026-11-25T18:00:00Z")
        with pytest.raises(BotRefused) as e:
            await live_bot.messages(agent, CONV, "yesterday'),(1")
    read.assert_awaited_once_with(CONV, "2026-11-25T18:00:00+00:00")
    assert (e.value.status, e.value.code) == (422, "after")
    assert out == [{"id": "m1", "role": "agent", "text": "Hi, I'm Ana.", "at": "t1"},
                   {"id": "m2", "role": "visitor", "text": "Hi! Two seats to IST?", "at": "t2"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("conv", [
    _conv(metadata={"live_hello": {"id": "h1", "agent_id": AGENT}}),   # a hello from the panel
    # A colleague's bot's chat a supervisor moved to the agent (CodeRabbit #282, round 7).
    _conv(metadata={"live_hello": {"id": "h1", "agent_id": COLLEAGUE, "bot": True}}),
    _conv(metadata={}),                                                 # no hello at all
    _conv(assigned_agent_id=COLLEAGUE),                                 # a colleague's now
    _conv(mode="ai"),                                                   # back with Skye
    _conv(status="closed"),
])
async def test_a_chat_that_isnt_the_bots_is_never_read_or_answered(redis, conv):
    """Rule 5: the bot reads and answers the chats its own hellos started, while they are the agent's."""
    agent = await _agent(redis)
    with patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=conv)), \
            patch("app.db.supabase.get_messages_after", new=AsyncMock(return_value=[])) as read, \
            patch("app.api.conversations.send_as_agent", new=AsyncMock()) as send:
        for call in (live_bot.messages(agent, CONV), live_bot.say(agent, CONV, "Hello")):
            with pytest.raises(BotRefused) as e:
                await call
            assert (e.value.status, e.value.code) == (403, "not_the_bots")
    read.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_bot_answers_as_the_agent_through_the_panels_own_send(redis):
    agent = await _agent(redis)
    stored = {"id": "m3", "content": "Two seats, got it. Which dates?", "created_at": "t3"}
    with patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=_conv())), \
            patch("app.api.conversations.send_as_agent",
                  new=AsyncMock(return_value={"success": True, "data": stored})) as send:
        out = await live_bot.say(agent, CONV, "Two seats — got it. Which dates?")
    assert out == {"id": "m3", "text": "Two seats, got it. Which dates?", "at": "t3"}
    conv_id, body, user = send.await_args.args
    assert conv_id == CONV and body.content == "Two seats, got it. Which dates?"
    assert user == {"id": AGENT, "email": "ana@example.com", "role": "sales", "tunnel_scope": "sales",
                    "name": "Ana Agent"}
    # Only while the chat is the agent's, never claimed (CodeRabbit #282, round 7).
    assert send.await_args.kwargs == {"must_hold": True}
    assert live_bot.BOT_HEALTH["messages_sent"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text, code", [
    ("Call me on +1 214 208 5185", "contact"), ("Business class to IST is $3,400 return", "price"),
    ("x" * (live_bot.MAX_MESSAGE + 1), "length"),
])
async def test_the_bots_answers_are_held_to_the_clients_rules(redis, text, code):
    agent = await _agent(redis)
    with patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=_conv())), \
            patch("app.api.conversations.send_as_agent", new=AsyncMock()) as send:
        with pytest.raises(BotRefused) as e:
            await live_bot.say(agent, CONV, text)
    assert (e.value.status, e.value.code) == (422, code)
    send.assert_not_awaited()
    assert live_bot.BOT_HEALTH["messages_refused"] == 1


@pytest.mark.asyncio
async def test_a_chat_that_moves_while_its_messages_are_read_gives_none_of_them(redis):
    """CodeRabbit #282, round 7: the chat is read again after its messages: what the bot gets was said in the agent's
    chat."""
    agent = await _agent(redis)
    msgs = [{"id": "m9", "role": "user", "content": "To the agent who has the chat now", "created_at": "t9"}]
    with patch("app.db.supabase.get_conversation_simple",
               new=AsyncMock(side_effect=[_conv(), _conv(assigned_agent_id=COLLEAGUE)])), \
            patch("app.db.supabase.get_messages_after", new=AsyncMock(return_value=msgs)):
        with pytest.raises(BotRefused) as e:
            await live_bot.messages(agent, CONV)
    assert (e.value.status, e.value.code) == (403, "not_the_bots")


@pytest.mark.asyncio
async def test_a_chat_that_moved_before_the_send_is_refused_by_the_send(redis):
    from fastapi import HTTPException

    agent = await _agent(redis)
    with patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=_conv())), \
            patch("app.api.conversations.send_as_agent",
                  new=AsyncMock(side_effect=HTTPException(status_code=409, detail="The chat is no longer yours."))):
        with pytest.raises(BotRefused) as e:
            await live_bot.say(agent, CONV, "Which dates?")
    assert (e.value.status, e.value.code) == (403, "not_the_bots") and live_bot.BOT_HEALTH["messages_refused"] == 1


def _send_patches(conv):
    from app.api import conversations as mod

    return [
        patch.object(mod.db, "get_user_by_id", new=AsyncMock(return_value={"id": AGENT, "role": "sales", "name": "Ana Agent"})),
        patch.object(mod.db, "get_conversation", new=AsyncMock(return_value=conv)),
        patch.object(mod, "add_message", new=AsyncMock(return_value={"id": "m3", "role": "agent", "content": "Hi"})),
        patch.object(mod.db, "count_agent_messages_since", new=AsyncMock(return_value=2)),
        patch.object(mod.db, "update_conversation", new=AsyncMock(return_value=conv)),
        patch.object(mod.manager, "push", new=AsyncMock()),
        patch.object(mod.db, "update_user_last_seen", new=AsyncMock()),
        patch("app.services.handoff.perform_handoff_to_agent", new=AsyncMock()),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("conv", [_conv(assigned_agent_id=COLLEAGUE), _conv(mode="ai", assigned_agent_id=None),
                                  _conv(status="closed")])
async def test_the_bots_send_writes_nothing_in_a_chat_no_longer_the_agents(conv):
    """CodeRabbit #282, round 7: must_hold refuses before any write and never claims the chat (no manual handoff).
    Round 8: a chat the agent closed keeps its owner and mode, and is refused too."""
    from fastapi import HTTPException

    from app.api import conversations as mod

    ps = _send_patches(conv)
    mocks = [p.start() for p in ps]
    try:
        with pytest.raises(HTTPException) as e:
            await mod.send_as_agent(CONV, mod.AgentMessageBody(content="Which dates?"), dict(SALES), must_hold=True)
    finally:
        for p in ps:
            p.stop()
    assert e.value.status_code == 409
    add, update, claim = mocks[2], mocks[4], mocks[7]
    add.assert_not_awaited()
    update.assert_not_awaited()
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_chat_closed_after_the_bots_check_gets_no_answer(redis):
    """CodeRabbit #282, round 8: the chat was open when the bot read it and closed by the agent before the send:
    nothing is written."""
    from app.api import conversations as mod

    agent = await _agent(redis)
    ps = _send_patches(_conv(status="closed"))
    mocks = [p.start() for p in ps]
    try:
        with patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=_conv())):
            with pytest.raises(BotRefused) as e:
                await live_bot.say(agent, CONV, "Which dates?")
    finally:
        for p in ps:
            p.stop()
    assert (e.value.status, e.value.code) == (403, "not_the_bots")
    mocks[2].assert_not_awaited()
    mocks[4].assert_not_awaited()
    assert mod.db.OPEN_STATUSES == ("active", "needs_agent")


@pytest.mark.asyncio
async def test_the_bots_send_in_its_chat_and_the_panels_send_are_as_before():
    """Rule 5: the bot's chat, still the agent's, is answered; the panel's send still claims a chat it doesn't hold,
    and still answers in a chat the agent holds that is closed (round 8 changes only the bot's send)."""
    from app.api import conversations as mod

    for conv, must_hold, claims in ((_conv(), True, False), (_conv(status="needs_agent"), True, False),
                                    (_conv(assigned_agent_id=COLLEAGUE), False, True),
                                    (_conv(status="closed"), False, False)):
        ps = _send_patches(conv)
        mocks = [p.start() for p in ps]
        try:
            out = await mod.send_as_agent(CONV, mod.AgentMessageBody(content="Which dates?"), dict(SALES),
                                          must_hold=must_hold)
        finally:
            for p in ps:
                p.stop()
        assert out["success"] is True and out["data"]["id"] == "m3"
        assert mocks[7].await_count == (1 if claims else 0)


@pytest.mark.asyncio
async def test_the_bots_chat_list_is_its_own_hellos_only():
    """CodeRabbit #282, round 7: the list asks for the agent's bot's own hellos, next to the bot mark."""
    from app.db import supabase as db

    calls: list = []

    class Q:
        def __getattr__(self, name):
            def step(*a, **kw):
                calls.append((name, a))
                return self
            return step

        def execute(self):
            return type("R", (), {"data": []})()

    class C:
        def table(self, name):
            return Q()

    with patch.object(db, "get_client", return_value=C()), patch.object(db, "_supervisor_columns_available", return_value=False):
        assert await db.list_live_bot_chats(AGENT, 5) == []
    for want in (("eq", ("assigned_agent_id", AGENT)), ("eq", ("metadata->live_hello->>bot", "true")),
                 ("eq", ("metadata->live_hello->>agent_id", AGENT))):
        assert want in calls, want


@pytest.mark.asyncio
async def test_an_answer_that_is_not_stored_is_said_so(redis):
    agent = await _agent(redis)
    with patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=_conv())), \
            patch("app.api.conversations.send_as_agent",
                  new=AsyncMock(return_value={"success": True, "data": None})):
        with pytest.raises(BotRefused) as e:
            await live_bot.say(agent, CONV, "Which dates?")
    assert (e.value.status, e.value.code) == (503, "not_stored") and live_bot.BOT_HEALTH["send_failed"] == 1


# ── the words: the panel's ready hello ──────────────────────────────

NOW_MS = int(datetime(2026, 11, 25, 18, tzinfo=timezone.utc).timestamp() * 1000)
WHO = "Hi, I'm Ana from the travel team."


@pytest.mark.parametrize("v, words, group", [
    ({"activity": ["search:JFK-IST", "quote_opened"]},
     f"{WHO} Need a hand with the quote form? I can take your trip details right here, it only takes a minute.", "form"),
    ({"activity": ["quote_opened", "quote_failed:server"]},
     f"{WHO} Did the quote form give you trouble? I can take your request right here.", "form"),
    ({"device": "computer", "tz": "Australia/Sydney", "trip": {"to": "LHR"}},
     f"{WHO} Planning the long trip to LHR? I can find business class options for your dates and send them right here.",
     "anz"),
    ({"device": "computer", "trip": {"to": "IST", "depart": "2026-12-20"}},
     f"{WHO} Flying to IST on Dec 20? A good moment to book business class. Want me to check options for your dates?",
     "soon"),
    ({"returning": True, "trip": {"to": "IST"}},
     f"{WHO} Welcome back! Want me to pick up your search for IST and check business class options for you?", "back"),
    ({"device": "phone", "tz": "Australia/Sydney", "trip": {"to": "IST", "depart": "2027-01-24"}},
     f"{WHO} Looking at flights to IST? I can check business class fares for your dates.", None),
    ({"activity": ["quote_opened", "quote_submitted"]},
     f"{WHO} Thanks for your request. Anything you'd like me to know while I prepare your options?", None),
    ({}, f"{WHO} Can I help you find a business class fare?", None),
])
def test_the_ready_hello_is_the_panels(v, words, group):
    from app.ai.output_guard import foreign_contact_kinds, has_price

    row = {"visitor_id": VID, "activity": [], "device": "computer", **v}
    assert live_bot.ready_hello(row, NOW_MS, "Ana") == (words, group)
    assert "—" not in words and not has_price(words) and not foreign_contact_kinds(words)


def test_the_words_and_the_rules_are_still_the_panels():
    """The hello the panel has ready (view.ts) and the bot's are the same words, under the same rules."""
    view = PANEL_VIEW.read_text(encoding="utf-8")
    for piece in (
        "from the travel team.",
        "Need a hand with the quote form? I can take your trip details right here, it only takes a minute.",
        "Did the quote form give you trouble? I can take your request right here.",
        "Planning the long trip to ${to}? I can find business class options for your dates and send them right here.",
        "Planning a long trip? I can find business class options for your dates and send them right here.",
        "? A good moment to book business class. Want me to check options for your dates?",
        "Welcome back! Want me to pick up your search",
        "Thanks for your request. Anything you'd like me to know while I prepare your options?",
        "Looking at flights to ${t.to}? I can check business class fares for your dates.",
        "Can I help you find a business class fare?",
        "export const FORM_QUIET_S = 120",
        "export const ALEX_GAVE_UP_S = 30",
        "export const BOT_HOLDER = 'bot:'",
    ):
        assert piece in view, piece


# ── the endpoints ───────────────────────────────────────────────────


def _client():
    from app.api import live_bot as live_bot_api

    app = FastAPI()
    app.include_router(live_bot_api.router, prefix="/api")
    return TestClient(app)


@pytest.mark.asyncio
async def test_the_bots_endpoints_work_by_its_key(redis):
    _visit(redis)
    key = await _key()
    c = _client()
    auth = {"Authorization": f"Bearer {key}"}
    listed = c.get("/api/live/bot/visitors", headers=auth)
    assert listed.status_code == 200 and listed.json()["count"] == 1
    hello = c.post("/api/live/bot/hello", json={"visitor_id": VID}, headers=auth)
    assert hello.status_code == 200 and hello.json()["data"]["text"] == f"{WHO} Can I help you find a business class fare?"
    again = c.post("/api/live/bot/hello", json={"visitor_id": VID}, headers=auth)
    assert again.status_code == 409 and again.json() == {"success": False, "data": None, "count": 0,
                                                         "code": "already", "error": live_bot._WHY_NOT["already"]}
    for path in ("/api/live/bot/visitors", "/api/live/bot/chats", f"/api/live/bot/chats/{CONV}/messages"):
        res = c.get(path)
        assert res.status_code == 401 and res.json()["code"] == "key", path
    assert c.post(f"/api/live/bot/chats/{CONV}/messages", json={"text": "Hi"}).status_code == 401


def test_the_agent_takes_and_revokes_the_key_in_the_panel(redis):
    from app.api import live as live_api
    from app.security.auth import get_current_user

    app = FastAPI()
    app.include_router(live_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SALES
    c = TestClient(app)
    assert c.get("/api/live/bot-key").json()["data"] == {"active": False, "expires_at": None}
    made = c.post("/api/live/bot-key").json()["data"]
    assert made["key"] and made["expires_at"]
    assert c.get("/api/live/bot-key").json()["data"]["active"] is True
    assert c.delete("/api/live/bot-key").json()["data"] == {"revoked": True}
    assert c.get("/api/live/bot-key").json()["data"]["active"] is False
