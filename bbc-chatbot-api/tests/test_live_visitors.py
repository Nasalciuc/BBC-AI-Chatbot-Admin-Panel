"""The API's side of "On the site now" (contracts/live.md): the tokens it issues, the heartbeat's pass,
the widget's config, an agent's hello, and the tap that starts the chat as the agent's.

The scripts' real behaviour is tested in bbc-live's Go suite against a real Redis (TestTheSendScriptHoldsTheRules);
here the fake Redis is taught the same scripts, line by line, so the keys and arguments the API passes are
held to their order. Every name here is invented.
"""

import json
import os
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.infra import redis_client  # noqa: E402
from app.services import live_tokens, live_visitors  # noqa: E402
from tests.fake_redis import FakeRedis  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
VID = "3f1c2a7e-8b4d-4c1e-9a2f-6d5e4c3b2a10"
OTHER_VID = "4a2d3b8f-9c5e-4d2f-8b3a-7e6f5d4c3b21"
AGENT = "aaaaaaaa-0000-4000-8000-000000000001"
COLLEAGUE = "aaaaaaaa-0000-4000-8000-000000000002"
SALES = {"id": AGENT, "role": "sales", "tunnel_scope": "sales", "email": "ana@example.com", "name": "Ana Agent"}
SALES_DB = {"id": AGENT, "role": "sales", "tunnel_scope": "sales", "is_active": True, "chat_enabled": True,
            "name": "Ana Agent"}


# ── the contract ────────────────────────────────────────────────────


def test_the_scripts_are_the_contracts():
    for name in ("send.lua", "take_hello.lua", "release.lua"):
        mine = (pathlib.Path(live_visitors.__file__).with_name("live_lua") / name).read_bytes()
        assert mine == (CONTRACTS / "live" / name).read_bytes(), f"app/services/live_lua/{name}: copy the contract"


def _vectors():
    return json.loads((CONTRACTS / "live_tokens.json").read_text())


def test_the_agent_tokens_in_the_contract_read_back():
    v = _vectors()["agent"]
    secret = v["secret"].encode()
    for c in v["valid"]:
        payload, reason = live_tokens.decode(c["token"], "l1", secret, now=v["now"])
        assert reason == "ok" and payload["u"] == c["user"] and payload["r"] == c["role"] and payload["exp"] == c["exp"]
        assert live_tokens.encode("l1", payload, secret) == c["token"], "the same bytes as the API issued"
    for c in v["invalid"]:
        assert live_tokens.decode(c["token"], "l1", secret, now=v["now"])[1] == c["reason"], c


def test_the_visitor_tokens_in_the_contract_read_back(monkeypatch):
    from app.security import visitor_token
    from config.settings import settings

    v = _vectors()["visitor"]
    monkeypatch.setattr(settings, "visitor_token_secret", v["secret"])
    for c in v["valid"]:
        assert visitor_token.parse(c["token"], now=v["now"]) == (c["visitor_id"], "ok")
    for c in v["invalid"]:
        assert visitor_token.parse(c["token"], now=v["now"])[1] == c["reason"], c


@pytest.fixture
def configured(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "live_token_secret", "live-test-secret")
    monkeypatch.setattr(settings, "live_service_url", "https://live.example.test/")
    monkeypatch.setenv("LIVE_VISITORS", "true")
    monkeypatch.setenv("AGENT_FIRST_MESSAGE", "true")
    for k in live_visitors.LIVE_HEALTH:
        live_visitors.LIVE_HEALTH[k] = 0


def test_a_tap_token_carries_who_and_runs_out(configured):
    tok = live_tokens.tap_token(VID, AGENT, "h1", now=1000)
    assert live_tokens.read_tap(tok, now=1000 + live_tokens.TAP_TTL)[0] == {
        "v": VID, "a": AGENT, "h": "h1", "iat": 1000, "exp": 1000 + live_tokens.TAP_TTL}
    assert live_tokens.read_tap(tok, now=1001 + live_tokens.TAP_TTL) == (None, "expired")
    assert live_tokens.read_tap(live_tokens.agent_token(SALES), now=1000)[1] == "invalid", "an agent token is no tap"
    assert live_tokens.read_tap(tok[:-1] + ("0" if tok[-1] != "0" else "1"), now=1000)[1] == "invalid"


# ── the panel's pass and the widget's config ────────────────────────


