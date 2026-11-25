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
    for name in ("send.lua", "take_hello.lua", "release.lua", "pass.lua"):
        mine = (pathlib.Path(live_visitors.__file__).with_name("live_lua") / name).read_bytes()
        assert mine == (CONTRACTS / "live" / name).read_bytes(), f"app/services/live_lua/{name}: copy the contract"


def _vectors():
    return json.loads((CONTRACTS / "live_tokens.json").read_text())


def test_the_agent_tokens_in_the_contract_read_back():
    v = _vectors()["agent"]
    secret = v["secret"].encode()
    for c in v["valid"]:
        payload, reason = live_tokens.decode(c["value"], "l1", secret, now=v["now"])
        assert reason == "ok" and payload["u"] == c["user"] and payload["r"] == c["role"] and payload["exp"] == c["exp"]
        assert live_tokens.encode("l1", payload, secret) == c["value"], "the same bytes as the API issued"
    for c in v["invalid"]:
        assert live_tokens.decode(c["value"], "l1", secret, now=v["now"])[1] == c["reason"], c


def test_the_passes_in_the_contract_read_back():
    v = _vectors()["pass"]
    secret = v["secret"].encode()
    for c in v["valid"]:
        payload, reason = live_tokens.decode(c["value"], "b1", secret, now=v["now"])
        assert reason == "ok" and payload["v"] == c["visitor_id"] and payload["exp"] == c["exp"]
        assert live_tokens.encode("b1", payload, secret) == c["value"], "the same bytes as the API issued"
    for c in v["invalid"]:
        assert live_tokens.decode(c["value"], "b1", secret, now=v["now"])[1] == c["reason"], c


def test_the_vectors_are_what_this_module_issues(monkeypatch):
    """contracts/live_tokens.json was written by beat_pass and agent_token: they still write the same."""
    from config.settings import settings

    v = _vectors()
    monkeypatch.setattr(settings, "live_token_secret", v["pass"]["secret"])
    first = v["pass"]["valid"][0]
    assert live_tokens.beat_pass(first["visitor_id"], now=first["exp"] - live_tokens.PASS_TTL) == first["value"]
    a = v["agent"]["valid"][0]
    user = {"id": a["user"], "role": a["role"], "tunnel_scope": a["tunnel"]}
    monkeypatch.setattr(settings, "live_token_secret", v["agent"]["secret"])
    assert live_tokens.agent_token(user, now=a["exp"] - live_tokens.AGENT_TTL) == a["value"]


@pytest.fixture
def configured(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "live_token_secret", "live-test-secret")
    monkeypatch.setattr(settings, "live_service_url", "https://live.example.test/")
    monkeypatch.setenv("LIVE_VISITORS", "true")
    monkeypatch.setenv("AGENT_FIRST_MESSAGE", "true")
    # The live list as it works with the owner's agent-first test off (0): every visitor listed, Alex never
    # waits. The test itself is tests/test_agent_first_test.py.
    monkeypatch.setenv("AGENT_FIRST_TEST_PERCENT", "0")
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


# ── the widget's pass ───────────────────────────────────────────────


def _pass_lua(fake, keys, args):
    """pass.lua, line by line."""
    passed, per_address = keys
    proven, ttl, most = args
    if proven == "1":
        fake.kv[passed] = "1"
        fake._set_expiry(passed, ex=int(ttl))
        return 1
    if fake._alive(passed):
        return -1
    n = (int(fake.kv[per_address]) if fake._alive(per_address) else 0) + 1
    fake.kv[per_address] = str(n)
    if n == 1:
        fake._set_expiry(per_address, ex=3600)
    if n > int(most):
        return -2
    fake.kv[passed] = "1"
    fake._set_expiry(passed, ex=int(ttl))
    return 1


@pytest.fixture
def passes(redis):
    redis.register(live_visitors.PASS_LUA, _pass_lua)
    return redis


def _ask(vid=VID, live_pass=None, ip="203.0.113.7", headers=None):
    from app.api import chat as chat_api

    app = FastAPI()
    app.include_router(chat_api.router)
    body = {"visitor_id": vid, **({"live_pass": live_pass} if live_pass is not None else {})}
    with patch("app.api.chat.client_ip", return_value=ip):
        return TestClient(app).post("/chat/live", json=body, headers=headers or {})