def test_the_heartbeat_hands_a_pass_to_the_roles_that_see_the_list(configured):
    live = live_visitors.heartbeat_live(SALES)
    assert live["url"] == "https://live.example.test" and live["can_say_hello"] is True
    payload, reason = live_tokens.decode(live["token"], "l1", b"live-test-secret")
    assert reason == "ok" and payload["u"] == AGENT and payload["r"] == "sales"
    sup = live_visitors.heartbeat_live({"id": COLLEAGUE, "role": "supervisor", "tunnel_scope": "all"})
    assert sup["can_say_hello"] is False, "a supervisor sees the list, says no hello"
    for role in ("support", "qa", "project_manager", ""):
        assert live_visitors.heartbeat_live({"id": COLLEAGUE, "role": role}) is None, role


@pytest.mark.parametrize("off", ["flag", "secret", "url"])
def test_nothing_is_handed_while_off(configured, monkeypatch, off):
    from config.settings import settings

    if off == "flag":
        monkeypatch.setenv("LIVE_VISITORS", "false")
    elif off == "secret":
        monkeypatch.setattr(settings, "live_token_secret", "")
    else:
        monkeypatch.setattr(settings, "live_service_url", "")
    assert live_visitors.heartbeat_live(SALES) is None
    assert live_visitors.widget_config() == {"enabled": False}


def test_the_switches_are_off_at_merge():
    assert live_visitors.list_enabled() is False and live_visitors.hello_enabled() is False


def test_the_widget_learns_where_to_beat(configured):
    from app.api import chat as chat_api

    app = FastAPI()
    app.include_router(chat_api.router)
    r = TestClient(app).get("/chat/live")
    assert r.json() == {"enabled": True, "url": "https://live.example.test", "after_s": 20, "beat_s": 15, "hello": True}
    assert "max-age=60" in r.headers["cache-control"]


# ── the hello ───────────────────────────────────────────────────────


def _send_lua(fake, keys, args):
    """send.lua, line by line."""
    claim, dismissed, inbox, hello, said = keys
    agent, vid, message, hello_json, ttl = args
    if not fake._alive(claim) or fake.kv[claim] != agent:
        return -1
    if fake._alive(dismissed):
        return -2
    if fake._alive(said):
        return -3
    for key, value in ((said, agent), (hello, hello_json)):
        fake.kv[key] = value
        fake._set_expiry(key, ex=int(ttl))
    fake._list(inbox).append(message)
    fake._set_expiry(inbox, ex=int(ttl))
    fake.published = getattr(fake, "published", []) + [("live:inbox", vid)]
    return 1


def _take_hello_lua(fake, keys, args):
    if not fake._alive(keys[0]):
        return None
    return fake.kv.pop(keys[0])


def _release_lua(fake, keys, args):
    claim, claims = keys
    agent, vid = args
    if fake._alive(claim) and fake.kv[claim] == agent:
        fake.kv.pop(claim)
        fake.kv.get(claims, set()).discard(vid)
        return 1
    return 0


@pytest.fixture
def redis(configured):
    fake = FakeRedis()
    fake.register(live_visitors.SEND_LUA, _send_lua)
    fake.register(live_visitors.TAKE_HELLO_LUA, _take_hello_lua)
    fake.register(live_visitors.RELEASE_LUA, _release_lua)
    redis_client.set_client_for_tests(fake)
    with patch("config.settings.settings.redis_url", "redis://fake"):
        yield fake
    redis_client.set_client_for_tests(None)


def _hold(fake, vid=VID, agent=AGENT):
    fake.kv[f"live:claim:{vid}"] = agent
    fake.kv[f"live:claims:{agent}"] = {vid}


async def _send(text="Hi, I'm Ana from the travel team. Can I help with your trip?", *, user=SALES, user_db=SALES_DB,
                vid=VID):
    with patch("app.db.supabase.log_audit", new=AsyncMock()) as audit:
        out = await live_visitors.send_hello(user, user_db, vid, text)
    return out, audit