def test_a_new_visitor_gets_where_to_beat_and_a_pass(passes):
    r = _ask()
    out = r.json()
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store", "the pass is this visitor's"
    assert {k: out[k] for k in ("enabled", "url", "after_s", "beat_s", "hello")} == {
        "enabled": True, "url": "https://live.example.test", "after_s": 20, "beat_s": 15, "hello": True}
    payload, reason = live_tokens.read_pass(out["pass"])
    assert reason == "ok" and payload["v"] == VID and payload["exp"] - payload["iat"] == live_tokens.PASS_TTL
    assert out["pass_expires_at"] and live_visitors.LIVE_HEALTH["passes"] == 1
    assert not any("203.0.113.7" in k for k in passes.kv), "the address itself is not stored"


def test_a_visitor_id_with_a_pass_is_nobody_elses(passes):
    first = _ask().json()["pass"]
    assert _ask().json() == {"enabled": False}, "someone who saw the id in the list gets no pass for it"
    assert live_visitors.LIVE_HEALTH["passes_refused"] == 1
    again = _ask(live_pass=first).json()
    assert again["enabled"] is True and live_tokens.read_pass(again["pass"])[0]["v"] == VID, "the visitor's own pass renews"
    other = _ask(OTHER_VID).json()["pass"]
    assert _ask(VID, live_pass=other).json() == {"enabled": False}, "another visitor's pass proves nothing"
    forged = first[:-1] + ("0" if first[-1] != "0" else "1")
    assert _ask(VID, live_pass=forged).json() == {"enabled": False}
    agent = live_tokens.agent_token(SALES)
    assert _ask(VID, live_pass=agent).json() == {"enabled": False}, "an agent token is no pass"


def test_a_pass_just_past_its_time_still_renews(passes):
    first = _ask().json()["pass"]
    exp = live_tokens.read_pass(first)[0]["exp"]
    with patch("app.services.live_tokens.time.time", return_value=exp + live_tokens.PASS_GRACE):
        assert live_tokens.read_pass(first, grace=live_tokens.PASS_GRACE)[1] == "ok"
        assert _ask(live_pass=first).json()["enabled"] is True
    with patch("app.services.live_tokens.time.time", return_value=exp + live_tokens.PASS_GRACE + 1):
        assert _ask(live_pass=first).json() == {"enabled": False}, "the id is still bound: an old pass is no proof"


def test_new_ids_from_one_address_are_limited(passes):
    ids = [f"{n:08d}-0000-4000-8000-00000000abcd" for n in range(live_visitors.PASS_NEW_PER_ADDRESS + 1)]
    answers = [_ask(vid, ip="198.51.100.4").json()["enabled"] for vid in ids]
    assert answers == [True] * live_visitors.PASS_NEW_PER_ADDRESS + [False]
    assert _ask(ids[-1], ip="198.51.100.5").json()["enabled"] is True, "another address is not limited by it"
    held = _ask(ids[0], ip="198.51.100.4", live_pass=None)
    assert held.json() == {"enabled": False}
    renewed = _ask(ids[1], ip="198.51.100.4", live_pass=live_tokens.beat_pass(ids[1]))
    assert renewed.json()["enabled"] is True, "a renewal is no new id: the limit doesn't stop it"


@pytest.mark.parametrize("vid", ["short", "x" * 65, "has/slash-00000", "spaces in it 000"])
def test_a_visitor_id_that_is_not_one_gets_nothing(passes, vid):
    r = _ask(vid)
    assert r.status_code in (200, 422)
    assert r.json().get("enabled") is not True and not any(k.startswith("live:passed:") for k in passes.kv)


def test_off_or_redis_down_hands_no_pass_and_off_asks_no_redis(passes, monkeypatch):
    monkeypatch.setenv("LIVE_VISITORS", "false")
    with patch.object(passes, "eval", new=AsyncMock()) as ev:
        assert _ask().json() == {"enabled": False}
    ev.assert_not_called()
    monkeypatch.setenv("LIVE_VISITORS", "true")
    # Redis failing is for now: the widget asks again later instead of ending the page's live work
    # (CodeRabbit #273); not configured at all is off.
    with patch.object(passes, "eval", new=AsyncMock(side_effect=ConnectionError("down"))):
        assert _ask().json() == {"enabled": False, "later": True}
    assert live_visitors.LIVE_HEALTH["pass_failed"] == 1
    with patch("app.services.live_visitors.get_redis", return_value=None):
        assert _ask().json() == {"enabled": False, "later": True}, "cooling down after a failure"
    redis_client.set_client_for_tests(None)
    with patch("config.settings.settings.redis_url", ""):
        assert _ask().json() == {"enabled": False}
    assert live_visitors.LIVE_HEALTH["pass_failed"] == 3


def test_the_pass_is_made_before_the_id_is_bound_and_dropped_when_the_binding_says_no(passes):
    """The binding counts PASS_BINDING_S from the eval: made first, the pass can't outlive it (CodeRabbit #273).
    A refused binding hands nothing; a pass that can't be made binds nothing."""
    order = []
    real_beat, real_eval = live_tokens.beat_pass, passes.eval

    def beat(vid):
        order.append("pass")
        return real_beat(vid)

    async def bind(*a, **k):
        order.append("bind")
        return await real_eval(*a, **k)

    with patch("app.services.live_tokens.beat_pass", side_effect=beat), patch.object(passes, "eval", side_effect=bind):
        assert _ask().json()["enabled"] is True
        assert order == ["pass", "bind"]
        assert _ask().json() == {"enabled": False}, "the id is bound: the pass made for this request is dropped"
    with patch("app.services.live_tokens.beat_pass", return_value=None), \
            patch.object(passes, "eval", new=AsyncMock()) as ev:
        assert _ask(OTHER_VID).json() == {"enabled": False}
    ev.assert_not_called()


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
                vid=VID, group=None):
    with patch("app.db.supabase.log_audit", new=AsyncMock()) as audit:
        out = await live_visitors.send_hello(user, user_db, vid, text, group=group)
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
    refused = again.json()
    assert again.status_code == 409 and refused["success"] is False and refused["count"] == 0
    assert refused["code"] == "already" and "already" in refused["error"] and refused["data"] is None


# ── the tap ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_tap_takes_its_hello_once_and_frees_the_agents_place(redis):
    _hold(redis)
    out, _ = await _send("Hi, can I help with your trip?")
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    got = await live_visitors.take_tap(VID, tap)
    assert got == {"agent_id": AGENT, "text": "Hi, can I help with your trip?", "hello_id": out["hello_id"],
                   "sent_at": got["sent_at"], "group": None, "bot": False}
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


# When the hello became the chat's first message (the row the database answers with).
STORED_AT = "2026-11-23T10:00:00.123456+00:00"


def _init_patches(claim_won=True, agent_row=None, stored=True, given_back=True, reread=None, marked=True):
    row = {**SALES_DB, "avatar_url": None} if agent_row is None else agent_row
    # `marked`: what the presence fields' writes answer, one for all or one each (a list).
    mark = AsyncMock(side_effect=marked) if isinstance(marked, list) else AsyncMock(return_value=marked)
    return [
        patch("app.api.chat.is_blocked", new=AsyncMock(return_value=False)),
        patch("app.services.conversation_service.get_or_create_conversation", new=AsyncMock(return_value={"id": "c1"})),
        patch("app.db.supabase.claim_conversation_if_unassigned", new=AsyncMock(return_value={"won": claim_won})),
        patch("app.db.supabase.patch_conversation_presence", new=mark),
        patch("app.db.supabase.get_user_by_id", new=AsyncMock(return_value=row)),
        patch("app.services.conversation_service.db.add_message",
              new=AsyncMock(return_value={"id": "m1", "created_at": STORED_AT} if stored else None)),
        patch("app.db.supabase.release_live_claim", new=AsyncMock(return_value=given_back)),
        patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=reread)),
    ]


async def _init(payload, claim_won=True, agent_row=None, stored=True, given_back=True, reread=None, marked=True):
    from app.api.chat import chat_init

    ps = _init_patches(claim_won, agent_row, stored, given_back, reread, marked)
    mocks = [p.start() for p in ps]
    try:
        res = await chat_init(payload, _request(), None)
    finally:
        for p in ps:
            p.stop()
    return res, dict(zip(["blocked", "conv", "claim", "meta", "user", "add", "give_back", "reread"], mocks))