@pytest.mark.asyncio
async def test_a_hello_reaches_the_inbox_in_the_contracts_shape(redis):
    _hold(redis)
    out, audit = await _send("  Hi,   I'm Ana.\nLooking at Istanbul?  ")
    (msg_raw,) = redis.kv[f"live:inbox:{VID}"]
    msg = json.loads(msg_raw)
    example = json.loads((CONTRACTS / "live_inbox_message.json").read_text())
    assert set(msg) == set(example), "the widget reads the contract's fields"
    assert msg["kind"] == "hello" and msg["agent"] == "Ana" and msg["text"] == "Hi, I'm Ana. Looking at Istanbul?"
    tap, _ = live_tokens.read_tap(msg["tap"])
    assert tap["v"] == VID and tap["a"] == AGENT and tap["h"] == out["hello_id"] == msg["id"]
    hello = json.loads(redis.kv[f"live:hello:{out['hello_id']}"])
    assert hello == {"id": out["hello_id"], "visitor_id": VID, "agent_id": AGENT, "text": msg["text"],
                     "sent_at": msg["sent_at"]}
    assert redis.published == [("live:inbox", VID)]
    assert audit.await_args.args[2] == "live_hello_sent" and audit.await_args.args[5]["text"] == msg["text"]
    assert live_visitors.LIVE_HEALTH["sent"] == 1


@pytest.mark.parametrize("text", [
    "Call me on +1 214 208 5185", "write to ana.private@gmail.com", "see https://scam.example.test/deal",
])
@pytest.mark.asyncio
async def test_no_contact_or_link_in_a_hello(redis, text):
    _hold(redis)
    with pytest.raises(live_visitors.SendRefused) as e:
        await _send(text)
    assert e.value.status == 422 and e.value.code == "contact"
    assert f"live:inbox:{VID}" not in redis.kv and "eval" not in redis.calls


@pytest.mark.parametrize("text, code", [("", "length"), ("   ", "length"), ("x" * 301, "length")])
@pytest.mark.asyncio
async def test_a_hello_has_words_and_not_too_many(redis, text, code):
    _hold(redis)
    with pytest.raises(live_visitors.SendRefused) as e:
        await _send(text)
    assert e.value.code == code


@pytest.mark.parametrize("user, user_db", [
    ({**SALES, "role": "support", "tunnel_scope": "support"}, {**SALES_DB, "role": "support"}),
    ({**SALES, "role": "owner"}, {**SALES_DB, "role": "owner"}),
    ({**SALES, "tunnel_scope": "support"}, SALES_DB),
    (SALES, {**SALES_DB, "is_active": False}),
    (SALES, {**SALES_DB, "chat_enabled": False}),
    (SALES, None),
])
@pytest.mark.asyncio
async def test_only_a_sales_agent_who_takes_chats_says_hello(redis, user, user_db):
    _hold(redis)
    with pytest.raises(live_visitors.SendRefused) as e:
        await _send(user=user, user_db=user_db)
    assert e.value.status == 403


@pytest.mark.asyncio
async def test_a_visitor_someone_else_holds_said_not_now_or_heard_already(redis):
    with pytest.raises(live_visitors.SendRefused) as e:
        await _send()
    assert (e.value.status, e.value.code) == (409, "not_held")
    _hold(redis, agent=COLLEAGUE)
    with pytest.raises(live_visitors.SendRefused) as e:
        await _send()
    assert e.value.code == "not_held"
    _hold(redis)
    await _send()
    with pytest.raises(live_visitors.SendRefused) as e:
        await _send()
    assert e.value.code == "already"
    _hold(redis, vid=OTHER_VID)
    redis.kv[f"live:dismissed:{OTHER_VID}"] = "1"
    with pytest.raises(live_visitors.SendRefused) as e:
        await _send(vid=OTHER_VID)
    assert e.value.code == "declined"


@pytest.mark.asyncio
async def test_off_or_redis_down_sends_nothing(redis, monkeypatch):
    _hold(redis)
    redis.down = True
    with pytest.raises(live_visitors.SendRefused) as e:
        await _send()
    assert e.value.status == 503 and live_visitors.LIVE_HEALTH["send_failed"] == 1
    monkeypatch.setenv("AGENT_FIRST_MESSAGE", "false")
    with pytest.raises(live_visitors.SendRefused) as e:
        await _send()
    assert e.value.status == 404