@pytest.mark.asyncio
async def test_the_tapped_chat_starts_as_the_agents_with_the_hello_first(redis):
    from app.api.chat import ChatInitRequest

    _hold(redis)
    await _send("Hi, can I help with your trip?")
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap))
    assert res.conversation_id == "c1" and res.live_agent == "Ana" and res.live_hello == "Hi, can I help with your trip?"
    # The widget that shows the hello already (the chat opened with it) reads messages after it.
    assert res.live_at == STORED_AT
    m["claim"].assert_awaited_once_with("c1", AGENT)
    patched = m["meta"].await_args.args[1]
    assert patched["engaged_agent_id"] == AGENT and patched["live_hello"]["agent_id"] == AGENT
    conv_id, role, content = m["add"].await_args.args[:3]
    assert (conv_id, role, content) == ("c1", "agent", "Hi, can I help with your trip?")


@pytest.mark.asyncio
@pytest.mark.parametrize("group, kept", [
    ("anz", "anz"), ("soon", "soon"), ("back", "back"), ("form", "form"),
    # Rule 5: a group the panel doesn't name, and none, are left out; the hello goes all the same.
    ("vip", None), ("", None), (None, None),
])
async def test_the_group_of_a_ready_hello_stays_with_it_to_the_chat(redis, group, kept):
    """The owner, 25 Nov 2026: the panel names the visitor's group and has its hello ready; the answers are
    counted per group, from the hello's audit and the chat its tap starts."""
    from app.api.chat import ChatInitRequest

    _hold(redis)
    out, audit = await _send("Hi, I'm Ana from the travel team. A long trip ahead?", group=group)
    hello = json.loads(redis.kv[f"live:hello:{out['hello_id']}"])
    assert hello.get("group") == kept and ("group" in hello) is bool(kept)
    assert audit.await_args.args[5]["group"] == kept
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    # The visitor's widget never sees it: the inbox message keeps the contract's fields.
    assert "group" not in json.loads(redis.kv[f"live:inbox:{VID}"][0])
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap))
    assert res.live_agent == "Ana"
    live_hello = m["meta"].await_args.args[1]["live_hello"]
    assert live_hello.get("group") == kept and ("group" in live_hello) is bool(kept)


def test_the_route_hands_the_group_on(redis):
    from app.api import live as live_api
    from app.api.live import SendBody
    from app.security.auth import get_current_user

    assert SendBody(visitor_id=VID, text="Hi").group is None
    with pytest.raises(Exception):
        SendBody(visitor_id=VID, text="Hi", group="x" * 17)
    _hold(redis)
    app = FastAPI()
    app.include_router(live_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SALES
    with patch("app.db.supabase.get_user_by_id", new=AsyncMock(return_value=SALES_DB)), \
            patch("app.db.supabase.log_audit", new=AsyncMock()):
        ok = TestClient(app).post("/api/live/send", json={"visitor_id": VID, "text": "Hi there!", "group": "back"})
    assert ok.status_code == 200
    assert json.loads(redis.kv[f"live:hello:{ok.json()['data']['hello_id']}"])["group"] == "back"


@pytest.mark.asyncio
async def test_a_tap_that_does_not_hold_starts_an_ordinary_chat(redis):
    """Rule 5: an expired or forged tap, and no tap at all, change nothing in the chat."""
    from app.api.chat import ChatInitRequest

    for payload in (ChatInitRequest(visitor_id=VID, live_tap="t1.forged.0000"), ChatInitRequest(visitor_id=VID)):
        res, m = await _init(payload)
        assert res.conversation_id == "c1" and res.live_agent is None and res.live_at is None
        m["claim"].assert_not_awaited()
        m["add"].assert_not_awaited()


@pytest.mark.parametrize("row", [{**SALES_DB, "is_active": False}, {**SALES_DB, "chat_enabled": False}, {}])
@pytest.mark.asyncio
async def test_a_hello_from_an_agent_who_no_longer_takes_chats_starts_an_ordinary_chat(redis, row):
    """The hello is up to 15 minutes old: an agent switched off or gone since gets no chat from it."""
    from app.api.chat import ChatInitRequest

    _hold(redis)
    await _send()
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap), agent_row=row)
    assert res.conversation_id == "c1" and res.live_agent is None and res.live_hello is None
    m["claim"].assert_not_awaited()
    m["add"].assert_not_awaited()
    assert live_visitors.LIVE_HEALTH["taps_refused"] == 1 and live_visitors.LIVE_HEALTH["tap_failed"] == 0


@pytest.mark.asyncio
async def test_a_hello_that_cannot_be_stored_gives_the_chat_back(redis):
    """After the claim the hello is the chat's first message; when it can't be stored the chat goes back to the
    line (unassigned, AI), so the visitor's ordinary opening is what the database says too."""
    from app.api.chat import ChatInitRequest

    _hold(redis)
    await _send()
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap), stored=False)
    assert res.conversation_id == "c1" and res.live_agent is None and res.live_hello is None
    m["claim"].assert_awaited_once_with("c1", AGENT)
    m["give_back"].assert_awaited_once_with("c1", AGENT)
    m["meta"].assert_not_awaited()
    assert live_visitors.LIVE_HEALTH["tap_failed"] == 1


@pytest.mark.asyncio
async def test_a_stored_hello_never_gives_the_chat_back(redis):
    from app.api.chat import ChatInitRequest

    _hold(redis)
    await _send()
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap))
    assert res.live_agent == "Ana"
    m["give_back"].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_release_keeps_the_hello_it_took(redis):
    _hold(redis)
    await _send()
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]

    def release_fails(fake, keys, args):
        raise ConnectionError("redis went away mid-tap")

    redis.register(live_visitors.RELEASE_LUA, release_fails)
    hello = await live_visitors.take_tap(VID, tap)
    assert hello and hello["agent_id"] == AGENT, "the hello taken is the visitor's, release or not"
    assert live_visitors.LIVE_HEALTH["release_failed"] == 1 and live_visitors.LIVE_HEALTH["tap_failed"] == 0


@pytest.mark.parametrize("row, gets", [
    (None, False), ({"role": ""}, False), ({"role": "sales", "is_active": False}, False),
    ({"role": "support"}, False), ({"role": "sales", "tunnel_scope": "sales", "is_active": True}, True),
    ({"role": "supervisor"}, True),
])
def test_the_list_pass_follows_the_database_row_not_the_session(configured, row, gets):
    from app.api.agent import _live_for

    session = {"id": AGENT, "role": "sales", "tunnel_scope": "sales"}
    assert (_live_for(session, row) is not None) is gets


@pytest.mark.asyncio
@pytest.mark.parametrize("marked, writes, counted", [
    (True, 1, 0),             # Rule 5: written at once, written once
    ([False, True], 2, 0),    # a write that failed once: the second holds
    (False, 2, 1),            # failed twice: counted, the chat from the panel stays the agent's
])
async def test_the_hellos_chat_fields_are_tried_twice(redis, marked, writes, counted):
    """CodeRabbit #282, round 7: the presence fields (the hello's live_hello among them) are no longer written once
    and forgotten."""
    from app.api.chat import ChatInitRequest

    _hold(redis)
    await _send()
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap), marked=marked)
    assert m["meta"].await_count == writes and live_visitors.LIVE_HEALTH["mark_failed"] == counted
    # Each try is the same write, on the tapped chat (CodeRabbit #282, round 8: a mock answers any arguments).
    tries = [c.args for c in m["meta"].await_args_list]
    assert all(conv_id == "c1" for conv_id, _ in tries)
    first = tries[0][1]
    assert set(first) == {"engaged_agent_id", "agent_assigned_at", "clock_agent_id", "live_hello"}
    assert first["engaged_agent_id"] == first["clock_agent_id"] == first["live_hello"]["agent_id"] == AGENT
    assert all(fields == first for _, fields in tries), "a second try writes what the first meant to"
    assert res.live_agent == "Ana", "a hello from the panel: its agent is at the panel, the chat stays theirs"
    m["give_back"].assert_not_awaited()


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


# ── CodeRabbit #273 round 2: the pass's binding, a give-back that fails ──


def test_the_id_stays_bound_while_its_own_pass_still_renews(passes):
    """The binding lasts the pass's life and its grace: an id is never free to a stranger while the pass it was
    handed can still renew it (bound for PASS_TTL alone, two browsers could hold passes for one id)."""
    import time as _time

    _ask()
    left = passes.expires[f"live:passed:{VID}"] - _time.monotonic()
    assert live_tokens.PASS_TTL < left <= live_tokens.PASS_TTL + live_tokens.PASS_GRACE
    assert live_visitors.PASS_BINDING_S == live_tokens.PASS_TTL + live_tokens.PASS_GRACE