def test_the_route_answers_in_the_admin_shape(redis):
    from app.api import live as live_api
    from app.security.auth import get_current_user

    _hold(redis)
    app = FastAPI()
    app.include_router(live_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SALES
    with patch("app.db.supabase.get_user_by_id", new=AsyncMock(return_value=SALES_DB)), \
            patch("app.db.supabase.log_audit", new=AsyncMock()):
        c = TestClient(app)
        ok = c.post("/api/live/send", json={"visitor_id": VID, "text": "Hi there, can I help?"})
        again = c.post("/api/live/send", json={"visitor_id": VID, "text": "Hi there, can I help?"})
    assert ok.status_code == 200 and ok.json()["success"] is True and ok.json()["data"]["hello_id"]
    assert again.status_code == 409 and "already" in again.json()["detail"]


# ── the tap ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_tap_takes_its_hello_once_and_frees_the_agents_place(redis):
    _hold(redis)
    out, _ = await _send("Hi, can I help with your trip?")
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    got = await live_visitors.take_tap(VID, tap)
    assert got == {"agent_id": AGENT, "text": "Hi, can I help with your trip?", "hello_id": out["hello_id"],
                   "sent_at": got["sent_at"]}
    assert f"live:claim:{VID}" not in redis.kv, "the agent's place came back"
    assert await live_visitors.take_tap(VID, tap) is None, "one tap"


@pytest.mark.asyncio
async def test_a_tap_for_another_visitor_or_switched_off_is_nothing(redis, monkeypatch):
    _hold(redis)
    await _send()
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    assert await live_visitors.take_tap(OTHER_VID, tap) is None
    assert await live_visitors.take_tap(VID, "t1.forged.0000") is None
    monkeypatch.setenv("AGENT_FIRST_MESSAGE", "false")
    assert await live_visitors.take_tap(VID, tap) is None
    assert live_visitors.LIVE_HEALTH["taps_refused"] == 3
    monkeypatch.setenv("AGENT_FIRST_MESSAGE", "true")
    assert await live_visitors.take_tap(VID, tap), "the hello still waits for the right tap"


def _request():
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="127.0.0.1")
    return req


def _init_patches(claim_won=True):
    return [
        patch("app.api.chat.is_blocked", new=AsyncMock(return_value=False)),
        patch("app.services.conversation_service.get_or_create_conversation", new=AsyncMock(return_value={"id": "c1"})),
        patch("app.db.supabase.claim_conversation_if_unassigned", new=AsyncMock(return_value={"won": claim_won})),
        patch("app.db.supabase.patch_conversation_presence", new=AsyncMock(return_value=True)),
        patch("app.db.supabase.get_user_by_id", new=AsyncMock(return_value={"name": "Ana Agent", "avatar_url": None})),
        patch("app.services.conversation_service.db.add_message", new=AsyncMock(return_value={"id": "m1"})),
    ]


async def _init(payload, claim_won=True):
    from app.api.chat import chat_init

    ps = _init_patches(claim_won)
    mocks = [p.start() for p in ps]
    try:
        res = await chat_init(payload, _request(), None)
    finally:
        for p in ps:
            p.stop()
    return res, dict(zip(["blocked", "conv", "claim", "meta", "user", "add"], mocks))


@pytest.mark.asyncio
async def test_the_tapped_chat_starts_as_the_agents_with_the_hello_first(redis):
    from app.api.chat import ChatInitRequest

    _hold(redis)
    await _send("Hi, can I help with your trip?")
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap))
    assert res.conversation_id == "c1" and res.live_agent == "Ana" and res.live_hello == "Hi, can I help with your trip?"
    m["claim"].assert_awaited_once_with("c1", AGENT)
    patched = m["meta"].await_args.args[1]
    assert patched["engaged_agent_id"] == AGENT and patched["live_hello"]["agent_id"] == AGENT
    conv_id, role, content = m["add"].await_args.args[:3]
    assert (conv_id, role, content) == ("c1", "agent", "Hi, can I help with your trip?")


@pytest.mark.asyncio
async def test_a_tap_that_does_not_hold_starts_an_ordinary_chat(redis):
    """Rule 5: an expired or forged tap, and no tap at all, change nothing in the chat."""
    from app.api.chat import ChatInitRequest

    for payload in (ChatInitRequest(visitor_id=VID, live_tap="t1.forged.0000"), ChatInitRequest(visitor_id=VID)):
        res, m = await _init(payload)
        assert res.conversation_id == "c1" and res.live_agent is None
        m["claim"].assert_not_awaited()
        m["add"].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_chat_the_agent_cannot_take_stays_the_visitors_and_is_counted(redis):
    from app.api.chat import ChatInitRequest

    _hold(redis)
    await _send()
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap), claim_won=False)
    assert res.conversation_id == "c1" and res.live_agent is None
    m["add"].assert_not_awaited()
    assert live_visitors.LIVE_HEALTH["tap_failed"] == 1