@pytest.mark.asyncio
@pytest.mark.parametrize("reread", [{"id": "c1", "assigned_agent_id": AGENT}, None])
async def test_a_chat_that_cannot_be_given_back_stays_the_agents(redis, reread):
    """The hello was not stored and the give-back failed: the chat is still the agent's (or can't be read), so
    the visitor is told it is theirs, never handed an ordinary chat an agent holds in human mode."""
    from app.api.chat import ChatInitRequest

    _hold(redis)
    await _send()
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap), stored=False, given_back=False,
                         reread=reread)
    # The agent's, with no stored hello to read after: the widget then reads the chat from its start.
    assert res.live_agent == "Ana" and res.live_hello and res.live_at is None
    m["meta"].assert_awaited_once()
    assert live_visitors.LIVE_HEALTH["release_failed"] == 1 and live_visitors.LIVE_HEALTH["tap_failed"] == 1


@pytest.mark.asyncio
async def test_a_chat_that_is_no_longer_the_agents_starts_ordinary(redis):
    from app.api.chat import ChatInitRequest

    _hold(redis)
    await _send()
    tap = json.loads(redis.kv[f"live:inbox:{VID}"][0])["tap"]
    res, m = await _init(ChatInitRequest(visitor_id=VID, live_tap=tap), stored=False, given_back=False,
                         reread={"id": "c1", "assigned_agent_id": "someone-else"})
    assert res.live_agent is None and res.live_hello is None
    m["meta"].assert_not_awaited()


# ── the visit registry's pass (app/services/visit_registry.py, contracts/live.md "The visit registry") ──────


@pytest.fixture
def registry_on(passes, monkeypatch):
    from app.services import visit_registry

    monkeypatch.setenv("VISIT_REGISTRY", "true")
    for k in visit_registry.VISIT_HEALTH:
        visit_registry.VISIT_HEALTH[k] = None if k == "queued" else 0
    return passes


def test_the_registry_alone_hands_a_pass_and_no_beats(registry_on, monkeypatch):
    from app.services import visit_registry

    monkeypatch.setenv("LIVE_VISITORS", "false")
    out = _ask(headers={"cf-ipcountry": "us"}).json()
    assert out["enabled"] is False, "no beats, no list, no hello: the registry only"
    assert out["visits"] is True and out["url"] == "https://live.example.test" and out["country"] == "US"
    assert live_tokens.read_pass(out["pass"])[0]["v"] == VID
    assert "after_s" not in out and "hello" not in out
    assert live_visitors.LIVE_HEALTH["passes"] == 1 and visit_registry.VISIT_HEALTH["passes"] == 1


def test_with_the_list_on_the_registry_rides_on_the_same_pass(registry_on):
    from app.services import visit_registry

    out = _ask(headers={"cf-ipcountry": "GB"}).json()
    assert out["enabled"] is True and out["visits"] is True and out["country"] == "GB" and out["after_s"] == 20
    assert visit_registry.VISIT_HEALTH["passes"] == 0, "counted as the registry's alone only without the list"


def test_alexs_side_is_never_listed_but_its_visit_is_kept(registry_on, monkeypatch):
    from app.services import agent_first_test

    monkeypatch.setattr(agent_first_test, "side", lambda vid: agent_first_test.ALEX)
    out = _ask().json()
    assert out["enabled"] is False and out["test"] == "alex", "the owner's test: Alex's side is never listed"
    assert out["visits"] is True and live_tokens.read_pass(out["pass"])[0]["v"] == VID
    monkeypatch.setenv("VISIT_REGISTRY", "false")
    with patch.object(registry_on, "eval", new=AsyncMock()) as ev:
        assert _ask().json() == {"enabled": False, "test": "alex"}, "the registry off: as before"
    ev.assert_not_called()


@pytest.mark.parametrize("header", ["XX", "T1", "usa", "", "1A"])
def test_a_country_that_is_not_one_is_left_out(registry_on, header):
    out = _ask(headers={"cf-ipcountry": header}).json()
    assert out["visits"] is True and "country" not in out


def test_the_registry_off_adds_nothing(passes, monkeypatch):
    monkeypatch.delenv("VISIT_REGISTRY", raising=False)
    out = _ask(headers={"cf-ipcountry": "US"}).json()
    assert out["enabled"] is True and "visits" not in out and "country" not in out
    monkeypatch.setenv("LIVE_VISITORS", "false")
    assert _ask().json() == {"enabled": False}
