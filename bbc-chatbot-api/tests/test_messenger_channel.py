"""Facebook Messenger, the API's side (app.services.messenger_channel, contracts/messenger.md).

The gateway (bbc-messenger) is tested in its Go suite; here, what the API does with its events: the call it signs,
the person's chat, the same turn a widget message takes, what goes back to the gateway and what goes out through it
(an agent's message), what is kept without a reply, and the counters. Every id, name and text here is invented.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import FastAPI, HTTPException  # noqa: E402
from redis.exceptions import ResponseError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.infra import redis_client  # noqa: E402
from app.models.chat import ChatResponse  # noqa: E402
from app.services import messenger_channel as mc  # noqa: E402
from config.settings import settings  # noqa: E402
from tests.fake_redis import FakeRedis  # noqa: E402

PAGE, PSID = "PAGE1", "PSID-7"
CONV = "c0000000-0000-4000-8000-00000000m001"
SECRET = "shared-test-secret"


def now_iso(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def event(**over) -> mc.InboundEvent:
    data = {"kind": "message", "page_id": PAGE, "psid": PSID, "site": "bbc", "mid": "m1", "timestamp": 100,
            "text": "Hi, two seats to Istanbul in December?"}
    data.update(over)
    return mc.InboundEvent(**data)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for k in mc.MESSENGER_HEALTH:
        mc.MESSENGER_HEALTH[k] = 0
    mc._LAST.clear()
    mc._UNSENT.clear()
    mc._ENDED.clear()
    mc._UNKEPT.clear()
    mc._UNGIVEN.clear()
    mc._HELD_BESIDE.clear()
    mc._PUTTING_BACK.clear()
    mc._ORDER.clear()
    monkeypatch.setattr(mc, "_CLOSING", False)
    monkeypatch.setenv("MESSENGER_CHANNEL", "true")
    monkeypatch.setattr(settings, "messenger_gateway_url", "https://gateway.example.test")
    monkeypatch.setattr(settings, "messenger_gateway_secret", SECRET)
    yield


def give_to_gateway(fake: FakeRedis, keys: list, args: list) -> int:
    """What messenger_channel.GIVE_TO_GATEWAY_LUA does."""
    event_key, sending_key = keys
    token, answer, none_given, ex, right, right_ex = args[0], args[1], args[2], int(args[3]), args[4], int(args[5])
    api_to_send, its_id = args[6], args[7]
    sending = fake.kv.get(sending_key) if fake._alive(sending_key) else None
    if sending is not None and str(sending).startswith("gateway:") and str(sending).endswith(its_id):
        fake.kv[sending_key] = right  # the same delivery's, made again (a run whose answer was lost): from now
        fake._set_expiry(sending_key, ex=right_ex)
        return 1
    held = fake.kv.get(event_key) if fake._alive(event_key) else None
    if held is not None and held != token:
        return 0
    if sending is not None and not sending.startswith("gateway"):
        fake.kv[event_key] = none_given if sending == "done" else api_to_send
        fake._set_expiry(event_key, ex=ex)
        return 2
    fake.kv[sending_key] = right
    fake._set_expiry(sending_key, ex=right_ex)
    fake.kv[event_key] = answer
    fake._set_expiry(event_key, ex=ex)
    return 1


def last_taken(fake: FakeRedis, keys: list, args: list) -> int:
    """What messenger_channel.LAST_TAKEN_LUA does."""
    (key,) = keys
    at, ex = float(args[0]), int(args[1])
    if at > float(fake.kv.get(key) or 0 if fake._alive(key) else 0):
        fake.kv[key] = str(args[0])
    fake._set_expiry(key, ex=ex)
    return 1


def rekeep(fake: FakeRedis, keys: list, args: list) -> int:
    """What messenger_channel.REKEEP_LUA does."""
    sending_key, last_key, inbox, who, event_key = keys
    mark, at, k, place, kept_event, ex = args[0], float(args[1]), args[2], float(args[3]), args[4], int(args[5])
    if (fake.kv.get(sending_key) if fake._alive(sending_key) else None) != mark:
        return 0
    if float(fake.kv.get(last_key) or 0 if fake._alive(last_key) else 0) > at:
        return -1
    fake.kv[sending_key] = args[6]
    fake._set_expiry(sending_key, ex=ex)
    fake.kv[event_key] = kept_event
    fake._set_expiry(event_key, ex=ex)
    fake._zset(inbox)[k] = place
    fake._zset(who)[k] = place
    fake._set_expiry(who, ex=ex)
    return 1


def clear_unsent(fake: FakeRedis, keys: list, args: list) -> int:
    """What messenger_channel.CLEAR_UNSENT_LUA does."""
    inbox, sending_key, event_key = keys
    k, mark, answer, ex = args[0], args[1], args[2], int(args[3])
    if (fake.kv.get(sending_key) if fake._alive(sending_key) else None) != mark:
        return 0
    if k not in fake._zset(inbox):
        return -1
    if not fake._alive(event_key):
        fake.kv[event_key] = answer
        fake._set_expiry(event_key, ex=ex)
    fake.kv.pop(sending_key, None)
    return 1


def release_on_mark(fake: FakeRedis, keys: list, args: list) -> int:
    """What messenger_channel.RELEASE_ON_MARK_LUA does."""
    inbox, who, event_json, tries, sending_key = keys
    mark, k = args[0], args[1]
    if (fake.kv.get(sending_key) if fake._alive(sending_key) else None) != mark:
        return 0
    fake._zset(inbox).pop(k, None)
    fake._zset(who).pop(k, None)
    fake.kv.pop(event_json, None)
    fake.kv.pop(tries, None)
    return 1


def give_again(fake: FakeRedis, keys: list, args: list) -> int:
    """What messenger_channel.GIVE_AGAIN_LUA does."""
    sending_key, inbox, last_key = keys
    k, right, ex = args[0], args[1], int(args[2])
    s = fake.kv.get(sending_key) if fake._alive(sending_key) else None
    if s is None or not str(s).startswith("gateway"):
        return 1
    if k in fake._zset(inbox):
        kept_id = re.match(r"^gateway:[^:]*(:.*)$", str(s))
        fake.kv[sending_key] = f"gateway:{right}{kept_id.group(1) if kept_id else ''}"
        fake._set_expiry(sending_key, ex=ex)
        return 1
    when = re.match(r"^gateway:([^:]+)", str(s))
    try:
        at = float(when.group(1)) if when else 0.0
    except ValueError:
        at = 0.0
    if float(fake.kv.get(last_key) or 0 if fake._alive(last_key) else 0) > at:
        return -1
    return 1


@pytest.fixture
def redis():
    from tests.fake_redis import compare_and_delete, compare_and_pexpire, compare_or_absent_and_set

    r = FakeRedis()
    # Redis's clock moves with messenger_channel's own here (a test moving `mc.time` moves both), unless a test gives
    # its processes clocks of their own (_Clock).
    r.wall = lambda: mc.time.time()
    # The recovery's lock, and an event's claim, renewed and let go only while still the pass's or the turn's own
    # (conversation_lock's scripts); an event's answer written only over its turn's own claim, or nothing, and given
    # to the gateway with the right to send it only then.
    r.register(mc.RELEASE_LUA, compare_and_delete)
    r.register(mc.RENEW_LUA, compare_and_pexpire)
    r.register(mc.KEEP_ANSWER_LUA, compare_or_absent_and_set)
    r.register(mc.GIVE_TO_GATEWAY_LUA, give_to_gateway)
    r.register(mc.LAST_TAKEN_LUA, last_taken)
    r.register(mc.REKEEP_LUA, rekeep)
    r.register(mc.CLEAR_UNSENT_LUA, clear_unsent)
    r.register(mc.RELEASE_ON_MARK_LUA, release_on_mark)
    r.register(mc.GIVE_AGAIN_LUA, give_again)
    redis_client.set_client_for_tests(r)
    with patch("config.settings.settings.redis_url", "redis://fake"):
        yield r
    redis_client.set_client_for_tests(None)


class Chat:
    """The database and the chat's turn as the tests set them: rows stored in order, the turn's request kept."""

    def __init__(self, latest=None, conv=None):
        self.rows: list[dict] = []
        self.latest = {} if latest is None else latest
        self.conv = conv or {"id": CONV, "status": "active", "mode": "ai", "metadata": {}}
        self.requests: list = []
        self.patches: list[dict] = []
        self.answer = None  # what the turn does: a function (req) -> ChatResponse
        # Saves to refuse, by role, in order: "fail" (nothing stored, None back), "lost" (stored, the answer lost:
        # None back all the same), "unreadable" (its id stored before, and that row could not be read back).
        self.saves: dict[str, list[str]] = {}
        self.patch_ok = True
        self.unreadable: set[str] = set()  # row ids whose read by id fails

    async def add_message(self, conversation_id, role, content, model_used=None, cost=0.0, *, message_id=None,
                          on_stored=None, on_unknown=None):
        """As app.db.supabase.add_message: an id stored before is refused by the database and its row read back, and
        the caller is called back with the row the moment it is known stored (or found stored before)."""
        from app.db.supabase import StoredBefore

        queue = self.saves.get(role)
        what = queue.pop(0) if queue else None
        before = next((r for r in self.rows if message_id and r["id"] == message_id), None)
        if before is not None:
            if what == "unreadable":
                return None
            if on_stored is not None:
                on_stored(before)
            return StoredBefore(before)
        row = {"id": message_id or f"r{len(self.rows) + 1}", "conversation_id": conversation_id, "role": role,
               "content": content, "created_at": now_iso()}
        if what == "fail":
            return None
        self.rows.append(row)
        if what == "lost":
            return None  # stored, its answer lost: the caller never learns it was
        if on_stored is not None:
            on_stored(row)
        return row

    async def get_message(self, message_id):
        """As app.db.supabase.get_message: the row, None when there is none, raising when it could not be read
        (self.unreadable)."""
        if message_id in self.unreadable:
            raise RuntimeError("the database did not answer")
        return next((r for r in self.rows if r["id"] == message_id), None)

    async def recent(self, conversation_id, limit=5):
        return [{"role": r["role"], "content": r["content"], "created_at": r["created_at"]} for r in self.rows][-limit:]

    async def turn(self, req, request, _rate, token):
        self.requests.append((req, token))
        from app.services.conversation_service import add_message

        return await self.answer(req, add_message)

    async def patch(self, conversation_id, fields):
        self.patches.append(fields)
        return self.patch_ok

    def start(self):
        self.created = AsyncMock(return_value=self.conv)
        ps = [
            patch("app.db.supabase.add_message", new=self.add_message),
            patch("app.db.supabase.get_message", new=self.get_message),
            patch("app.realtime.manager.manager.push", new=AsyncMock()),
            patch("app.db.supabase.latest_conversation_of_visitor", new=AsyncMock(return_value=self.latest)),
            patch("app.services.conversation_service.get_or_create_conversation", new=self.created),
            patch("app.db.supabase.patch_conversation_presence", new=self.patch),
            patch("app.db.supabase.get_recent_messages", new=self.recent),
            patch("app.api.chat.chat", new=self.turn),
        ]
        for p in ps:
            p.start()
        self._ps = ps
        return self

    def stop(self):
        for p in self._ps:
            p.stop()


@pytest.fixture
def chat(redis):
    c = Chat()

    async def skye(req, add_message):
        row = await add_message(req.conversation_id, "ai", "Happy to help! Which dates in December?",
                                model_used="claude")
        return ChatResponse(conversation_id=req.conversation_id, message=row["content"], type="ai", model_used="claude")

    c.answer = skye
    c.start()
    yield c
    c.stop()


# ── the call between the gateway and the API ──────────────────────


def test_the_signature_is_the_gateways():
    """bbc-messenger's TestTheSignatureIsTheAPIs holds the same vector: both sides sign the same bytes."""
    assert mc.sign(SECRET.encode(), 1700000000, b'{"page_id":"PAGE1"}') == \
        "sha256=6ba3016f066046b9feae73d4fc707c60be6500545ede51024b6037601f8bb434"


def test_a_signed_call_is_checked_for_its_secret_and_its_time():
    body = b'{"kind":"message"}'
    now = 1_800_000_000
    good = mc.sign(SECRET.encode(), now, body)
    assert mc.valid_signed(str(now), good, body, now=now)
    assert mc.valid_signed(str(now), good, body, now=now + 299)
    assert not mc.valid_signed(str(now), good, body, now=now + 301), "a call played again later"
    assert not mc.valid_signed(str(now), good, body + b" ", now=now)
    assert not mc.valid_signed(str(now), mc.sign(b"another", now, body), body, now=now)
    assert not mc.valid_signed("soon", good, body, now=now)
    assert not mc.valid_signed(None, good, body, now=now)


@pytest.mark.parametrize("unix_header, signature", [
    ("9" * 400, None),                  # a time too big for the arithmetic: refused, never an OverflowError
    ("١٨٠٠٠٠٠٠٠٠", None),               # digits, but not the gateway's
    ("-1800000000", None),
    (None, "sha256=" + "é" * 64),       # not ASCII: refused, never compare_digest's TypeError
    (None, "SHA256=" + "a" * 64),
    (None, "sha256=" + "A" * 64),       # the gateway writes lower-case hex
    (None, "sha256=" + "a" * 63),
])
def test_a_signed_call_the_gateway_could_not_have_made_is_refused_not_raised(unix_header, signature):
    body = b'{"kind":"message"}'
    now = 1_800_000_000
    good = mc.sign(SECRET.encode(), now, body)
    assert mc.valid_signed(unix_header or str(now), signature or good, body, now=now) is False


def test_the_route_answers_401_to_headers_it_cannot_read():
    client = _app()
    body = event().model_dump_json().encode()
    with patch.object(mc, "handle", new=AsyncMock(return_value={"replies": []})) as handle:
        res = client.post("/api/channels/messenger/inbound", content=body, headers={
            "X-Gateway-Time": "9" * 400, "X-Gateway-Signature": "sha256=" + "0" * 64})
    assert res.status_code == 401 and handle.await_count == 0


def _app():
    from app.api.channels import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def _post(client, body: bytes, secret: str = SECRET, at=None):
    import time

    unix = int(at if at is not None else time.time())
    return client.post("/api/channels/messenger/inbound", content=body, headers={
        "X-Gateway-Time": str(unix), "X-Gateway-Signature": mc.sign(secret.encode(), unix, body)})


def test_the_route_answers_the_gateway_only(monkeypatch):
    client = _app()
    body = event().model_dump_json().encode()
    with patch.object(mc, "handle", new=AsyncMock(return_value={"replies": [{"text": "Hi"}]})) as handle:
        assert _post(client, body).json() == {"replies": [{"text": "Hi"}]}
        assert _post(client, body, secret="another").status_code == 401
        assert _post(client, b'{"kind":"call me"}').status_code == 422
        assert handle.await_count == 1
    assert mc.MESSENGER_HEALTH["bad_signature"] == 1
    with patch.object(mc, "handle", new=AsyncMock(side_effect=mc.StillAnswering())):
        assert _post(client, body).status_code == 409
    # Failed and kept: 503 with X-Messenger-Kept (the API answers it later itself); not kept: 503 alone.
    with patch.object(mc, "handle", new=AsyncMock(side_effect=mc.EventFailed(True))):
        res = _post(client, body)
        assert res.status_code == 503 and res.headers.get("x-messenger-kept") == "1"
    with patch.object(mc, "handle", new=AsyncMock(side_effect=mc.EventFailed(False))):
        res = _post(client, body)
        assert res.status_code == 503 and "x-messenger-kept" not in res.headers
    with patch.object(mc, "handle", new=AsyncMock(side_effect=RuntimeError("db"))):
        assert _post(client, body).status_code == 503
    assert mc.MESSENGER_HEALTH["turns_failed"] == 3
    # A body bigger than the gateway ever sends is not read, signed or not.
    assert _post(client, b"x" * ((1 << 20) + 1)).status_code == 413
    # Off: 503 (never 404, which ban_guard counts as a scan) with X-Messenger-Off (the gateway stops there), and
    # nothing is read.
    monkeypatch.setenv("MESSENGER_CHANNEL", "false")
    with patch.object(mc, "handle", new=AsyncMock()) as handle:
        res = _post(client, body)
        assert res.status_code == 503 and res.headers.get("x-messenger-off") == "1"
    handle.assert_not_awaited()


def test_the_channel_is_off_at_merge(monkeypatch):
    monkeypatch.delenv("MESSENGER_CHANNEL", raising=False)
    assert mc.enabled() is False
    monkeypatch.setenv("MESSENGER_CHANNEL", "true")
    assert mc.enabled() is True
    monkeypatch.setattr(settings, "messenger_gateway_secret", "")
    assert mc.enabled() is False, "no secret: off whatever the switch says"


# ── a message ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_first_message_starts_a_chat_and_takes_the_widgets_turn(chat, redis):
    out = await mc.handle(event(referral={"ref": "kayak-nyc", "source": "ADS", "ad_id": "ad9"}))
    assert out == {"replies": [{"text": "Happy to help! Which dates in December?"}]}
    # The chat: the person's visitor id, the channel, the Page and PSID to answer to, the brand, where they came from.
    args, kwargs = chat.created.await_args
    vid = mc.visitor_id_for(PAGE, PSID)
    assert args[:2] == (None, "sales") and kwargs["visitor_id"] == vid and vid.startswith("msgr-")
    meta = kwargs["metadata"]
    assert meta["chat_channel"] == "messenger" and meta["messenger"] == {"page_id": PAGE, "psid": PSID}
    assert meta["site"] == "bbc" and meta["utm_source"] == "messenger" and meta["utm_medium"] == "messenger_ads"
    assert meta["utm_campaign"] == "kayak-nyc" and meta["messenger_referral"]["ad_id"] == "ad9"
    # The turn: what they wrote, as a widget message of that chat, with the visitor's token.
    req, token = chat.requests[0]
    assert (req.message, req.conversation_id, req.visitor_id, req.tunnel) == (
        "Hi, two seats to Istanbul in December?", CONV, vid, "sales")
    assert req.metadata == {"site": "bbc"} and req.button is None
    # Where it answers to, for an agent's messages later.
    assert json.loads(redis.kv[f"msgr:conv:{CONV}"]) == {"page_id": PAGE, "psid": PSID}
    assert mc.MESSENGER_HEALTH["turns"] == 1 and mc.MESSENGER_HEALTH["replies"] == 1


@pytest.mark.asyncio
async def test_a_thread_goes_on_in_its_chat_and_an_old_one_starts_a_new_chat(redis):
    for latest, made in (({"id": CONV, "status": "closed", "updated_at": now_iso(days=-2), "metadata": {}}, False),
                         # The person's last message counts, whatever the chat's own last change.
                         ({"id": CONV, "status": "closed", "updated_at": now_iso(days=-9),
                           "last_user_message_at": now_iso(days=-1), "metadata": {}}, False),
                         ({"id": CONV, "status": "active", "updated_at": now_iso(days=-8), "metadata": {}}, True),
                         ({"id": CONV, "status": "closed", "updated_at": "not a time", "metadata": {}}, True)):
        c = Chat(latest=latest)

        async def quiet(req, add_message):
            return ChatResponse(conversation_id=req.conversation_id, message="", type="ai", model_used="x")

        c.answer = quiet
        c.start()
        try:
            await mc.handle(event(mid=f"m-{made}-{latest['updated_at']}"))
        finally:
            c.stop()
        assert c.created.await_count == (1 if made else 0)
        assert c.requests[0][0].conversation_id == CONV


@pytest.mark.asyncio
async def test_a_delivery_made_again_gets_the_same_replies_and_no_second_turn(chat, redis):
    first = await mc.handle(event())
    again = await mc.handle(event())
    assert again == first and len(chat.requests) == 1 and mc.MESSENGER_HEALTH["duplicates"] == 1
    # One still being answered: the gateway waits (409) and tries again.
    redis.kv["msgr:event:m2"] = "pending"
    with pytest.raises(mc.StillAnswering):
        await mc.handle(event(mid="m2"))


@pytest.mark.asyncio
async def test_a_turn_that_fails_can_be_delivered_again(chat, redis):
    async def broken(req, add_message):
        raise RuntimeError("model down")

    chat.answer = broken
    with pytest.raises(mc.EventFailed) as failed:
        await mc.handle(event())
    assert failed.value.kept is True and isinstance(failed.value.__cause__, RuntimeError)
    assert "msgr:event:m1" not in redis.kv, "the mark is cleared: the next delivery is answered"
    # Kept in the inbox until answered: the recovery's to answer if no delivery does.
    assert "m1" in redis.kv[mc.INBOX] and json.loads(redis.kv[f"{mc.INBOX}:m1"])["text"] == event().text

    async def skye(req, add_message):
        await add_message(req.conversation_id, "ai", "Back now.")
        return ChatResponse(conversation_id=req.conversation_id, message="Back now.", type="ai", model_used="x")

    chat.answer = skye
    assert (await mc.handle(event()))["replies"] == [{"text": "Back now."}]
    assert not redis.kv[mc.INBOX] and f"{mc.INBOX}:m1" not in redis.kv, "answered: out of the inbox"


@pytest.mark.asyncio
async def test_a_turn_the_chat_could_not_take_now_is_delivered_again(chat):
    """A 5xx from the turn (its message not saved, as after the sale) fails the event; rule 5: a 4xx (the
    blocklist, a limit) stays a refusal with no reply (test_a_turn_the_chat_refuses_gets_no_reply)."""
    async def unsaved(req, add_message):
        raise HTTPException(status_code=503, detail="Message could not be saved")

    chat.answer = unsaved
    with pytest.raises(mc.EventFailed):
        await mc.handle(event())
    assert mc.MESSENGER_HEALTH["turns_refused"] == 0


@pytest.mark.asyncio
async def test_a_streamed_answer_is_the_row_the_turn_stored(chat):
    async def streamed(req, add_message):
        await add_message(req.conversation_id, "ai", "Streamed words, stored whole.")
        return ChatResponse(conversation_id=req.conversation_id, message="", streaming=True, type="ai", model_used="x")

    chat.answer = streamed
    assert (await mc.handle(event()))["replies"] == [{"text": "Streamed words, stored whole."}]


@pytest.mark.asyncio
async def test_the_answers_buttons_ride_on_the_last_reply(chat):
    async def card(req, add_message):
        await add_message(req.conversation_id, "system", "Ana joined the chat")
        row = await add_message(req.conversation_id, "ai", "How would you like us to reach you?")
        return ChatResponse(conversation_id=req.conversation_id, message=row["content"], type="template",
                            model_used="template", buttons=[{"kind": "sms", "label": "Text me (SMS)"},
                                                            {"kind": "email", "label": "Email me"},
                                                            {"kind": "dance", "label": "No such kind"}],
                            quick_replies=["Business class", "  "])

    chat.answer = card
    out = (await mc.handle(event()))["replies"]
    assert [r["text"] for r in out] == ["Ana joined the chat", "How would you like us to reach you?"]
    assert "quick_replies" not in out[0]
    assert out[1]["quick_replies"] == [
        {"content_type": "text", "title": "Text me (SMS)", "payload": "sms"},
        {"content_type": "text", "title": "Email me", "payload": "email"},
        {"content_type": "text", "title": "Business class", "payload": "say"},
    ]


@pytest.mark.asyncio
async def test_a_button_tapped_is_the_turns_button_and_the_contact_is_one_tap(chat):
    async def ask(req, add_message):
        row = await add_message(req.conversation_id, "ai", "What's the best number to text you?")
        return ChatResponse(conversation_id=req.conversation_id, message=row["content"], type="template",
                            model_used="template")

    chat.answer = ask
    out = (await mc.handle(event(text="Text me (SMS)", quick_reply="sms")))["replies"]
    assert chat.requests[0][0].button == "sms"
    assert out[-1]["quick_replies"] == [{"content_type": "user_phone_number"}]
    # Rule 5: a quick reply that is no button (a suggestion, or the person's own number) is what they typed.
    await mc.handle(event(mid="m2", text="+1 214 555 0100", quick_reply="+1 214 555 0100"))
    assert chat.requests[1][0].button is None and chat.requests[1][0].message == "+1 214 555 0100"


@pytest.mark.asyncio
async def test_get_started_is_the_persons_first_words(chat):
    await mc.handle(event(kind="postback", mid="p1", text="", postback="GET_STARTED", postback_title="Get Started"))
    assert chat.requests[0][0].message == "Get Started"


@pytest.mark.asyncio
async def test_a_photo_is_kept_first_then_skye_asks_for_words(chat):
    out = await mc.handle(event(text="", attachments=["image"]))
    assert [(r["role"], r["content"]) for r in chat.rows] == [("user", "[photo]"), ("ai", mc.ATTACHMENT_REPLY)]
    assert out["replies"] == [{"text": mc.ATTACHMENT_REPLY}] and not chat.requests
    assert mc.MESSENGER_HEALTH["attachments"] == 1


@pytest.mark.asyncio
async def test_an_agents_chat_says_connecting_once_and_never_each_message_sent(chat):
    from app.api.chat import QUEUED_AGAIN, QUEUED_FIRST

    texts = iter([QUEUED_FIRST, QUEUED_FIRST, QUEUED_AGAIN])

    async def queued(req, add_message):
        return ChatResponse(conversation_id=req.conversation_id, message=next(texts), type="queued", model_used="none")

    chat.answer = queued
    assert (await mc.handle(event(mid="a")))["replies"] == [{"text": QUEUED_FIRST}]
    assert (await mc.handle(event(mid="b")))["replies"] == [], "once in half an hour"
    assert (await mc.handle(event(mid="c")))["replies"] == []


@pytest.mark.asyncio
async def test_a_stored_answer_goes_out_as_stored_never_beside_what_the_turn_returned(chat):
    """The answer the turn stored went through the output guard (db.add_message): it is what goes out, even when
    the turn returned other words (a cached answer read raw), and those are never sent beside it."""
    async def cached(req, add_message):
        await add_message(req.conversation_id, "ai", "Call our team any time.")
        return ChatResponse(conversation_id=req.conversation_id, message="Call our team any time, or 555-0100.",
                            type="ai", model_used="cache")

    chat.answer = cached
    assert (await mc.handle(event()))["replies"] == [{"text": "Call our team any time."}]


@pytest.mark.asyncio
async def test_an_answer_the_turn_could_not_store_goes_through_the_guard(chat):
    """Rule 5: no row for the answer (its save failed): it still goes out, guarded on the way."""
    async def unstored(req, add_message):
        return ChatResponse(conversation_id=req.conversation_id, message="Sure — which dates suit you?", type="ai",
                            model_used="claude")

    chat.answer = unstored
    out = (await mc.handle(event()))["replies"]
    assert out == [{"text": "Sure, which dates suit you?"}], "the guard's long dash rule ran on it"


@pytest.mark.asyncio
async def test_a_turn_the_chat_refuses_gets_no_reply(chat):
    async def blocked(req, add_message):
        raise HTTPException(status_code=403, detail="blocked")

    chat.answer = blocked
    assert (await mc.handle(event()))["replies"] == []
    assert mc.MESSENGER_HEALTH["turns_refused"] == 1


# ── kept without a reply ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_page_inbox_writes_and_skye_leaves_the_thread_to_it(chat):
    out = await mc.handle(event(kind="echo", mid="e1", text="Hi, Ana from the team here."))
    assert out == {"replies": []}
    assert [(r["role"], r["content"]) for r in chat.rows] == [("agent", "Hi, Ana from the team here.")]
    assert "messenger_inbox_at" in chat.patches[-1] and mc.MESSENGER_HEALTH["inbox_replies"] == 1
    # The person answers while the inbox has the thread: kept, Skye quiet.
    chat.conv["metadata"] = {"messenger_inbox_at": now_iso(minutes=-5)}
    chat.latest = {**chat.conv, "updated_at": now_iso()}
    with patch("app.db.supabase.latest_conversation_of_visitor", new=AsyncMock(return_value=chat.latest)):
        out = await mc.handle(event(mid="m5", text="Thanks Ana"))
        assert out == {"replies": []} and not chat.requests
        assert chat.rows[-1]["role"] == "user" and mc.MESSENGER_HEALTH["kept_unanswered"] == 1
        # Rule 5: half an hour later Skye answers again.
        chat.latest["metadata"] = {"messenger_inbox_at": now_iso(minutes=-31)}
        await mc.handle(event(mid="m6", text="Anyone there?"))
    assert len(chat.requests) == 1


@pytest.mark.asyncio
async def test_the_inbox_quiet_time_not_saved_fails_the_echo_and_its_row_is_not_stored_twice(chat):
    chat.patch_ok = False
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(kind="echo", mid="e1", text="Hi, Ana from the team here."))
    assert [(r["role"], r["content"]) for r in chat.rows] == [("agent", "Hi, Ana from the team here.")]
    # Delivered again: the quiet time is saved now, the echo kept once.
    chat.patch_ok = True
    assert (await mc.handle(event(kind="echo", mid="e1", text="Hi, Ana from the team here."))) == {"replies": []}
    assert len(chat.rows) == 1 and "messenger_inbox_at" in chat.patches[-1]
    assert mc.MESSENGER_HEALTH["saved_once"] == 1 and mc.MESSENGER_HEALTH["inbox_replies"] == 1


@pytest.mark.asyncio
async def test_a_message_heard_in_standby_is_kept_not_answered(chat):
    assert (await mc.handle(event(standby=True)))["replies"] == []
    assert chat.rows[-1]["role"] == "user" and not chat.requests


@pytest.mark.asyncio
async def test_a_message_kept_unanswered_that_was_not_saved_is_never_acknowledged(chat):
    chat.saves["user"] = ["fail"]
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(standby=True))
    assert chat.rows == [] and mc.MESSENGER_HEALTH["kept_unanswered"] == 0
    assert (await mc.handle(event(standby=True)))["replies"] == []
    assert [r["role"] for r in chat.rows] == ["user"] and mc.MESSENGER_HEALTH["kept_unanswered"] == 1


@pytest.mark.asyncio
async def test_a_photo_not_saved_gets_no_prompt_until_it_is(chat):
    chat.saves["user"] = ["fail"]
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(text="", attachments=["image"]))
    assert chat.rows == [], "no prompt for a photo that was not kept"
    out = await mc.handle(event(text="", attachments=["image"]))
    assert [(r["role"], r["content"]) for r in chat.rows] == [("user", "[photo]"), ("ai", mc.ATTACHMENT_REPLY)]
    assert out["replies"] == [{"text": mc.ATTACHMENT_REPLY}]


@pytest.mark.asyncio
async def test_a_save_whose_answer_was_lost_is_found_and_not_stored_twice(chat):
    """The insert reached the database, its answer did not (None back): the delivery made again looks before it
    saves, finds the row, and the prompt still goes out."""
    chat.saves["user"] = ["lost"]
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(text="", attachments=["image"]))
    out = await mc.handle(event(text="", attachments=["image"]))
    assert [(r["role"], r["content"]) for r in chat.rows] == [("user", "[photo]"), ("ai", mc.ATTACHMENT_REPLY)]
    assert out["replies"] == [{"text": mc.ATTACHMENT_REPLY}] and mc.MESSENGER_HEALTH["saved_once"] == 1


@pytest.mark.asyncio
async def test_two_photos_one_after_the_other_are_each_kept_once(chat):
    """Each row's id is its own event's: a second photo whose save failed is never taken for the first one (the same
    "[photo]" text), and one whose answer was lost is found by its own id (CodeRabbit #288)."""
    await mc.handle(event(mid="p1", text="", attachments=["image"]))
    chat.saves["user"] = ["fail"]
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="p2", text="", attachments=["image"]))
    out = await mc.handle(event(mid="p2", text="", attachments=["image"]))
    assert [(r["role"], r["content"]) for r in chat.rows] == [("user", "[photo]"), ("ai", mc.ATTACHMENT_REPLY)] * 2
    assert out["replies"] == [{"text": mc.ATTACHMENT_REPLY}] and mc.MESSENGER_HEALTH["saved_once"] == 0
    chat.saves["user"] = ["lost"]
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="p3", text="", attachments=["image"]))
    await mc.handle(event(mid="p3", text="", attachments=["image"]))
    assert [r["role"] for r in chat.rows] == ["user", "ai"] * 3 and mc.MESSENGER_HEALTH["saved_once"] == 1
    assert len({r["id"] for r in chat.rows}) == 6
    assert chat.rows[4]["id"] == mc.row_id(event(mid="p3"), "user") != mc.row_id(event(mid="p2"), "user")


@pytest.mark.asyncio
async def test_a_row_stored_before_that_cannot_be_read_back_is_answered_later_never_stored_twice(chat, redis):
    """The recovery's turn finds the photo stored by the failed delivery and cannot read it back: not known, so
    it waits (nothing said, nothing stored twice) and the next minute goes on."""
    chat.saves["user"] = ["lost", "unreadable"]
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(text="", attachments=["image"]))
    redis.kv[mc.INBOX]["m1"] -= mc.RECOVER_AFTER_S + 1
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        assert (await mc.run_messenger_recover())["waiting"] == 1
        assert [r["role"] for r in chat.rows] == ["user"] and gw.sent == []
        assert (await mc.run_messenger_recover())["answered"] == 1
    assert [r["role"] for r in chat.rows] == ["user", "ai"] and gw.sent == [mc.ATTACHMENT_REPLY]
    assert mc.MESSENGER_HEALTH["saved_once"] == 1


@pytest.mark.asyncio
async def test_without_redis_an_echo_whose_quiet_time_failed_is_not_stored_twice(monkeypatch):
    """No Redis to keep any mark: the echo's id alone keeps it once when the quiet time fails to save and Meta
    delivers the echo again."""
    redis_client.set_client_for_tests(None)
    monkeypatch.setattr(settings, "redis_url", None)
    c = Chat().start()
    try:
        c.patch_ok = False
        with pytest.raises(mc.EventFailed):
            await mc.handle(event(kind="echo", mid="e1", text="Hi, Ana from the team here."))
        c.patch_ok = True
        assert (await mc.handle(event(kind="echo", mid="e1", text="Hi, Ana from the team here."))) == {"replies": []}
    finally:
        c.stop()
    assert [(r["role"], r["content"]) for r in c.rows] == [("agent", "Hi, Ana from the team here.")]
    assert mc.MESSENGER_HEALTH["saved_once"] == 1 and mc.MESSENGER_HEALTH["no_dedupe"] == 2


@pytest.mark.asyncio
async def test_a_referral_before_the_first_message_reaches_the_chat(chat, redis):
    assert (await mc.handle(event(kind="referral", mid="", text="", referral={"ref": "promo", "source": "SHORTLINK"})))[
        "replies"] == []
    chat.created.assert_not_awaited()
    await mc.handle(event())
    meta = chat.created.await_args.kwargs["metadata"]
    assert meta["utm_campaign"] == "promo" and meta["utm_medium"] == "messenger"


@pytest.mark.asyncio
async def test_a_thread_that_cannot_be_read_is_delivered_again(chat):
    with patch("app.db.supabase.latest_conversation_of_visitor", new=AsyncMock(return_value=None)):
        with pytest.raises(mc.EventFailed):
            await mc.handle(event())
    chat.created.assert_not_awaited()


# ── out of a turn: an agent's message ─────────────────────────────


@pytest.mark.asyncio
async def test_an_agents_message_goes_out_through_the_gateway(redis, monkeypatch):
    import httpx

    calls: list = []

    def gateway(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"sent": 1})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(gateway), **kw))
    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    from app.services.conversation_service import add_message

    async def stored(c, r, t, m=None, x=0.0, *, message_id=None, on_stored=None, on_unknown=None):
        row = {"id": message_id or "r9", "role": r, "content": t}
        if on_stored is not None:
            on_stored(row)
        return row

    with patch("app.db.supabase.add_message", new=stored), \
            patch("app.realtime.manager.manager.push", new=AsyncMock()):
        await add_message(CONV, "agent", "Hi, I'm Ana. Which dates suit you?")
        await mc.drain(CONV)
    assert len(calls) == 1 and str(calls[0].url) == "https://gateway.example.test/v1/send"
    body = calls[0].content
    assert json.loads(body) == {"page_id": PAGE, "psid": PSID, "messages": [{"text": "Hi, I'm Ana. Which dates suit you?"}]}
    assert mc.valid_signed(calls[0].headers["x-gateway-time"], calls[0].headers["x-gateway-signature"], body)
    assert mc.MESSENGER_HEALTH["sent"] == 1


class Gateway:
    def __init__(self, answers):
        self.answers = list(answers)
        self.sent: list = []

    async def __call__(self, page_id, psid, messages, tag=None):
        self.sent.append(messages[0]["text"])
        return self.answers.pop(0) if self.answers else {"sent": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, note", [
    ({"sent": 0, "failed": {"kind": "window_closed"}}, mc.NOT_SENT["window_closed"]),
    ({"sent": 0, "failed": {"kind": "unavailable"}}, mc.NOT_SENT["unavailable"]),
    ({"sent": 0, "failed": {"kind": "gateway"}}, mc.NOT_SENT_OTHER),
    ({"sent": 0, "parts": 1, "parts_sent": 0, "failed": {"kind": "unknown"}}, mc.NOT_SENT["unknown"]),
    ({"sent": 0, "parts": 1, "parts_sent": 0, "failed": {"kind": "timeout"}}, mc.NOT_SENT_OTHER),
    # A long one cut short: its first pieces are with the client, so never "try again" with the whole of it.
    ({"sent": 0, "parts": 3, "parts_sent": 1, "failed": {"kind": "temporary"}},
     mc.PARTLY_SENT.format(sent=1, parts=3)),
    # The piece after them unconfirmed: it may be with the client too, so the agent asks before sending the rest.
    ({"sent": 0, "parts": 3, "parts_sent": 1, "failed": {"kind": "unknown"}},
     mc.PARTLY_UNCONFIRMED.format(sent=1, parts=3, next=2)),
])
async def test_an_agents_message_that_did_not_go_out_tells_the_agent(redis, answer, note):
    failed = answer["failed"]
    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    c = Chat().start()
    gw = Gateway([answer])
    try:
        with patch.object(mc, "send", new=gw):
            from app.services.conversation_service import add_message

            await add_message(CONV, "agent", "Are you still there?")
            await mc.drain(CONV)
            await mc.drain(CONV)
    finally:
        c.stop()
    # The note is the agent's alone: stored in the chat, never sent (one call: the agent's message).
    assert [(r["role"], r["content"]) for r in c.rows] == [("agent", "Are you still there?"), ("system", note)]
    assert gw.sent == ["Are you still there?"] and mc.MESSENGER_HEALTH["send_failed"] == 1
    if failed["kind"] in ("window_closed", "unavailable"):
        assert mc.MESSENGER_HEALTH[failed["kind"]] == 1
    assert mc.MESSENGER_HEALTH["unconfirmed"] == (1 if failed["kind"] == "unknown" else 0)
    assert mc.MESSENGER_HEALTH["partly_sent"] == (1 if answer.get("parts_sent") else 0)
    assert not redis.kv.get(mc.OUTBOX), "told: nothing left written down"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, kind", [
    # The gateway had the call, and what it did is not known: never "send it again".
    (lambda request: (_ for _ in ()).throw(__import__("httpx").ReadTimeout("no answer", request=request)), "unknown"),
    (lambda request: (_ for _ in ()).throw(__import__("httpx").RemoteProtocolError("gone", request=request)), "unknown"),
    (lambda request: __import__("httpx").Response(504, text="upstream timed out"), "unknown"),
    (lambda request: __import__("httpx").Response(200, text="not json"), "unknown"),
    # Any 5xx is a proxy's (the gateway never answers one): it may have come once the gateway had the call.
    (lambda request: __import__("httpx").Response(500, text="internal error"), "unknown"),
    (lambda request: __import__("httpx").Response(503, text="upstream connect error or disconnect/reset"), "unknown"),
    # A write that failed or ran out of time (CodeRabbit #288): what was handed to the socket before may have reached
    # the gateway whole (here it did), so it is never "not sent".
    (lambda request: (_ for _ in ()).throw(__import__("httpx").WriteError("broken pipe", request=request)), "unknown"),
    (lambda request: (_ for _ in ()).throw(__import__("httpx").WriteTimeout("timed out", request=request)), "unknown"),
    # Rule 5: it never had a connection to go on, or it was turned away before anything was sent: not sent.
    (lambda request: (_ for _ in ()).throw(__import__("httpx").ConnectError("refused", request=request)), "gateway"),
    (lambda request: (_ for _ in ()).throw(__import__("httpx").ConnectTimeout("no route", request=request)), "gateway"),
    (lambda request: (_ for _ in ()).throw(__import__("httpx").PoolTimeout("no connection", request=request)), "gateway"),
    (lambda request: __import__("httpx").Response(401, text="unauthorized"), "gateway"),
    (lambda request: __import__("httpx").Response(404, text="unknown page"), "gateway"),
    (lambda request: __import__("httpx").Response(308, headers={"location": "https://elsewhere.test"}), "gateway"),
])
async def test_the_gateways_silence_once_it_had_the_call_is_unknown(monkeypatch, answer, kind):
    import httpx

    had: list = []

    def gateway(request: httpx.Request):
        had.append(request.content)  # tried once; a write error here comes once the gateway had the call whole
        return answer(request)

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(gateway), **kw))
    out = await mc.send(PAGE, PSID, [{"text": "Hi"}])
    assert out == {"sent": 0, "failed": {"kind": kind}} and mc.MESSENGER_HEALTH["gateway_down"] == 1 and len(had) == 1


@pytest.mark.asyncio
async def test_a_chats_rows_go_out_in_order(redis):
    import asyncio

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    order: list = []

    async def slow_first(page_id, psid, messages, tag=None):
        if not order:
            await asyncio.sleep(0.05)
        order.append(messages[0]["text"])
        return {"sent": 1}

    c = Chat().start()
    try:
        with patch.object(mc, "send", new=slow_first):
            from app.services.conversation_service import add_message

            await add_message(CONV, "system", "Ana joined the chat")
            await add_message(CONV, "agent", "Hi, I'm Ana.")
            await mc.drain(CONV)
            await asyncio.sleep(0.01)
            await mc.drain(CONV)
    finally:
        c.stop()
    assert order == ["Ana joined the chat", "Hi, I'm Ana."]


@pytest.mark.asyncio
@pytest.mark.parametrize("held", ["push", "outbox"])
async def test_a_chats_rows_go_out_in_the_order_they_were_stored(redis, monkeypatch, held):
    """CodeRabbit #288: the agent's message stored, then its live push slow: a line saved meanwhile for the same chat
    goes out after it, and reaches the live stream after it (the push is part of the row's save, in the chat's order:
    the line's save waits for it). Rule 5: the agent's message held up in its outbox write is not stored yet, so a
    line stored meanwhile is the earlier one in the chat, and goes first, on both."""
    import asyncio

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    order: list = []

    async def gw(page_id, psid, messages, tag=None):
        order.append(messages[0]["text"])
        return {"sent": 1}

    gate, holding = asyncio.Event(), asyncio.Event()
    real_hset = redis.hset
    pushed: list = []

    async def push(conversation_id, row):
        if held == "push" and row["role"] == "agent":
            holding.set()
            await gate.wait()
        pushed.append(row["content"])

    async def outbox_held(*a, **kw):
        if held == "outbox" and not holding.is_set():
            holding.set()
            await gate.wait()
        return await real_hset(*a, **kw)

    c = Chat().start()
    try:
        with patch.object(mc, "send", new=gw), patch("app.realtime.manager.manager.push", new=push):
            from app.services.conversation_service import add_message

            monkeypatch.setattr(redis, "hset", outbox_held)
            agent = asyncio.create_task(add_message(CONV, "agent", "Hi, I'm Ana."))
            await asyncio.wait_for(holding.wait(), 1)
            line = asyncio.create_task(add_message(CONV, "system", "Ana shared the itinerary"))
            for _ in range(5):
                await asyncio.sleep(0)  # every chance to go on
            gate.set()
            await asyncio.wait_for(agent, 1)
            await asyncio.wait_for(line, 1)
            await mc.drain(CONV)
            await asyncio.sleep(0)
            await mc.drain(CONV)
    finally:
        c.stop()
    stored = [r["content"] for r in c.rows]
    assert order == stored, "out in the order stored"
    assert pushed == stored, "on the live stream in the order stored"
    assert stored == (["Hi, I'm Ana.", "Ana shared the itinerary"] if held == "push"
                      else ["Ana shared the itinerary", "Hi, I'm Ana."])


@pytest.mark.asyncio
async def test_a_rows_place_is_taken_as_the_database_stores_it_before_its_activity_clocks(redis):
    """CodeRabbit #288, the real db.add_message: the agent's message inserted, then the chat's activity clocks slow to
    update: it takes its place in the chat's order the moment it is stored, and a line for the same chat is stored
    after it (one at a time), so it goes out after it. Rule 5: a row the database refused takes no place and goes
    nowhere."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.db import supabase as db

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    stored: list[dict] = []
    refuse = {"on": False}

    def insert(payload):
        def execute():
            if refuse["on"]:
                raise RuntimeError("the database refused the insert")
            row = {"id": payload.get("id") or f"r{len(stored) + 1}", "created_at": now_iso(), **payload}
            stored.append(row)
            return SimpleNamespace(data=[row])

        return SimpleNamespace(execute=execute)

    client = MagicMock()
    client.table.return_value.insert.side_effect = insert

    async def run_sync(fn, **kw):
        return fn()

    clocks, go_on = asyncio.Event(), asyncio.Event()

    async def touch(conversation_id, role, created_at, *, catch_up=False):
        if role == "agent" and not go_on.is_set():
            clocks.set()
            await go_on.wait()

    order: list = []

    async def gw(page_id, psid, messages, tag=None):
        order.append(messages[0]["text"])
        return {"sent": 1}

    with patch.object(db, "get_client", return_value=client), patch.object(db, "_run_sync", new=run_sync), \
            patch.object(db, "_touch_conversation_activity", new=touch), patch.object(mc, "send", new=gw), \
            patch("app.realtime.manager.manager.push", new=AsyncMock()):
        from app.services.conversation_service import add_message

        agent = asyncio.create_task(add_message(CONV, "agent", "Hi, I'm Ana."))
        await asyncio.wait_for(clocks.wait(), 1)  # stored, its clocks being updated
        line = asyncio.create_task(add_message(CONV, "system", "Ana shared the itinerary"))
        for _ in range(5):
            await asyncio.sleep(0)
        assert not line.done(), "the chat's next row waits for the one being stored"
        go_on.set()
        assert (await agent)["content"] == "Hi, I'm Ana."
        await asyncio.wait_for(line, 1)
        refuse["on"] = True
        assert await add_message(CONV, "system", "Refused by the database") is None
        await mc.drain(CONV)
        await asyncio.sleep(0)
        await mc.drain(CONV)
    assert [r["content"] for r in stored] == ["Hi, I'm Ana.", "Ana shared the itinerary"]
    assert order == ["Hi, I'm Ana.", "Ana shared the itinerary"], "out in the order stored"
    assert not await redis.hgetall(mc.OUTBOX)


@pytest.mark.asyncio
async def test_a_chats_rows_are_stored_one_at_a_time_however_long_an_inserts_answer_takes(redis):
    """CodeRabbit #288, the real db.add_message: the agent's message stored, its insert's answer slow to come back,
    and a line for the same chat saved meanwhile: the line's insert waits for the agent's row (a chat's rows are
    stored and placed one at a time in this process), so it is stored after it and goes out after it. Rule 5:
    another chat's row never waits for this one's."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.db import supabase as db

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    stored: list[dict] = []

    def insert(payload):
        def execute():
            row = {"id": payload.get("id") or f"r{len(stored) + 1}", "created_at": now_iso(), **payload}
            stored.append(row)
            return SimpleNamespace(data=[row])

        return SimpleNamespace(execute=execute)

    client = MagicMock()
    client.table.return_value.insert.side_effect = insert
    landed, answer = asyncio.Event(), asyncio.Event()

    async def run_sync(fn, **kw):
        out = fn()  # the database has it
        if out.data[0]["role"] == "agent" and not answer.is_set():
            landed.set()
            await answer.wait()  # its answer on its way back
        return out

    async def touch(*a, **kw):
        return None

    order: list = []

    async def gw(page_id, psid, messages, tag=None):
        order.append(messages[0]["text"])
        return {"sent": 1}

    with patch.object(db, "get_client", return_value=client), patch.object(db, "_run_sync", new=run_sync), \
            patch.object(db, "_touch_conversation_activity", new=touch), patch.object(mc, "send", new=gw), \
            patch("app.realtime.manager.manager.push", new=AsyncMock()):
        from app.services.conversation_service import add_message

        agent = asyncio.create_task(add_message(CONV, "agent", "Hi, I'm Ana."))
        await asyncio.wait_for(landed.wait(), 1)
        line = asyncio.create_task(add_message(CONV, "system", "Ana shared the itinerary"))
        assert await asyncio.wait_for(add_message("another-chat", "system", "Another chat's line"), 1)
        for _ in range(5):
            await asyncio.sleep(0)
        assert not line.done() and [r["content"] for r in stored] == ["Hi, I'm Ana.", "Another chat's line"]
        answer.set()
        await asyncio.wait_for(agent, 1)
        await asyncio.wait_for(line, 1)
        await mc.drain(CONV)
        await asyncio.sleep(0)
        await mc.drain(CONV)
    assert [r["content"] for r in stored if r["conversation_id"] == CONV] == ["Hi, I'm Ana.", "Ana shared the itinerary"]
    assert order == ["Hi, I'm Ana.", "Ana shared the itinerary"], "out in the order stored"


def _order_watched(at_call: int = 2):
    """mc.in_order watched: `asked` is set as its at_call-th caller asks for its chat's order, before it waits for it
    (the boundary a test of a chat's order looks at, never a count of loop turns)."""
    import asyncio
    import contextlib

    real = mc.in_order
    asked = asyncio.Event()
    calls = {"n": 0}

    @contextlib.asynccontextmanager
    async def watched(conversation_id):
        calls["n"] += 1
        if calls["n"] == at_call:
            asked.set()
        async with real(conversation_id):
            yield

    return watched, asked


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["agent", "user"])
async def test_a_stored_row_whose_caller_was_cancelled_still_goes_out_before_the_next(redis, role):
    """CodeRabbit #288, the real db.add_message: the agent's request is cancelled while its insert is on its way, and
    the database stores the row all the same. The cancel waits for the insert's answer, so the row takes its place in
    the chat's order before the chat's next row is stored: it goes out first, and nothing is left to tell its agent.
    Rule 5: a row with no place to take (the person's own words) never waits on its cancel."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.db import supabase as db

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    stored: list[dict] = []

    def insert(payload):
        def execute():
            row = {"id": payload.get("id") or f"r{len(stored) + 1}", "created_at": now_iso(), **payload}
            stored.append(row)
            return SimpleNamespace(data=[row])

        return SimpleNamespace(execute=execute)

    client = MagicMock()
    client.table.return_value.insert.side_effect = insert
    landed, answer = asyncio.Event(), asyncio.Event()

    async def run_sync(fn, **kw):
        out = fn()  # the database has it
        if out.data[0]["role"] == role and not answer.is_set():
            landed.set()
            await answer.wait()  # its answer on its way back
        return out

    async def touch(*a, **kw):
        return None

    order: list = []

    async def gw(page_id, psid, messages, tag=None):
        order.append(messages[0]["text"])
        return {"sent": 1}

    text = "Hi, I'm Ana." if role == "agent" else "Thanks!"
    watched, asked = _order_watched()
    with patch.object(db, "get_client", return_value=client), patch.object(db, "_run_sync", new=run_sync), \
            patch.object(db, "_touch_conversation_activity", new=touch), patch.object(mc, "send", new=gw), \
            patch("app.realtime.manager.manager.push", new=AsyncMock()), \
            patch.object(mc, "in_order", new=watched):
        from app.services.conversation_service import add_message

        first = asyncio.create_task(add_message(CONV, role, text))
        await asyncio.wait_for(landed.wait(), 1)
        first.cancel()  # the request goes, its row stored
        if role == "user":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(first, 1)  # at once: nothing to place
            answer.set()
            return
        line = asyncio.create_task(add_message(CONV, "system", "Ana shared the itinerary"))
        await asyncio.wait_for(asked.wait(), 1)  # the next row asks for the chat's order
        for _ in range(5):
            await asyncio.sleep(0)  # every chance to go on
        assert [r["role"] for r in stored] == ["agent"] and not line.done(), \
            "the next row waits for the cancelled one's answer, not inserted"
        answer.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(line, 1)
        await mc.drain(CONV)
        await asyncio.sleep(0)
        await mc.drain(CONV)
    assert [r["content"] for r in stored] == ["Hi, I'm Ana.", "Ana shared the itinerary"]
    assert order == ["Hi, I'm Ana.", "Ana shared the itinerary"], "out first, as stored"
    assert not await redis.hgetall(mc.OUTBOX), "sent: nothing left to tell its agent"


@pytest.mark.asyncio
async def test_a_cancelled_agents_message_stored_as_the_channel_went_off_is_crossed_out_by_its_save(
        redis, monkeypatch):
    """CodeRabbit #288, the real db.add_message: the agent's request is cancelled while its insert is on its way (it
    stops waiting at once), the channel is turned off meanwhile, and the insert stores the row. Its save goes on, in
    the chat's order: its place has nothing to send and crosses its written-down delivery out, never left unawaited
    (no note for a message that was never to go). Rule 5: not cancelled, the same is done once the insert answers
    (test_a_stored_row_whose_caller_was_cancelled_still_goes_out_before_the_next, its user row)."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.db import supabase as db

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    stored: list[dict] = []

    def insert_row(payload):
        def execute():
            row = {"id": payload.get("id") or f"r{len(stored) + 1}", "created_at": now_iso(), **payload}
            stored.append(row)
            return SimpleNamespace(data=[row])

        return SimpleNamespace(execute=execute)

    client = MagicMock()
    client.table.return_value.insert.side_effect = insert_row
    landed, answer = asyncio.Event(), asyncio.Event()

    async def run_sync(fn, **kw):
        out = fn()
        if out.data[0]["role"] == "agent" and not answer.is_set():
            landed.set()
            await answer.wait()  # its answer on its way back
        return out

    async def touch(*a, **kw):
        return None

    gw = Gateway([])
    with patch.object(db, "get_client", return_value=client), patch.object(db, "_run_sync", new=run_sync), \
            patch.object(db, "_touch_conversation_activity", new=touch), patch.object(mc, "send", new=gw), \
            patch("app.realtime.manager.manager.push", new=AsyncMock()):
        from app.services.conversation_service import add_message

        first = asyncio.create_task(add_message(CONV, "agent", "Hi, I'm Ana."))
        await asyncio.wait_for(landed.wait(), 1)
        assert len(await redis.hgetall(mc.OUTBOX)) == 1, "written down before it was stored"
        first.cancel()  # the request goes, its insert on its way
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, 1)  # at once: it only stops waiting for its save
        monkeypatch.setattr(settings, "messenger_gateway_url", "")  # the channel off meanwhile
        answer.set()
        await asyncio.wait_for(mc.saved(), 1)
        assert not await redis.hgetall(mc.OUTBOX), "crossed out by its save"
    assert [r["content"] for r in stored] == ["Hi, I'm Ana."] and gw.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("insert", ["stored_answer_lost", "refused", "retry_answer_lost"])
async def test_a_cancelled_agents_message_is_saved_as_if_its_request_had_not_been_cancelled(redis, insert):
    """CodeRabbit #288, the real db.add_message: the agent's request is cancelled while an insert of its message is on
    its way, and that insert's answer comes back an error, late or not. The request only stops waiting, at once: its
    save goes on, holding the chat's order, as a request not cancelled would. Asked again under its id, a row stored
    with its answer lost comes back (stored before) and goes out, first; one refused twice, or a retry stored with its
    answer lost (the request cancelled during the retry), is marked unsure before the chat's next row may go, so the
    recovery minute looks its row up while this process lives: stored, its agent is told it was not sent, once; not
    stored, crossed out without a word. Rule 5: an insert that answers with its row is placed at once
    (test_a_stored_row_whose_caller_was_cancelled_still_goes_out_before_the_next)."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from postgrest.exceptions import APIError
    from redis.exceptions import ConnectionError as RedisConnectionError

    from app.db import supabase as db

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    stored: list[dict] = []
    inserts = {"n": 0}
    agent = {"n": 0}

    def insert_row(payload):
        def execute():
            inserts["n"] += 1
            row = {"id": payload.get("id") or f"r{len(stored) + 1}", "created_at": now_iso(), **payload}
            if row["role"] != "agent":
                stored.append(row)
                return SimpleNamespace(data=[row])
            agent["n"] += 1
            if any(r["id"] == row["id"] for r in stored):
                raise APIError({"code": "23505", "message": "duplicate key"})
            if (insert, agent["n"]) in (("stored_answer_lost", 1), ("retry_answer_lost", 2)):
                stored.append(row)  # stored, its answer lost below
            return SimpleNamespace(data=[row])

        return SimpleNamespace(execute=execute)

    client = MagicMock()
    client.table.return_value.insert.side_effect = insert_row
    landed, answer = asyncio.Event(), asyncio.Event()

    async def run_sync(fn, **kw):
        out = fn()
        if out.data[0]["role"] != "agent":
            return out
        if agent["n"] == (2 if insert == "retry_answer_lost" else 1) and not answer.is_set():
            landed.set()
            await answer.wait()  # its answer on its way back, later than any bound
        raise RedisConnectionError("the connection dropped")  # an error, whatever the database did

    async def touch(*a, **kw):
        return None

    async def get_message(row_id):
        return next((r for r in stored if r["id"] == row_id), None)

    order: list = []

    async def gw(page_id, psid, messages, tag=None):
        order.append(messages[0]["text"])
        return {"sent": 1}

    watched, asked = _order_watched()
    with patch.object(db, "get_client", return_value=client), patch.object(db, "_run_sync", new=run_sync), \
            patch.object(db, "_touch_conversation_activity", new=touch), patch.object(db, "get_message", new=get_message), \
            patch.object(mc, "send", new=gw), patch("app.realtime.manager.manager.push", new=AsyncMock()), \
            patch.object(mc, "in_order", new=watched):
        from app.services.conversation_service import add_message

        first = asyncio.create_task(add_message(CONV, "agent", "Hi, I'm Ana."))
        await asyncio.wait_for(landed.wait(), 1)
        first.cancel()  # the request goes, an insert of its message on its way
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, 1)  # at once: it only stops waiting for its save
        line = asyncio.create_task(add_message(CONV, "system", "Ana shared the itinerary"))
        await asyncio.wait_for(asked.wait(), 1)  # the next row asks for the chat's order
        for _ in range(5):
            await asyncio.sleep(0)  # every chance to go on
        assert not [r for r in stored if r["role"] == "system"] and not line.done(), \
            "the next row waits for the cancelled one's save, not inserted"
        answer.set()
        await asyncio.wait_for(line, 1)
        left = [json.loads(v)["state"] for v in (await redis.hgetall(mc.OUTBOX)).values()]
        await mc.drain(CONV)
        await asyncio.sleep(0)
        await mc.drain(CONV)
        out = await mc.run_messenger_recover()  # this process alive: unsure is looked up all the same
    notes = [r["content"] for r in stored if r["role"] == "system" and r["content"] == mc.NOT_SENT_OTHER]
    if insert == "stored_answer_lost":
        assert order == ["Hi, I'm Ana.", "Ana shared the itinerary"], "found stored, it goes out first"
        assert notes == [] and out["told"] == 0
    else:
        assert left == ["unsure"], "left to the recovery minute before the next row went"
        assert order == ["Ana shared the itinerary"], "the agent's message never sent"
        assert notes == ([mc.NOT_SENT_OTHER] if insert == "retry_answer_lost" else []) and out["told"] == len(notes)
    assert not await redis.hgetall(mc.OUTBOX)
    assert agent["n"] == 2 and inserts["n"] == 3 + len(notes), "asked again once, under its id"


@pytest.mark.asyncio
@pytest.mark.parametrize("request_then", ["cancelled", "waited"])
async def test_an_agents_message_reaches_the_live_stream_once_in_the_chats_order_whatever_its_request(redis,
                                                                                                    request_then):
    """CodeRabbit #288, the real db.add_message: an agent's message (their name riding the live copy) whose request is
    cancelled while its insert is on its way, the row stored all the same. Its save goes on and publishes the row to
    the live stream itself, once, with the agent's name, before the chat's next row is published. Rule 5: a request
    that waits, the same: once each, in order, never a second copy."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.db import supabase as db

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    stored: list[dict] = []

    def insert_row(payload):
        def execute():
            row = {"id": payload.get("id") or f"r{len(stored) + 1}", "created_at": now_iso(), **payload}
            stored.append(row)
            return SimpleNamespace(data=[row])

        return SimpleNamespace(execute=execute)

    client = MagicMock()
    client.table.return_value.insert.side_effect = insert_row
    landed, answer = asyncio.Event(), asyncio.Event()

    async def run_sync(fn, **kw):
        out = fn()  # the database has it
        if out.data[0]["role"] == "agent" and not answer.is_set():
            landed.set()
            await answer.wait()  # its answer on its way back
        return out

    async def touch(*a, **kw):
        return None

    pushed: list = []

    async def push(conversation_id, row):
        pushed.append((row["content"], row.get("agent_name")))

    gw = Gateway([])
    with patch.object(db, "get_client", return_value=client), patch.object(db, "_run_sync", new=run_sync), \
            patch.object(db, "_touch_conversation_activity", new=touch), patch.object(mc, "send", new=gw), \
            patch("app.realtime.manager.manager.push", new=push):
        from app.services.conversation_service import add_message

        first = asyncio.create_task(add_message(CONV, "agent", "Hi, I'm Ana.", live_extra={"agent_name": "Ana"}))
        await asyncio.wait_for(landed.wait(), 1)
        if request_then == "cancelled":
            first.cancel()  # the request goes, its row's insert on its way
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(first, 1)  # at once
        line = asyncio.create_task(add_message(CONV, "system", "Ana shared the itinerary"))
        for _ in range(5):
            await asyncio.sleep(0)
        assert pushed == [] and not line.done(), "nothing published before the agent's row is stored"
        answer.set()
        if request_then == "waited":
            assert (await asyncio.wait_for(first, 1))["content"] == "Hi, I'm Ana."
        await asyncio.wait_for(line, 1)
        await asyncio.wait_for(mc.saved(), 1)
        await mc.drain(CONV)
        await asyncio.sleep(0)
        await mc.drain(CONV)
    assert pushed == [("Hi, I'm Ana.", "Ana"), ("Ana shared the itinerary", None)], "once each, in the chat's order"
    assert gw.sent == ["Hi, I'm Ana.", "Ana shared the itinerary"]


@pytest.mark.asyncio
@pytest.mark.parametrize("then", ["the_turn_stopped", "its_part_timed_out", "in_time"])
async def test_a_turn_cancelled_while_its_rows_insert_is_on_its_way_goes_on_at_once_the_chats_order_kept(redis,
                                                                                                         then):
    """CodeRabbit #288, the real db.add_message: a Messenger turn is cancelled while its reply's insert is on its way,
    and the insert answers late (the row stored all the same). The turn only stops waiting for the row's save, at
    once: the save goes on by itself, holding the chat's order, as a request's does. The whole turn stopped (its
    delivery cancelled): the agent's message stored next waits for that save, the row joins no answer and is never
    sent on its own (turn_rows_left), and the event is left to be answered again. The turn's own part stopped by its
    timeout: the turn goes on, and its fallback, stored next, waits for that save, so the answer says the reply, then
    the fallback, in the order stored. Rule 5: an insert that answers in time, its row in the turn's answer."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.db import supabase as db

    c = Chat().start()
    real_add = c._ps.pop(0)  # the real db.add_message from here
    real_add.stop()
    stored: list[dict] = []

    def insert_row(payload):
        def execute():
            row = {"id": payload.get("id") or f"r{len(stored) + 1}", "created_at": now_iso(), **payload}
            stored.append(row)
            return SimpleNamespace(data=[row])

        return SimpleNamespace(execute=execute)

    client = MagicMock()
    client.table.return_value.insert.side_effect = insert_row
    landed, answer = asyncio.Event(), asyncio.Event()
    if then == "in_time":
        answer.set()

    async def run_sync(fn, **kw):
        out = fn()  # the database has it
        if out.data[0]["content"] == "Here now." and not answer.is_set():
            landed.set()
            await answer.wait()  # its answer on its way back, late
        return out

    async def touch(*a, **kw):
        return None

    async def turn(req, add_message):
        await add_message(req.conversation_id, "user", req.message)
        if then != "its_part_timed_out":
            await add_message(req.conversation_id, "ai", "Here now.")
            return ChatResponse(conversation_id=req.conversation_id, message="Here now.", type="ai", model_used="x")
        try:
            await asyncio.wait_for(add_message(req.conversation_id, "ai", "Here now."), 0.05)  # its own timeout
        except asyncio.TimeoutError:
            await add_message(req.conversation_id, "ai", "One moment, please.")  # its fallback, stored after
        return ChatResponse(conversation_id=req.conversation_id, message="One moment, please.",
                            type="template_fallback", model_used="template")

    c.answer = turn
    gw = Gateway([])
    watched, asked = _order_watched()
    try:
        with patch.object(db, "get_client", return_value=client), patch.object(db, "_run_sync", new=run_sync), \
                patch.object(db, "_touch_conversation_activity", new=touch), patch.object(mc, "send", new=gw), \
                patch.object(mc, "in_order", new=watched):
            from app.services.conversation_service import add_message

            delivery = asyncio.create_task(mc.handle(event()))
            if then == "in_time":
                out = await asyncio.wait_for(delivery, 1)
                assert [r["text"] for r in out["replies"]] == ["Here now."]
                return
            await asyncio.wait_for(landed.wait(), 1)
            if then == "the_turn_stopped":
                delivery.cancel()  # the gateway's call goes, the reply's insert on its way
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(delivery, 0.5)  # at once: it only stops waiting for the save
                agent = asyncio.create_task(add_message(CONV, "agent", "Hi, I'm Ana."))
                await asyncio.wait_for(asked.wait(), 1)  # the next row asks for the chat's order
                for _ in range(5):
                    await asyncio.sleep(0)  # every chance to go on
                assert [r["role"] for r in stored] == ["user", "ai"] and not agent.done(), \
                    "the next row waits for the cancelled turn's save, not inserted"
                answer.set()
                await asyncio.wait_for(agent, 1)
                await asyncio.wait_for(mc.saved(), 1)
                await mc.drain(CONV)
                assert gw.sent == ["Hi, I'm Ana."], "the turn's row never sent on its own"
                assert mc.MESSENGER_HEALTH["turn_rows_left"] == 1
                assert str(redis.kv["msgr:event:m1"]).startswith("pending:"), "left to be answered again"
                return
            await asyncio.wait_for(asked.wait(), 1)  # the turn went on: its fallback asks for the chat's order
            assert not delivery.done() and [r["role"] for r in stored] == ["user", "ai"]
            answer.set()
            out = await asyncio.wait_for(delivery, 1)
    finally:
        c.stop()
    assert [r["text"] for r in out["replies"]] == ["Here now.", "One moment, please."], "in the order stored"
    assert [r["content"] for r in stored][1:] == ["Here now.", "One moment, please."]
    assert mc.MESSENGER_HEALTH["turn_rows_left"] == 0 and gw.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("between", ["unread", "nowhere", "found"])
async def test_a_row_with_nothing_to_send_keeps_the_next_one_behind_the_one_before(redis, monkeypatch, between):
    """CodeRabbit #288: the agent's first message is with the gateway when their second one's chat cannot be read
    (kept for the minute, unsure), or reads as going nowhere: the second ends at once, yet the third, stored after it,
    still waits for the first (a row ends only after the chat's previous one), so it never reaches the person before
    it. Rule 5: the second found going to the chat goes out between them, in the order stored."""
    import asyncio

    target = {"page_id": PAGE, "psid": PSID}
    reads = {"n": 0}

    async def where(conversation_id, role):
        reads["n"] += 1
        if reads["n"] == 2 and between == "unread":
            raise mc.TargetUnread(conversation_id)
        if reads["n"] == 2 and between == "nowhere":
            return None
        return target

    sent: list = []
    first_out, go_on = asyncio.Event(), asyncio.Event()

    async def gw(page_id, psid, messages, tag=None):
        sent.append(messages[0]["text"])
        if messages[0]["text"] == "First":
            first_out.set()
            await go_on.wait()
        return {"sent": 1}

    monkeypatch.setattr(mc, "_target", where)
    c = Chat().start()
    try:
        with patch.object(mc, "send", new=gw):
            from app.services.conversation_service import add_message

            await add_message(CONV, "agent", "First")
            await asyncio.wait_for(first_out.wait(), 1)
            await add_message(CONV, "agent", "Second")
            for _ in range(10):
                await asyncio.sleep(0)  # the second looked up, its entry written: done with, or waiting
            await add_message(CONV, "agent", "Third")
            for _ in range(10):
                await asyncio.sleep(0)
            assert sent == ["First"], "nothing passes the first while it is with the gateway"
            go_on.set()
            await mc.drain(CONV)
            await asyncio.sleep(0)
            await mc.drain(CONV)
    finally:
        c.stop()
    assert sent == (["First", "Second", "Third"] if between == "found" else ["First", "Third"])
    left = [json.loads(v)["state"] for v in (await redis.hgetall(mc.OUTBOX)).values()]
    assert left == (["unsure"] if between == "unread" else []), "the unread one is kept for the minute"


@pytest.mark.asyncio
async def test_an_agents_message_finds_its_thread_when_redis_forgot_it(redis):
    conv = {"id": CONV, "metadata": {"chat_channel": "messenger", "messenger": {"page_id": PAGE, "psid": PSID}}}
    gw = Gateway([])
    c = Chat().start()
    try:
        with patch.object(mc, "send", new=gw), \
                patch("app.db.supabase.get_conversation_metadata", new=AsyncMock(return_value=conv)):
            from app.services.conversation_service import add_message

            await add_message(CONV, "agent", "Following up on your trip.")
            await mc.drain(CONV)
    finally:
        c.stop()
    assert gw.sent == ["Following up on your trip."]
    assert json.loads(redis.kv[f"msgr:conv:{CONV}"]) == {"page_id": PAGE, "psid": PSID}


@pytest.mark.asyncio
async def test_an_agents_message_to_a_chat_that_could_not_be_read_is_kept_for_the_minute(redis):
    """CodeRabbit #288: Redis no longer knows where the chat answers to, and the chat's read fails: where the agent's
    message goes is not known, so it is never crossed out. Kept (unsure) for the recovery minute, which looks the chat
    up again while this process lives, and tells the agent it was not sent, once. Rule 5: a chat read and found not
    on Messenger is crossed out at once, without a word (test_a_widget_chat_sends_nothing_anywhere)."""
    conv = {"id": CONV, "metadata": {"chat_channel": "messenger", "messenger": {"page_id": PAGE, "psid": PSID}}}
    reads = {"fail": True}

    async def read(cid):
        if reads["fail"]:
            raise RuntimeError("the database did not answer")
        return conv

    gw = Gateway([])
    c = Chat().start()
    try:
        with patch.object(mc, "send", new=gw), patch("app.db.supabase.get_conversation_metadata", new=read):
            from app.services.conversation_service import add_message

            await add_message(CONV, "agent", "Following up on your trip.")
            await mc.drain(CONV)
            (left,) = [json.loads(v) for v in (await redis.hgetall(mc.OUTBOX)).values()]
            assert left["state"] == "unsure" and gw.sent == [] and mc.MESSENGER_HEALTH["target_unread"] == 1
            assert (await mc.run_messenger_recover())["told"] == 0, "the chat still not read: looked at again"
            reads["fail"] = False
            out = await mc.run_messenger_recover()
            again = await mc.run_messenger_recover()
    finally:
        c.stop()
    notes = [r["content"] for r in c.rows if r["role"] == "system"]
    assert out["told"] == 1 and again["told"] == 0 and notes == [mc.NOT_SENT_OTHER] and gw.sent == []
    assert not await redis.hgetall(mc.OUTBOX)


@pytest.mark.asyncio
async def test_a_shutdown_leaves_a_message_whose_chat_could_not_be_read_to_the_next_process(redis, monkeypatch):
    """The same at a shutdown: an agent's message saved once it began, its chat not read: nothing is said now and
    nothing crossed out; the next process's minute looks the chat up and tells the agent, once."""
    conv = {"id": CONV, "metadata": {"chat_channel": "messenger", "messenger": {"page_id": PAGE, "psid": PSID}}}
    reads = {"fail": True}

    async def read(cid):
        if reads["fail"]:
            raise RuntimeError("the database did not answer")
        return conv

    gw = Gateway([])
    c = Chat().start()
    try:
        with patch.object(mc, "send", new=gw), patch("app.db.supabase.get_conversation_metadata", new=read):
            assert (await mc.drain_all(0.05)) == {"left": 0}
            from app.services.conversation_service import add_message

            await add_message(CONV, "agent", "Saved while the process goes")
            (left,) = [json.loads(v) for v in (await redis.hgetall(mc.OUTBOX)).values()]
            assert left["state"] == "waiting" and not [r for r in c.rows if r["role"] == "system"]
            reads["fail"] = False
            stopped = mc.BOOT
            monkeypatch.setattr(mc, "BOOT", "the-next-process")
            monkeypatch.setattr(mc, "_CLOSING", False)
            redis.kv.pop(f"msgr:alive:{stopped}")
            out = await mc.run_messenger_recover()
    finally:
        c.stop()
    notes = [r["content"] for r in c.rows if r["role"] == "system"]
    assert out["told"] == 1 and notes == [mc.NOT_SENT_RESTART] and gw.sent == []
    assert not await redis.hgetall(mc.OUTBOX)


@pytest.mark.asyncio
async def test_a_widget_chat_sends_nothing_anywhere(redis, monkeypatch):
    """Rule 5: a chat that is not on Messenger: nothing goes out; with the channel off, nothing is even looked up."""
    gw = Gateway([])
    c = Chat().start()
    try:
        with patch.object(mc, "send", new=gw), \
                patch("app.db.supabase.get_conversation_metadata",
                      new=AsyncMock(return_value={"id": CONV, "metadata": {"site": "bbc"}})) as read:
            from app.services.conversation_service import add_message

            await add_message(CONV, "agent", "Hi from the panel")
            await add_message(CONV, "ai", "Skye on the site")
            await mc.drain(CONV)
            assert read.await_count == 1, "only the agent's message looks the chat up"
            monkeypatch.setenv("MESSENGER_CHANNEL", "false")
            await add_message(CONV, "agent", "Again")
            await mc.drain(CONV)
            assert read.await_count == 1
    finally:
        c.stop()
    assert gw.sent == [] and mc.MESSENGER_HEALTH["sent"] == 0
    assert not redis.kv.get(mc.OUTBOX), "the agent's message, written down first, crossed out once looked up"


@pytest.mark.asyncio
async def test_a_note_for_the_agent_and_an_inbox_echo_never_go_out(redis):
    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    gw = Gateway([])
    c = Chat().start()
    try:
        with patch.object(mc, "send", new=gw):
            from app.services.conversation_service import add_message

            await add_message(CONV, "system", "Not sent on Messenger.", outbound=False)
            await add_message(CONV, "agent", "Written in the inbox", outbound=False)
            await add_message(CONV, "user", "The person's own words")
            await mc.drain(CONV)
    finally:
        c.stop()
    assert gw.sent == []


@pytest.mark.asyncio
async def test_a_row_waiting_behind_another_is_not_sent_once_the_channel_is_off(redis, monkeypatch):
    import asyncio

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    sent: list = []
    go = asyncio.Event()

    async def slow(page_id, psid, messages, tag=None):
        if not sent:
            await go.wait()
        sent.append(messages[0]["text"])
        return {"sent": 1}

    c = Chat().start()
    try:
        with patch.object(mc, "send", new=slow):
            from app.services.conversation_service import add_message

            await add_message(CONV, "agent", "First")
            await add_message(CONV, "agent", "Second, queued behind the first")
            await asyncio.sleep(0.01)
            monkeypatch.setenv("MESSENGER_CHANNEL", "false")
            go.set()
            await mc.drain(CONV)
    finally:
        c.stop()
    # The first was with the gateway already; the second had not started: it stays in the panel only.
    assert sent == ["First"] and mc.MESSENGER_HEALTH["off_skipped"] == 1


@pytest.mark.asyncio
async def test_a_shutdown_waits_a_little_then_tells_each_agent_what_did_not_go_out(redis):
    import asyncio

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})

    async def stuck(page_id, psid, messages, tag=None):
        await asyncio.sleep(30)
        return {"sent": 1}

    c = Chat().start()
    try:
        with patch.object(mc, "send", new=stuck):
            from app.services.conversation_service import add_message

            await add_message(CONV, "agent", "With the gateway when the server stops")
            await add_message(CONV, "agent", "Queued behind it")
            await asyncio.sleep(0.01)
            assert (await mc.drain_all(0.05)) == {"left": 1}
            # After the shutdown began nothing starts: the agent is told at once.
            await add_message(CONV, "agent", "Saved while the process goes")
            # Rule 5: an agent on a site chat is told nothing about Messenger.
            with patch("app.db.supabase.get_conversation_metadata",
                       new=AsyncMock(return_value={"id": "site-chat", "metadata": {"site": "bbc"}})):
                await add_message("site-chat", "agent", "On the site")
    finally:
        c.stop()
    assert not [r for r in c.rows if r["conversation_id"] == "site-chat" and r["role"] == "system"]
    notes = [r["content"] for r in c.rows if r["role"] == "system"]
    # The first may have reached the client (it was with the gateway); the others did not.
    assert sorted(notes) == sorted([mc.NOT_SENT["unknown"], mc.NOT_SENT_RESTART, mc.NOT_SENT_RESTART])
    assert mc.MESSENGER_HEALTH["cut_at_shutdown"] == 3 and not mc._LAST
    assert not redis.kv.get(mc.OUTBOX), "each agent told: nothing left for the next process"


@pytest.mark.asyncio
async def test_a_note_the_shutdown_could_not_wait_for_is_written_by_the_next_process(redis, monkeypatch):
    """The database too slow for the shutdown's notes (CodeRabbit #288): the process goes with the agent's message
    still written down in Redis, and the next process's recovery minute tells the agent, once."""
    import asyncio

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})

    async def stuck(page_id, psid, messages, tag=None):
        await asyncio.sleep(30)
        return {"sent": 1}

    c = Chat().start()
    real = c.add_message

    async def notes_hang(conversation_id, role, content, model_used=None, cost=0.0, *, message_id=None,
                         on_stored=None, on_unknown=None):
        if role == "system":
            await asyncio.sleep(30)
        return await real(conversation_id, role, content, model_used, cost, message_id=message_id, on_stored=on_stored)

    monkeypatch.setattr(mc, "NOTES_S", 0.05)
    try:
        with patch("app.db.supabase.add_message", new=notes_hang), patch.object(mc, "send", new=stuck):
            from app.services.conversation_service import add_message

            await add_message(CONV, "agent", "With the gateway when the server stops")
            await asyncio.sleep(0.01)
            # Rule 5: a delivery this process is on is left to it.
            assert (await mc.run_messenger_recover())["told"] == 0
            assert (await mc.drain_all(0.05)) == {"left": 1}
            # The process goes, and what it was still doing with it.
            for t in list(mc._LAST.values()):
                t.cancel()
            await asyncio.sleep(0)
        assert not [r for r in c.rows if r["role"] == "system"] and len((await redis.hgetall(mc.OUTBOX))) == 1
        stopped = mc.BOOT
        monkeypatch.setattr(mc, "BOOT", "the-next-process")
        monkeypatch.setattr(mc, "_CLOSING", False)
        # Its alive mark not gone yet: it may still be finishing (a deploy's overlap).
        assert (await mc.run_messenger_recover())["told"] == 0
        redis.kv.pop(f"msgr:alive:{stopped}")
        out = await mc.run_messenger_recover()
        again = await mc.run_messenger_recover()
    finally:
        c.stop()
    notes = [r["content"] for r in c.rows if r["role"] == "system"]
    # It was with the gateway: maybe out, said so once.
    assert out["told"] == 1 and out["state"] == "ran" and again["told"] == 0 and notes == [mc.NOT_SENT["unknown"]]
    assert not (await redis.hgetall(mc.OUTBOX)) and mc.MESSENGER_HEALTH["told_later"] == 1


@pytest.mark.asyncio
async def test_a_stop_while_an_agents_message_is_written_down_leaves_it_to_the_next_process(redis, monkeypatch):
    """CodeRabbit #288: the outbox's write held up when the shutdown begins, then the note to the agent too slow:
    the message is written down before anything a stop can cut, nothing goes out, and the next process's minute
    tells the agent, once."""
    import asyncio

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    gate, writing, noting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    real_hset = redis.hset

    async def held_up(*a, **kw):
        writing.set()
        await gate.wait()
        return await real_hset(*a, **kw)

    c = Chat().start()
    real = c.add_message

    async def notes_hang(conversation_id, role, content, model_used=None, cost=0.0, *, message_id=None,
                         on_stored=None, on_unknown=None):
        if role == "system":
            noting.set()
            await asyncio.sleep(30)
        return await real(conversation_id, role, content, model_used, cost, message_id=message_id, on_stored=on_stored)

    gw = Gateway([])
    try:
        with patch("app.db.supabase.add_message", new=notes_hang), patch.object(mc, "send", new=gw):
            from app.services.conversation_service import add_message

            monkeypatch.setattr(redis, "hset", held_up)
            saving = asyncio.create_task(add_message(CONV, "agent", "Written while the server stops"))
            await asyncio.wait_for(writing.wait(), 1)  # its outbox write on its way when the stop begins
            assert (await mc.drain_all(0.05)) == {"left": 0}, "nothing was handed on yet"
            gate.set()
            await asyncio.wait_for(noting.wait(), 1)  # its note to the agent begun, and hanging
            # Written down; nothing started after (the stop had begun), and the agent's note hangs: the process goes.
            assert len((await redis.hgetall(mc.OUTBOX))) == 1 and gw.sent == [] and not saving.done()
            saving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await saving
            monkeypatch.setattr(redis, "hset", real_hset)
        assert not [r for r in c.rows if r["role"] == "system"]
        stopped = mc.BOOT
        monkeypatch.setattr(mc, "BOOT", "the-next-process")
        monkeypatch.setattr(mc, "_CLOSING", False)
        redis.kv.pop(f"msgr:alive:{stopped}")
        out = await mc.run_messenger_recover()
        again = await mc.run_messenger_recover()
    finally:
        c.stop()
    notes = [r["content"] for r in c.rows if r["role"] == "system"]
    assert out["told"] == 1 and again["told"] == 0 and notes == [mc.NOT_SENT_RESTART] and gw.sent == []
    assert not (await redis.hgetall(mc.OUTBOX)) and mc.MESSENGER_HEALTH["cut_at_shutdown"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("lands", [True, False])
async def test_a_request_cancelled_while_its_message_is_written_down_leaves_nothing_to_tell(redis, monkeypatch, lands):
    """CodeRabbit #288: the agent's request cancelled while the outbox's write is on its way, before its answer
    came (the write landed, or not): the message is written down before it is stored, so it is not stored yet,
    nothing goes out, and the next process's minute crosses out what was written without a word."""
    import asyncio

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    gate, blocked = asyncio.Event(), asyncio.Event()
    real_hset = redis.hset

    async def held_up(*a, **kw):
        if lands:
            await real_hset(*a, **kw)
        blocked.set()  # the write landed (or not), and its answer is held up
        await gate.wait()
        return 1

    c = Chat().start()
    gw = Gateway([])
    try:
        with patch.object(mc, "send", new=gw):
            from app.services.conversation_service import add_message

            monkeypatch.setattr(redis, "hset", held_up)
            saving = asyncio.create_task(add_message(CONV, "agent", "Cancelled while written down"))
            await asyncio.wait_for(blocked.wait(), 1)
            saving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await saving
            monkeypatch.setattr(redis, "hset", real_hset)
        assert c.rows == [] and gw.sent == [] and len(await redis.hgetall(mc.OUTBOX)) == (1 if lands else 0)
        stopped = mc.BOOT
        monkeypatch.setattr(mc, "BOOT", "the-next-process")
        redis.kv.pop(f"msgr:alive:{stopped}")
        out = await mc.run_messenger_recover()
    finally:
        c.stop()
    assert out["told"] == 0 and c.rows == [] and not await redis.hgetall(mc.OUTBOX)


@pytest.mark.asyncio
async def test_a_request_cancelled_once_its_message_is_stored_leaves_it_to_the_next_process(redis, monkeypatch):
    """CodeRabbit #288: the agent's message stored, then their request cancelled before it is handed on (the insert
    landed, its answer still on its way, and with it the row's hand-off): written down first, under its row's id, it
    is the next process's minute's to tell, once. Rule 5: while its process is alive, or while its row can't be read,
    nothing is said yet."""
    import asyncio

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    c = Chat().start()
    real = c.add_message
    stored = asyncio.Event()

    async def answer_hangs(conversation_id, role, content, model_used=None, cost=0.0, *, message_id=None,
                           on_stored=None, on_unknown=None):
        # Stored in the database; the insert's answer, which hands the row on (on_stored), never comes back.
        row = await real(conversation_id, role, content, model_used, cost, message_id=message_id)
        if role == "agent":
            stored.set()
            await asyncio.sleep(30)
        if row and on_stored is not None:
            on_stored(row)
        return row

    gw = Gateway([])
    try:
        with patch.object(mc, "send", new=gw), patch("app.db.supabase.add_message", new=answer_hangs):
            from app.services.conversation_service import add_message

            saving = asyncio.create_task(add_message(CONV, "agent", "Stored, then the request cancelled"))
            await asyncio.wait_for(stored.wait(), 1)
            saving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await saving
        assert [r["content"] for r in c.rows] == ["Stored, then the request cancelled"] and gw.sent == []
        (left,) = [json.loads(v) for v in (await redis.hgetall(mc.OUTBOX)).values()]
        assert left["state"] == "waiting" and left["row"] == c.rows[0]["id"]
        assert (await mc.run_messenger_recover())["told"] == 0, "its process may still be on it"
        stopped = mc.BOOT
        monkeypatch.setattr(mc, "BOOT", "the-next-process")
        redis.kv.pop(f"msgr:alive:{stopped}")
        c.unreadable.add(c.rows[0]["id"])
        assert (await mc.run_messenger_recover())["told"] == 0 and len(await redis.hgetall(mc.OUTBOX)) == 1
        c.unreadable.clear()
        out = await mc.run_messenger_recover()
        again = await mc.run_messenger_recover()
    finally:
        c.stop()
    notes = [r["content"] for r in c.rows if r["role"] == "system"]
    assert out["told"] == 1 and again["told"] == 0 and notes == [mc.NOT_SENT_RESTART] and gw.sent == []
    assert not await redis.hgetall(mc.OUTBOX)


@pytest.mark.asyncio
@pytest.mark.parametrize("saves, sent, notes", [
    # Stored, its answer lost: asked again under its id, the row stored before comes back and goes out, once.
    (["lost"], ["Stored, its answer lost?"], []),
    # Stored, its answer lost, and the row then not read back: stored or not is not known. The minute finds it stored
    # (this process alive all the same) and tells the agent, once.
    (["lost", "unreadable"], [], [mc.NOT_SENT_OTHER]),
    # Rule 5: nothing stored, either time: the minute crosses it out without a word.
    (["fail", "fail"], [], []),
])
async def test_an_agents_message_whose_insert_may_have_landed_is_never_left_without_a_word(redis, saves, sent, notes):
    """CodeRabbit #288: db.add_message gives None for an insert that may have been committed (its answer lost). The
    message is never left stored in the panel with nothing sent, no note and nothing written down: asked again under
    the same id, the row stored before is handed on; still not known, it stays written down, marked unsure, and the
    recovery minute looks its row up while this process lives."""
    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    c = Chat().start()
    c.saves["agent"] = list(saves)
    gw = Gateway([])
    try:
        with patch.object(mc, "send", new=gw):
            from app.services.conversation_service import add_message

            row = await add_message(CONV, "agent", "Stored, its answer lost?")
            await mc.drain(CONV)
            left = [json.loads(v)["state"] for v in (await redis.hgetall(mc.OUTBOX)).values()]
            out = await mc.run_messenger_recover()
            again = await mc.run_messenger_recover()
    finally:
        c.stop()
    assert bool(row) == (saves == ["lost"]) and gw.sent == sent
    assert left == ([] if sent else ["unsure"]), "kept for the minute while its save is not known"
    assert out["told"] == len(notes) and again["told"] == 0
    assert [r["content"] for r in c.rows if r["role"] == "system"] == notes
    assert [r["content"] for r in c.rows if r["role"] == "agent"] == ([] if saves[0] == "fail" else
                                                                      ["Stored, its answer lost?"])
    assert not await redis.hgetall(mc.OUTBOX)


@pytest.mark.asyncio
async def test_an_outbox_write_whose_answer_was_lost_is_taken_as_written(redis, monkeypatch):
    """CodeRabbit #288: the outbox's write lands, then its answer is lost (a timeout, and Redis's cooldown with it):
    the entry's id is kept, so the message is written down as sending before it goes and crossed out after, and the
    next process's minute has no "not sent" to say about a message that went out. Rule 5: Redis not there at all, it
    goes out without being written down (counted)."""
    from redis.exceptions import TimeoutError as RedisTimeout

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    conv = {"id": CONV, "metadata": {"chat_channel": "messenger", "messenger": {"page_id": PAGE, "psid": PSID}}}
    real_hset = redis.hset
    states: list = []

    async def lands_then_times_out(key, field=None, value=None, **kw):
        out = await real_hset(key, field, value, **kw)
        states.append(json.loads(value)["state"])
        if len(states) == 1:
            raise RedisTimeout("Timeout reading from socket")
        return out

    c = Chat().start()
    gw = Gateway([])
    try:
        with patch.object(mc, "send", new=gw), \
                patch("app.db.supabase.get_conversation_metadata", new=AsyncMock(return_value=conv)):
            from app.services.conversation_service import add_message

            monkeypatch.setattr(redis, "hset", lands_then_times_out)
            await add_message(CONV, "agent", "Written down, its answer lost")
            await mc.drain(CONV)
            assert states == ["waiting", "sending"] and gw.sent == ["Written down, its answer lost"]
            assert not await redis.hgetall(mc.OUTBOX) and mc.MESSENGER_HEALTH["outbox_unsaved"] == 1
            # Rule 5: still cooling down, Redis is not tried for the next one: it goes out, not written down.
            await add_message(CONV, "agent", "Redis away")
            await mc.drain(CONV)
            assert gw.sent[-1] == "Redis away" and states == ["waiting", "sending"]
            assert mc.MESSENGER_HEALTH["outbox_unsaved"] == 2
        stopped = mc.BOOT
        monkeypatch.setattr(mc, "BOOT", "the-next-process")
        redis.kv.pop(f"msgr:alive:{stopped}", None)
        redis_client.set_client_for_tests(redis)  # the cooldown over
        assert (await mc.run_messenger_recover())["told"] == 0
    finally:
        c.stop()
    assert not [r for r in c.rows if r["role"] == "system"] and not await redis.hgetall(mc.OUTBOX)


@pytest.mark.asyncio
async def test_an_agents_message_whose_send_could_not_be_written_down_is_never_sent(redis, monkeypatch):
    """CodeRabbit #288: the outbox's "sending" not written (Redis refusing it) would leave "waiting" for a message that
    may be with the client, and a stop then would say "not sent": it is not sent, and the agent is told to send it
    again. Rule 5: written, it goes out."""
    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    real_hset = redis.hset

    async def no_sending(key, field=None, value=None, **kw):
        if value and '"state": "sending"' in str(value):
            raise ResponseError("READONLY You can't write against a read only replica.")
        return await real_hset(key, field, value, **kw)

    c = Chat().start()
    gw = Gateway([])
    try:
        with patch.object(mc, "send", new=gw):
            from app.services.conversation_service import add_message

            monkeypatch.setattr(redis, "hset", no_sending)
            await add_message(CONV, "agent", "Not written down as sending")
            await mc.drain(CONV)
            monkeypatch.setattr(redis, "hset", real_hset)
            await add_message(CONV, "agent", "Written down, sent")
            await mc.drain(CONV)
    finally:
        c.stop()
    assert gw.sent == ["Written down, sent"]
    assert [r["content"] for r in c.rows if r["role"] == "system"] == [mc.NOT_SENT_OTHER]
    assert not await redis.hgetall(mc.OUTBOX) and mc.MESSENGER_HEALTH["send_failed"] == 1


@pytest.mark.asyncio
async def test_a_send_the_gateway_confirmed_says_nothing_when_the_stop_cuts_what_follows(redis, monkeypatch):
    """CodeRabbit #288: sent, then the shutdown cuts its crossing out: no "maybe not sent" for the agent, and nothing
    left written down. Rule 5: one cut while with the gateway is still "maybe not sent" (the shutdown test above)."""
    import asyncio

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    real_hdel = redis.hdel
    held = {"n": 0}
    crossing = asyncio.Event()

    async def slow_first(key, *fields):
        held["n"] += 1
        if held["n"] == 1:
            crossing.set()
            await asyncio.sleep(30)
        return await real_hdel(key, *fields)

    c = Chat().start()
    try:
        with patch.object(mc, "send", new=Gateway([])):
            from app.services.conversation_service import add_message

            monkeypatch.setattr(redis, "hdel", slow_first)
            await add_message(CONV, "agent", "Sent, then the server stops")
            await asyncio.wait_for(crossing.wait(), 1)  # sent, and its crossing out begun, when the stop begins
            assert (await mc.drain_all(0.05)) == {"left": 1}
    finally:
        c.stop()
    assert not [r for r in c.rows if r["role"] == "system"] and mc.MESSENGER_HEALTH["sent"] == 1
    assert not await redis.hgetall(mc.OUTBOX) and mc.MESSENGER_HEALTH["cut_at_shutdown"] == 0


@pytest.mark.asyncio
async def test_an_agents_note_that_could_not_be_saved_is_saved_by_the_next_minute(redis):
    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    c = Chat().start()
    c.saves["system"] = ["fail"]
    try:
        with patch.object(mc, "send", new=Gateway([{"sent": 0, "failed": {"kind": "window_closed"}}])):
            from app.services.conversation_service import add_message

            await add_message(CONV, "agent", "Hi, Ana here. Still interested?")
            await mc.drain(CONV)
        assert not [r for r in c.rows if r["role"] == "system"]
        (left,) = [json.loads(v) for v in (await redis.hgetall(mc.OUTBOX)).values()]
        assert left["state"] == "untold" and left["note"] == mc.NOT_SENT["window_closed"]
        out = await mc.run_messenger_recover()
    finally:
        c.stop()
    assert out["told"] == 1 and [r["content"] for r in c.rows if r["role"] == "system"] == [mc.NOT_SENT["window_closed"]]
    assert not (await redis.hgetall(mc.OUTBOX))


@pytest.mark.asyncio
async def test_what_is_left_written_down_is_told_once_and_what_cannot_be_is_given_up(redis):
    """Another process's delivery waits while that process is alive; a stopped one's is told (not sent: it never
    reached the gateway); a note already saved by the stopped process is not saved twice; one unreadable, or still
    not saved after OUTBOX_KEEP_S, is given up (counted)."""
    import time

    def entry(**d):
        return json.dumps({"cid": CONV, "state": "waiting", "at": time.time(), **d})

    redis.kv["msgr:alive:alive-one"] = "1"
    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    redis.kv[mc.OUTBOX] = {
        "e-alive": entry(boot="alive-one"),
        "e-gone": entry(boot="gone-one"),
        "e-told": entry(boot="gone-one", state="sending"),
        "e-bad": "not json",
        "e-old": entry(boot="gone-one", state="untold", note="An old note", at=time.time() - mc.OUTBOX_KEEP_S - 1),
        # Left before where it went was looked up: a site chat's says nothing; one whose chat can't be read now is
        # looked at again next minute, and given up after OUTBOX_KEEP_S.
        "e-site": entry(boot="gone-one", cid="site-chat"),
        "e-unread": entry(boot="gone-one", cid="unread-chat"),
        "e-unread-old": entry(boot="gone-one", cid="unread-chat", at=time.time() - mc.OUTBOX_KEEP_S - 1),
        # A chat that is no more: nothing goes there, crossed out without a word.
        "e-no-chat": entry(boot="gone-one", cid="no-such-chat"),
    }
    chats = {"site-chat": {"id": "site-chat", "metadata": {"site": "bbc"}}}

    async def read(cid):
        if cid == "unread-chat":
            raise RuntimeError("the database did not answer")
        return chats.get(cid)

    c = Chat().start()
    c.rows.append({"id": mc._note_id("e-told"), "conversation_id": CONV, "role": "system",
                   "content": mc.NOT_SENT["unknown"], "created_at": now_iso()})
    c.saves["system"] = ["", "", "fail"]
    try:
        with patch("app.db.supabase.get_conversation_metadata", new=read):
            out = await mc.run_messenger_recover()
    finally:
        c.stop()
    assert set((await redis.hgetall(mc.OUTBOX))) == {"e-alive", "e-unread"}
    assert [r["content"] for r in c.rows if r["role"] == "system"] == [mc.NOT_SENT["unknown"], mc.NOT_SENT_RESTART]
    assert out["told"] == 2 and mc.MESSENGER_HEALTH["outbox_given_up"] == 3


# ── the events whose turn failed ──────────────────────────────────


async def _fail_once(chat, redis):
    """An event whose turn fails: kept in the inbox, then made old enough for the recovery."""
    async def broken(req, add_message):
        raise RuntimeError("database away")

    chat.answer = broken
    with pytest.raises(mc.EventFailed):
        await mc.handle(event())
    redis.kv[mc.INBOX]["m1"] -= mc.RECOVER_AFTER_S + 1


@pytest.mark.asyncio
async def test_an_event_whose_turn_failed_is_answered_later_and_its_replies_sent(chat, redis):
    await _fail_once(chat, redis)

    async def skye(req, add_message):
        await add_message(req.conversation_id, "ai", "Sorry for the wait! Which dates?")
        return ChatResponse(conversation_id=req.conversation_id, message="x", type="ai", model_used="x")

    chat.answer = skye
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        out = await mc.run_messenger_recover()
    assert out["answered"] == 1 and gw.sent == ["Sorry for the wait! Which dates?"]
    assert not redis.kv[mc.INBOX] and mc.MESSENGER_HEALTH["recovered"] == 1
    # The gateway delivers it again after all: answered and sent already, nothing to send twice.
    assert (await mc.handle(event())) == {"replies": []} and len(chat.requests) == 2


@pytest.mark.asyncio
async def test_a_recovery_with_the_gateway_down_sends_next_minute_without_a_second_turn(chat, redis):
    await _fail_once(chat, redis)

    async def skye(req, add_message):
        await add_message(req.conversation_id, "ai", "Here now.")
        return ChatResponse(conversation_id=req.conversation_id, message="x", type="ai", model_used="x")

    chat.answer = skye
    gw = Gateway([{"sent": 0, "failed": {"kind": "gateway"}}])
    with patch.object(mc, "send", new=gw):
        assert (await mc.run_messenger_recover())["waiting"] == 1
        assert (await mc.run_messenger_recover())["answered"] == 1
    assert gw.sent == ["Here now.", "Here now."] and len(chat.requests) == 2, "one turn for the failure, one after"


def _refuse_answers(redis, monkeypatch, key: str = "") -> dict:
    """Redis refusing an event's answer (all, or `key`'s: its write, or its giving to the gateway with the right to send
    it) once while "on", a command refused (never a cooldown)."""
    real_eval = redis.eval
    refuse = {"on": True}

    async def eval_(script, numkeys, *keys_and_args):
        answers = (mc.KEEP_ANSWER_LUA, mc.GIVE_TO_GATEWAY_LUA)
        if refuse["on"] and script in answers and (not key or keys_and_args[0] == key):
            refuse["on"] = False
            raise ResponseError("OOM command not allowed when used memory > 'maxmemory'")
        return await real_eval(script, numkeys, *keys_and_args)

    monkeypatch.setattr(redis, "eval", eval_)
    return refuse


@pytest.mark.asyncio
async def test_a_recovered_answer_redis_did_not_take_is_written_before_it_is_sent_never_answered_twice(chat, redis,
                                                                                                     monkeypatch):
    """CodeRabbit #288: the recovery's turn answers, and Redis refuses its answer. Nothing is sent while it is not
    written (a send that then never reached the gateway would clear its mark, and once the claim ran out the event
    would be answered a second time): this process holds it and writes it at its next minute, its claim gone by then
    or not, and the send that then never reaches the gateway goes again the minute after, from the answer written.
    One turn for the failure, one after it. Rule 5: an answer written goes out at once
    (test_an_event_whose_turn_failed_is_answered_later_and_its_replies_sent)."""
    from tests.fake_redis import FakeClock

    await _fail_once(chat, redis)
    _turns(chat)
    clock = FakeClock()
    redis.clock = clock
    _refuse_answers(redis, monkeypatch)
    gw = Gateway([{"sent": 0, "failed": {"kind": "gateway"}}])
    reply = "About: Hi, two seats to Istanbul in December?"
    with patch.object(mc, "send", new=gw):
        assert (await mc.run_messenger_recover())["waiting"] == 1
        assert gw.sent == [] and mc.MESSENGER_HEALTH["answer_unkept"] == 1, "nothing sent before it is written"
        clock.advance(mc.PENDING_S * 2)  # the turn's claim runs out meanwhile
        assert (await mc.run_messenger_recover())["waiting"] == 1  # written; its send never reached the gateway
        assert (await mc.run_messenger_recover())["answered"] == 1
    assert gw.sent == [reply, reply] and len(chat.requests) == 2, "one turn for the failure, one after it"
    assert not redis.kv[mc.INBOX] and not mc._UNKEPT and mc.MESSENGER_HEALTH["claim_lost"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("meanwhile", ["delivered_again", "nothing"])
async def test_a_recovered_answer_gone_during_its_send_is_kept_for_the_send_made_again(chat, redis, meanwhile):
    """CodeRabbit #288: the recovery's send of a kept event is on its way when the event's answer goes from Redis (its
    day over, or let go of), and that send never reaches the gateway. Delivered again by the gateway meanwhile: that
    delivery gets none (the API's send began), and its own answer is kept as the API's to send, never an empty one
    that would let the event go unsent. Nothing meanwhile: the recovery's answer is written back before its mark is
    cleared, so no second turn. Either way the next pass sends the replies, once."""
    import asyncio

    await _fail_once(chat, redis)
    _turns(chat)
    sending, go_on = asyncio.Event(), asyncio.Event()
    sent: list = []
    answers = [{"sent": 0, "failed": {"kind": "gateway"}}]

    async def gw(page_id, psid, messages, tag=None):
        sent.append(messages[0]["text"])
        if len(sent) == 1:
            sending.set()
            await go_on.wait()
        return answers.pop(0) if answers else {"sent": 1}

    with patch.object(mc, "send", new=gw):
        recovery = asyncio.create_task(mc.run_messenger_recover())
        await asyncio.wait_for(sending.wait(), 1)
        redis.kv.pop("msgr:event:m1")  # the event's answer goes from Redis meanwhile
        if meanwhile == "delivered_again":
            assert (await mc.handle(event()))["replies"] == [], "the API's send began: the gateway gets none"
        go_on.set()
        assert (await asyncio.wait_for(recovery, 2))["waiting"] == 1  # it never reached the gateway
        out = await mc.run_messenger_recover()
    reply = "About: Hi, two seats to Istanbul in December?"
    assert out["answered"] == 1 and sent == [reply, reply] and not redis.kv[mc.INBOX]
    assert len(chat.requests) == (3 if meanwhile == "delivered_again" else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("sending", ["on_its_way", "over", "not_begun"])
async def test_a_gateway_delivery_keeps_an_empty_answer_only_once_the_apis_send_is_over(redis, sending):
    """Rule 5 of the same: the API's send on its way, the delivery's own answer is kept as the API's to send; over
    ("done"), an answer with none is kept (the replies went: never again); none begun, the delivery takes the right to
    send them and keeps its own answer. The gateway gets them only in the last."""
    key, k, token = "msgr:event:m1", "m1", "pending:mine"
    redis.kv[key] = token
    if sending == "on_its_way":
        redis.kv[mc._sending_key(k)] = f"{time.time()}:{mc.BOOT}"
    elif sending == "over":
        redis.kv[mc._sending_key(k)] = "done"
    replies = [{"text": "Hi"}]
    given = await mc._give_to_gateway(key, k, replies, token)
    kept = json.loads(redis.kv[key])
    if sending == "on_its_way":
        assert given == "theirs" and kept == {"replies": replies, "by": "api"}
    elif sending == "over":
        assert given == "theirs" and kept == {"replies": [], "by": "gateway"}
        assert redis.kv[mc._sending_key(k)] == "done"
    else:
        assert given == "kept" and kept == {"replies": replies, "by": "gateway"}
        assert redis.kv[mc._sending_key(k)].startswith(f"{mc.SENT_BY_GATEWAY}:")


@pytest.mark.asyncio
async def test_a_delivery_whose_giving_redis_did_not_take_waits_and_then_gets_the_replies_once(chat, redis,
                                                                                             monkeypatch):
    """CodeRabbit #288, from the gateway: Redis refuses the giving of the event's replies (their right and their
    answer) as its turn ends. Whether the right is this delivery's is not known (another delivery's, or the API's own
    send, may hold them), so the gateway gets none (409), the event kept, and a delivery made again while Redis is
    down waits too, with no second turn. The process's next minute, Redis back, gives them again, the right this
    delivery's, and the next delivery gets them from Redis. Rule 5: a giving Redis takes goes back at once
    (test_a_first_message_starts_a_chat_and_takes_the_widgets_turn)."""
    _turns(chat)
    _refuse_answers(redis, monkeypatch)
    reply = [{"text": "About: Hi, two seats to Istanbul in December?"}]
    with pytest.raises(mc.StillAnswering):
        await mc.handle(event())
    assert "msgr:event:m1" in mc._UNGIVEN and "m1" in redis.kv[mc.INBOX], "held, the event kept"
    redis.down = True
    with pytest.raises(mc.StillAnswering):
        await mc.handle(event())  # never given from the process: its right may be another's
    redis.down = False
    redis_client.set_client_for_tests(redis)  # the cooldown its failure began, over
    assert len(chat.requests) == 1, "no second turn"
    await mc.run_messenger_recover()
    assert not mc._UNKEPT and not mc._UNGIVEN
    assert json.loads(redis.kv["msgr:event:m1"]) == {"replies": reply, "by": "gateway"}
    assert redis.kv[f"{mc.INBOX}:sending:m1"].startswith(f"{mc.SENT_BY_GATEWAY}:"), "the right this delivery's"
    assert (await mc.handle(event()))["replies"] == reply and len(chat.requests) == 1


@pytest.mark.asyncio
async def test_a_giving_redis_made_but_whose_answer_was_lost_goes_back_with_the_next_delivery_once(chat, redis,
                                                                                                 monkeypatch):
    """CodeRabbit #288: Redis gives the event's replies to the gateway (their right and answer written) and its answer
    is lost on the way back: the gateway gets none (409), and the delivery made again gets them, the right found this
    delivery's (made again, it is the same) and taken again from then (the gateway sends them from then), with no
    second turn and nothing lost."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    _turns(chat)
    real_eval = redis.eval
    lost = {"on": True}

    async def answer_lost(script, numkeys, *keys_and_args):
        out = await real_eval(script, numkeys, *keys_and_args)
        if script == mc.GIVE_TO_GATEWAY_LUA and lost["on"]:
            lost["on"] = False
            raise RedisConnectionError("Connection closed by server.")
        return out

    monkeypatch.setattr(redis, "eval", answer_lost)
    with pytest.raises(mc.StillAnswering):
        await mc.handle(event())
    right = redis.kv[f"{mc.INBOX}:sending:m1"]
    redis_client.set_client_for_tests(redis)  # the cooldown its failure began, over
    reply = [{"text": "About: Hi, two seats to Istanbul in December?"}]
    assert (await mc.handle(event()))["replies"] == reply and len(chat.requests) == 1
    again = redis.kv[f"{mc.INBOX}:sending:m1"]
    assert again.split(":")[2] == right.split(":")[2], "the same delivery's right"
    assert float(again.split(":")[1]) >= float(right.split(":")[1]), "taken again from now"
    assert not mc._UNGIVEN and not mc._UNKEPT and mc.MESSENGER_HEALTH["claim_lost"] == 0


@pytest.mark.asyncio
async def test_an_earlier_events_answer_redis_did_not_take_is_never_sent_before_it_is_written(chat, redis,
                                                                                            monkeypatch):
    """CodeRabbit #288, a person's earlier event answered before their later one: Redis refuses its answer, so none
    of it is sent and the later one waits (409). The next minute its process writes it and the recovery sends it,
    with no second turn; then the later one is answered. Rule 5: an earlier answer written goes out before the later
    turn at once (test_a_later_message_never_comes_before_an_earlier_one_whose_turn_failed)."""
    _turns(chat, fail_first=1)
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))
    _refuse_answers(redis, monkeypatch, key="msgr:event:m1")
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        with pytest.raises(mc.StillAnswering):
            await mc.handle(event(mid="m2", text="Second", timestamp=200))
        assert gw.sent == [] and mc.MESSENGER_HEALTH["answer_unkept"] == 1
        redis.kv[mc.INBOX]["m1"] -= mc.RECOVER_AFTER_S + 1
        assert (await mc.run_messenger_recover())["answered"] == 1  # written, then sent
        out = await mc.handle(event(mid="m2", text="Second", timestamp=200))
    assert gw.sent == ["About: First"] and [r["text"] for r in out["replies"]] == ["About: Second"]
    assert len(chat.requests) == 3, "the failure, the first's answer, the second's: never the first's again"


@pytest.mark.asyncio
@pytest.mark.parametrize("redis_then", ["answers", "refuses"])
async def test_a_shutdown_writes_the_answers_its_process_holds(chat, redis, monkeypatch, redis_then):
    """CodeRabbit #288: Redis refused the giving of an event's replies as its turn ended (held by this process, none
    given), and the process stops before its next minute: the shutdown gives them, so the next delivery gets the same
    replies, with no second turn. Redis still refusing then: it goes with the process, counted (answer_dropped) and
    logged."""
    _turns(chat)
    refuse = _refuse_answers(redis, monkeypatch)
    reply = [{"text": "About: Hi, two seats to Istanbul in December?"}]
    with pytest.raises(mc.StillAnswering):
        await mc.handle(event())
    assert mc._UNKEPT and mc._UNGIVEN
    refuse["on"] = redis_then == "refuses"
    out = await mc.write_held(0.5)
    if redis_then == "refuses":
        assert out == {"written": 0, "dropped": 1} and mc.MESSENGER_HEALTH["answer_dropped"] == 1
        assert not mc._UNGIVEN
        return
    assert out == {"written": 1, "dropped": 0} and not mc._UNKEPT and not mc._UNGIVEN
    assert (await mc.handle(event()))["replies"] == reply and len(chat.requests) == 1, "no second turn"


@pytest.mark.asyncio
async def test_a_shutdown_stops_the_recovery_before_it_writes_the_held_answers(monkeypatch):
    """CodeRabbit #288: the recovery may be in a turn as the process stops, and a turn whose answer Redis refuses holds
    it: the recovery is stopped (waited for, RECOVER_STOP_S at most) before the held answers are written, so none it
    holds comes after the flush, to go with the process. Rule 5: the other scheduler tasks stop after, as before."""
    import asyncio

    with patch("app.db.supabase.get_client"):
        from app import main

    order: list = []
    started: list = []

    async def looping(name):
        try:
            started.append(name)
            await asyncio.sleep(30)  # in a turn, or between passes
        except asyncio.CancelledError:
            order.append(f"{name} stopped")
            raise

    recover = asyncio.create_task(looping("recovery"), name="scheduler_messenger_recover")
    sweep = asyncio.create_task(looping("sweep"), name="scheduler_agent_sweep")
    while len(started) < 2:
        await asyncio.sleep(0)

    async def write_held(*a, **kw):
        order.append("held written")
        return {"written": 0, "dropped": 0}

    monkeypatch.setattr(mc, "drain_all", AsyncMock(return_value={"left": 0}))
    monkeypatch.setattr(mc, "write_held", write_held)
    for name, value in (("ledger_task", None), ("flags_task", None), ("scheduler_tasks", [sweep, recover])):
        monkeypatch.setattr(main.app.state, name, value, raising=False)
    await main.shutdown()
    await asyncio.gather(sweep, recover, return_exceptions=True)
    assert order[:2] == ["recovery stopped", "held written"] and "sweep stopped" in order[2:]


@pytest.mark.asyncio
async def test_a_recovery_whose_turns_row_is_on_its_way_stops_before_the_flush_and_holds_nothing(redis, monkeypatch):
    """CodeRabbit #288: the recovery is in a turn as the process stops, its row's insert on its way (the real
    db.add_message, its answer back only once the shutdown is over), with Redis refusing every answer. The turn only
    stops waiting for its row's save (save_in_order), so the recovery stops at the first cancel, within the shutdown's
    wait and before the held answers are written; the save goes on by itself, and its row, stored after the turn
    stopped with no answer, joins nothing and is never sent on its own (turn_rows_left). Nothing held after the flush
    (nothing to go with the process): its claim is left to run out, and the next process answers the event. Rule 5:
    a recovery with no row on its way stops the same
    (test_a_shutdown_stops_the_recovery_before_it_writes_the_held_answers)."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.db import supabase as db

    with patch("app.db.supabase.get_client"):
        from app import main

    c = Chat().start()
    fake_add = c._ps.pop(0)  # the real db.add_message from here: its insert's answer waited for on a cancel
    fake_add.stop()
    try:
        await _fail_once(c, redis)
        landed, answer = asyncio.Event(), asyncio.Event()

        async def run_sync(fn, **kw):
            out = fn()
            if not answer.is_set():
                landed.set()
                await answer.wait()  # the insert on its way past the shutdown's wait
            return out

        def insert_row(payload):
            row = {"id": payload.get("id") or "r1", "created_at": now_iso(), **payload}
            return SimpleNamespace(execute=lambda: SimpleNamespace(data=[row]))

        client = MagicMock()
        client.table.return_value.insert.side_effect = insert_row

        async def turn(req, add_message):
            await add_message(req.conversation_id, "ai", "Here now.")
            return ChatResponse(conversation_id=req.conversation_id, message="x", type="ai", model_used="x")

        c.answer = turn
        real_eval = redis.eval

        async def refusing(script, numkeys, *keys_and_args):
            if script in (mc.KEEP_ANSWER_LUA, mc.GIVE_TO_GATEWAY_LUA):
                raise ResponseError("OOM command not allowed when used memory > 'maxmemory'")
            return await real_eval(script, numkeys, *keys_and_args)

        flushed: list = []
        real_write_held = mc.write_held

        async def write_held(*a, **kw):
            out = await real_write_held(*a, **kw)
            flushed.append((out, recover.done()))
            return out

        async def touch(*a, **kw):
            return None

        gw = Gateway([])
        with patch.object(db, "get_client", return_value=client), patch.object(db, "_run_sync", new=run_sync), \
                patch.object(db, "_touch_conversation_activity", new=touch), patch.object(mc, "send", new=gw):
            monkeypatch.setattr(redis, "eval", refusing)
            recover = asyncio.create_task(mc.run_messenger_recover(), name="scheduler_messenger_recover")
            await asyncio.wait_for(landed.wait(), 1)
            monkeypatch.setattr(mc, "drain_all", AsyncMock(return_value={"left": 0}))
            monkeypatch.setattr(mc, "write_held", write_held)
            for name, value in (("ledger_task", None), ("flags_task", None), ("scheduler_tasks", [recover])):
                monkeypatch.setattr(main.app.state, name, value, raising=False)
            await main.shutdown()
            assert flushed == [({"written": 0, "dropped": 0}, True)], "stopped by its first cancel, before the flush"
            with pytest.raises(asyncio.CancelledError):
                await recover
            assert mc._SAVES, "its row's save goes on by itself"
            answer.set()  # the insert answers, the process all but gone
            await asyncio.wait_for(mc.saved(), 1)
    finally:
        c.stop()
    assert mc.MESSENGER_HEALTH["turn_rows_left"] == 1 and gw.sent == [], "stored, in no answer, never sent alone"
    assert not mc._UNKEPT and not mc._UNGIVEN and not mc._HELD_BESIDE, "nothing held after the flush"
    assert mc.MESSENGER_HEALTH["answer_unkept"] == 0 and mc.MESSENGER_HEALTH["answer_dropped"] == 0
    assert str(redis.kv["msgr:event:m1"]).startswith("pending:"), "its claim left to run out"


@pytest.mark.asyncio
async def test_a_shutdown_tries_each_held_answer_whatever_redis_said_of_the_one_before(redis, monkeypatch):
    """CodeRabbit #288: the process holds two answers and Redis refuses the first one's write (a key of its own): the
    second is written all the same, and only the first goes with the process (answer_dropped). Rule 5: every one
    written (test_a_shutdown_writes_the_answers_its_process_holds)."""
    first, second = "msgr:event:m1", "msgr:event:m2"
    mc._UNKEPT[first] = ("pending:one", json.dumps({"replies": [{"text": "One"}], "by": "api"}))
    mc._UNKEPT[second] = ("pending:two", json.dumps({"replies": [{"text": "Two"}], "by": "api"}))
    real_eval = redis.eval

    async def refuses_the_first(script, numkeys, *keys_and_args):
        if script == mc.KEEP_ANSWER_LUA and keys_and_args[0] == first:
            raise ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")
        return await real_eval(script, numkeys, *keys_and_args)

    monkeypatch.setattr(redis, "eval", refuses_the_first)
    assert await mc.write_held(0.5) == {"written": 1, "dropped": 1}
    assert json.loads(redis.kv[second])["replies"] == [{"text": "Two"}] and first not in redis.kv
    assert mc.MESSENGER_HEALTH["answer_dropped"] == 1 and not mc._UNKEPT


@pytest.mark.asyncio
async def test_an_event_whose_answer_a_process_holds_is_never_taken_by_another_meanwhile(chat, redis, monkeypatch):
    """CodeRabbit #288: Redis keeps refusing the answer of process A's recovery turn, minute after minute. A keeps the
    event's claim meanwhile (a new expiry each minute), so process B's recovery never takes the event and answers it a
    second time, however long it lasts; once Redis takes the answer, it is sent, once. Rule 5: a claim nobody holds
    an answer for runs out as before, and the event is answered again (test_a_turn_that_fails_can_be_delivered_again)."""
    from tests.fake_redis import FakeClock

    await _fail_once(chat, redis)
    _turns(chat)
    clock = FakeClock()
    redis.clock = clock
    real_eval = redis.eval
    refuse = {"on": True}

    async def refusing(script, numkeys, *keys_and_args):
        if refuse["on"] and script in (mc.KEEP_ANSWER_LUA, mc.GIVE_TO_GATEWAY_LUA):
            raise ResponseError("OOM command not allowed when used memory > 'maxmemory'")
        return await real_eval(script, numkeys, *keys_and_args)

    def become(boot, holds):
        monkeypatch.setattr(mc, "BOOT", boot)
        mc._UNKEPT.clear()
        mc._UNKEPT.update(holds)

    monkeypatch.setattr(redis, "eval", refusing)
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        become("process-a", {})
        assert (await mc.run_messenger_recover())["waiting"] == 1  # answered, its answer held
        held = mc._UNKEPT.copy()
        for _ in range(3):  # minutes go by, past the claim's own time, Redis refusing the answer each one
            clock.advance(60)
            become("process-a", held)
            await mc.run_messenger_recover()
            held = mc._UNKEPT.copy()
            become("process-b", {})
            assert (await mc.run_messenger_recover())["waiting"] == 1, "B finds the event still claimed"
        refuse["on"] = False
        become("process-a", held)
        out = await mc.run_messenger_recover()
    assert out["answered"] == 1 and gw.sent == ["About: Hi, two seats to Istanbul in December?"]
    assert len(chat.requests) == 2, "the failure, then one turn"


@pytest.mark.asyncio
async def test_a_send_that_sent_nothing_goes_again_after_its_mark_could_not_be_cleared(chat, redis, monkeypatch):
    """CodeRabbit #288: the recovery's send never reached the gateway, and clearing its mark failed (Redis
    blinking): once Redis answers, the same replies go, with no second turn, however long the mark has waited (past
    SENDING_S it would read as a send a stopped process was making). Rule 5: one the gateway may have had (unknown)
    is never sent again (test_a_recovered_answer_the_gateway_may_have_sent_is_never_sent_again)."""
    await _fail_once(chat, redis)
    _turns(chat)
    gw = Gateway([{"sent": 0, "failed": {"kind": "gateway"}}])
    with patch.object(mc, "send", new=gw):
        blinks = _blink_send_marks(redis, monkeypatch)
        assert (await mc.run_messenger_recover())["waiting"] == 1
        assert f"{mc.INBOX}:sending:m1" in redis.kv, "its mark left by the failed clear"
        blinks["on"] = False
        monkeypatch.setattr(mc, "SENDING_S", -1.0)  # however long it waited: past SENDING_S
        assert (await mc.run_messenger_recover())["answered"] == 1
    assert gw.sent == ["About: Hi, two seats to Istanbul in December?"] * 2 and len(chat.requests) == 2
    assert not redis.kv[mc.INBOX] and mc.MESSENGER_HEALTH["recover_unsure"] == 0


def _blink_send_marks(redis, monkeypatch) -> dict:
    """Redis failing for the clear of a send's mark (its delete, or its compare and delete) while "on"."""
    real_eval, real_delete = redis.eval, redis.delete
    blinks = {"on": True}

    def sending(key) -> bool:
        return str(key).startswith(f"{mc.INBOX}:sending:")

    async def eval_(script, numkeys, *keys_and_args):
        clears = (mc.RELEASE_LUA, mc.CLEAR_UNSENT_LUA)
        if blinks["on"] and script in clears and any(sending(k) for k in keys_and_args[:numkeys]):
            raise ResponseError("LOADING Redis is loading the dataset in memory")
        return await real_eval(script, numkeys, *keys_and_args)

    async def delete(*keys):
        if blinks["on"] and any(sending(k) for k in keys):
            raise ResponseError("LOADING Redis is loading the dataset in memory")
        return await real_delete(*keys)

    monkeypatch.setattr(redis, "eval", eval_)
    monkeypatch.setattr(redis, "delete", delete)
    return blinks


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["alive", "long", "gone"])
async def test_a_send_that_sent_nothing_is_left_to_its_process_by_another_on_the_same_redis(chat, redis, monkeypatch,
                                                                                            owner):
    """CodeRabbit #288: two API processes on one Redis. Process A's send never reached the gateway and its mark could
    not be cleared (Redis failing): process B, meeting the mark past SENDING_S while A is alive, waits, however long
    A's clears keep failing (long: past SENDING_S + ALIVE_S, A's alive mark renewed all the while); A's next minute
    once Redis answers clears it, the lock held by B or not, and the same replies go, once, with no second turn. Rule
    5: A gone before it could, what it knew went with it: B takes the mark for a send begun, maybe out, never sent
    again (counted), as an outcome the gateway may have had is
    (test_a_recovered_answer_the_gateway_may_have_sent_is_never_sent_again)."""
    await _fail_once(chat, redis)
    _turns(chat)
    reply = "About: Hi, two seats to Istanbul in December?"
    gw = Gateway([{"sent": 0, "failed": {"kind": "gateway"}}])

    def become(boot, knows):
        monkeypatch.setattr(mc, "BOOT", boot)
        mc._UNSENT.clear()
        mc._UNSENT.update(knows)

    with patch.object(mc, "send", new=gw):
        blinks = _blink_send_marks(redis, monkeypatch)
        become("process-a", type(mc._UNSENT)())
        assert (await mc.run_messenger_recover())["waiting"] == 1
        assert f"{mc.INBOX}:sending:m1" in redis.kv, "its mark left by the failed clear"
        monkeypatch.setattr(mc, "SENDING_S", -1.0)  # however long it waited: past SENDING_S
        if owner == "long":
            # Past SENDING_S + ALIVE_S, A's minutes go on: its alive mark renewed each time, its clear failing each time.
            monkeypatch.setattr(mc, "SENDING_S", -1.0 - mc.ALIVE_S)
            redis.kv[mc.RECOVER_LOCK] = "a-pass-of-process-b"
            for _ in range(2):
                assert (await mc.run_messenger_recover())["state"] == "busy"
            redis.kv.pop(mc.RECOVER_LOCK)
            assert f"{mc.INBOX}:sending:m1" in redis.kv and mc._UNSENT, "still not cleared, still known"
        blinks["on"] = False  # Redis answers again: B looks at the event before A's next minute
        a_knows = mc._UNSENT.copy()
        become("process-b", type(mc._UNSENT)())
        if owner == "gone":
            redis.kv.pop("msgr:alive:process-a")
            out = await mc.run_messenger_recover()
            assert out["answered"] == 1 and mc.MESSENGER_HEALTH["recover_unsure"] == 1
            assert gw.sent == [reply] and not redis.kv[mc.INBOX] and len(chat.requests) == 2
            return
        b = await mc.run_messenger_recover()
        assert b["waiting"] == 1 and "m1" in redis.kv[mc.INBOX] and gw.sent == [reply]
        # A's minute while a pass of B's holds the lock: A clears its own mark all the same.
        redis.kv[mc.RECOVER_LOCK] = "a-pass-of-process-b"
        become("process-a", a_knows)
        assert (await mc.run_messenger_recover())["state"] == "busy"
        assert f"{mc.INBOX}:sending:m1" not in redis.kv
        redis.kv.pop(mc.RECOVER_LOCK)
        become("process-b", type(mc._UNSENT)())
        out = await mc.run_messenger_recover()
    assert out["answered"] == 1 and gw.sent == [reply] * 2 and len(chat.requests) == 2
    assert not redis.kv[mc.INBOX] and mc.MESSENGER_HEALTH["recover_unsure"] == 0


@pytest.mark.asyncio
async def test_a_send_that_sent_nothing_goes_from_its_process_after_another_let_its_event_go(chat, redis,
                                                                                            monkeypatch):
    """CodeRabbit #288: process A's send never reached the gateway and its mark could not be cleared; then A, still
    running, loses Redis past ALIVE_S, and its alive mark runs out. Process B looks at the event first and takes the
    mark for a send a stopped process began (maybe out: never sent by B), and lets the event go. Once A reaches Redis
    again it finds the event gone and the mark still its own: it knew nothing went out, and no one sends while the
    mark is its own, so it sends the replies itself, once, with no second turn (sent_late). Rule 5: A back before B
    looked clears its mark, and the recovery sends them
    (test_a_send_that_sent_nothing_is_left_to_its_process_by_another_on_the_same_redis)."""
    await _fail_once(chat, redis)
    _turns(chat)
    reply = "About: Hi, two seats to Istanbul in December?"
    gw = Gateway([{"sent": 0, "failed": {"kind": "gateway"}}])

    def become(boot, knows):
        monkeypatch.setattr(mc, "BOOT", boot)
        mc._UNSENT.clear()
        mc._UNSENT.update(knows)

    with patch.object(mc, "send", new=gw):
        blinks = _blink_send_marks(redis, monkeypatch)
        become("process-a", {})
        assert (await mc.run_messenger_recover())["waiting"] == 1
        assert f"{mc.INBOX}:sending:m1" in redis.kv and mc._UNSENT, "its mark left by the failed clear, and known"
        monkeypatch.setattr(mc, "SENDING_S", -1.0)
        a_knows = mc._UNSENT.copy()
        redis.kv.pop("msgr:alive:process-a")  # A cut off from Redis past ALIVE_S: its alive mark ran out
        blinks["on"] = False
        become("process-b", {})
        out = await mc.run_messenger_recover()
        assert out["answered"] == 1 and mc.MESSENGER_HEALTH["recover_unsure"] == 1 and not redis.kv[mc.INBOX]
        assert gw.sent == [reply], "B sends nothing of it: maybe out, as far as B can tell"
        become("process-a", a_knows)  # A reaches Redis again
        await mc.run_messenger_recover()
        await mc.run_messenger_recover()
    assert gw.sent == [reply, reply] and mc.MESSENGER_HEALTH["sent_late"] == 1 and len(chat.requests) == 2
    assert not mc._UNSENT and redis.kv[f"{mc.INBOX}:sending:m1"] == "done"


@pytest.mark.asyncio
async def test_a_process_back_clears_its_mark_only_while_its_event_is_kept_else_sends_late(chat, redis, monkeypatch):
    """CodeRabbit #288: as above, A cut off from Redis with a send it knows sent nothing; B lets the event go (taking
    A's mark for a send begun) just as A, back, looks at it. A's look at the inbox and the clear of its mark are one
    step: the event found gone, A keeps its mark and sends the replies late, once, never dropping what it knew with
    no event left to send them from. Rule 5: the event still kept, A clears its mark and the recovery sends them
    (test_a_send_that_sent_nothing_is_left_to_its_process_by_another_on_the_same_redis)."""
    await _fail_once(chat, redis)
    _turns(chat)
    reply = "About: Hi, two seats to Istanbul in December?"
    gw = Gateway([{"sent": 0, "failed": {"kind": "gateway"}}])
    who = mc._who(event())

    def become(boot, knows):
        monkeypatch.setattr(mc, "BOOT", boot)
        mc._UNSENT.clear()
        mc._UNSENT.update(knows)

    with patch.object(mc, "send", new=gw):
        blinks = _blink_send_marks(redis, monkeypatch)
        become("process-a", {})
        assert (await mc.run_messenger_recover())["waiting"] == 1
        a_knows = mc._UNSENT.copy()
        assert a_knows, "its mark left by the failed clear, and known"
        blinks["on"] = False
        redis.kv.pop("msgr:alive:process-a")  # A cut off from Redis past ALIVE_S
        real_eval = redis.eval
        let_go = {"on": True}

        async def b_lets_it_go_first(script, numkeys, *keys_and_args):
            if let_go["on"] and script in (mc.KEEP_ANSWER_LUA, mc.CLEAR_UNSENT_LUA):
                let_go["on"] = False  # B lets the event go just before A's clear
                await redis.zrem(mc.INBOX, "m1")
                await redis.zrem(who, "m1")
            return await real_eval(script, numkeys, *keys_and_args)

        monkeypatch.setattr(redis, "eval", b_lets_it_go_first)
        become("process-a", a_knows)  # A reaches Redis again
        await mc.run_messenger_recover()
        await mc.run_messenger_recover()
    assert gw.sent == [reply, reply] and mc.MESSENGER_HEALTH["sent_late"] == 1 and len(chat.requests) == 2
    assert not mc._UNSENT and redis.kv[f"{mc.INBOX}:sending:m1"] == "done" and "m1" not in redis.kv[mc.INBOX]


@pytest.mark.asyncio
@pytest.mark.parametrize("mark_then", ["the_one_read", "cleared_meanwhile"])
async def test_an_event_taken_for_one_whose_process_stopped_is_let_go_of_only_on_the_mark_read(redis, mark_then):
    """CodeRabbit #288: process B reads A's mark as a send begun by a process that stopped, and before B lets the event
    go, A, back, clears it as one that sent nothing (its answer written back): B never lets the event go then, so the
    recovery sends its replies from the answer. Rule 5: the mark still the one read, the event is let go of
    (counted)."""
    k, mark = "m1", "1.0:process-a"
    who = mc._who(event())
    await redis.zadd(mc.INBOX, {k: 1.0})
    await redis.zadd(who, {k: 1.0})
    redis.kv[f"{mc.INBOX}:{k}"] = event().model_dump_json()
    redis.kv[mc._sending_key(k)] = mark
    if mark_then == "cleared_meanwhile":
        redis.kv.pop(mc._sending_key(k))
    ended = await mc._send_ended(k, who, "begun", mark)
    kept = mark_then == "cleared_meanwhile"
    assert ended is (not kept), "it says whether it let the event go"
    assert (k in redis.kv[mc.INBOX]) == kept and (f"{mc.INBOX}:{k}" in redis.kv) == kept
    assert mc.MESSENGER_HEALTH["recover_unsure"] == (0 if kept else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["delivered", "recovered"])
@pytest.mark.parametrize("mark_then", ["cleared_meanwhile", "not_read", "the_one_read"])
async def test_a_later_event_waits_for_an_earlier_one_kept_because_its_mark_changed_since_it_was_read(
        chat, redis, monkeypatch, path, mark_then):
    """CodeRabbit #288: the person's first message is in the inbox, its send's mark read as begun by a process that
    stopped (A), and A, back, clears it as one that sent nothing just before it is let go of: the first stays, its
    replies to go again from its answer. Their second, delivered by the gateway or met by the recovery, never goes on
    past it: it waits, and the recovery sends the first and then the second. The same while whether the first was let
    go of is not known (Redis failing). Rule 5: the mark still the one read, the first is let go of (maybe with the
    person, never sent again) and the second goes on."""
    _turns(chat, fail_first=2 if path == "recovered" else 1)
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))
    if path == "recovered":
        with pytest.raises(mc.EventFailed):
            await mc.handle(event(mid="m2", text="Second", timestamp=200))
    redis.kv[mc._sending_key("m1")] = "1.0:process-a"  # A's send, begun long ago, A not alive
    redis.kv["msgr:event:m1"] = json.dumps({"replies": [{"text": "About: First"}], "by": "api"})
    real_eval = redis.eval
    once = {"on": True}

    async def eval_(script, numkeys, *keys_and_args):
        if script == mc.RELEASE_ON_MARK_LUA and once["on"]:
            once["on"] = False
            if mark_then == "cleared_meanwhile":
                redis.kv.pop(mc._sending_key("m1"), None)  # A, back, clears it as one that sent nothing
            elif mark_then == "not_read":
                raise ResponseError("LOADING Redis is loading the dataset in memory")
        return await real_eval(script, numkeys, *keys_and_args)

    monkeypatch.setattr(redis, "eval", eval_)
    monkeypatch.setattr(mc, "RECOVER_AFTER_S", -1)  # old enough for the recovery
    gw = Gateway([])
    let_go = mark_then == "the_one_read"
    with patch.object(mc, "send", new=gw):
        if path == "delivered":
            if let_go:
                out = await mc.handle(event(mid="m2", text="Second", timestamp=200))
                assert [r["text"] for r in out["replies"]] == ["About: Second"]
            else:
                with pytest.raises(mc.StillAnswering):
                    await mc.handle(event(mid="m2", text="Second", timestamp=200))
                assert not [r for r in chat.rows if r["content"] == "About: Second"], "its turn never ran"
        else:
            first = await mc.run_messenger_recover()
            assert (first["answered"], first["waiting"]) == ((2, 0) if let_go else (0, 2))
        assert ("m1" in redis.kv[mc.INBOX]) is not let_go
        redis_client.set_client_for_tests(redis)  # a cooldown its failure began, over
        await mc.run_messenger_recover()
    if mark_then == "cleared_meanwhile":
        assert gw.sent == ["About: First", "About: Second"], "the first sent from its answer, then the second"
    elif path == "recovered":
        assert gw.sent == ["About: Second"], "the first let go of, never sent again"
    else:
        assert gw.sent == ([] if let_go else ["About: Second"])
    assert mc.MESSENGER_HEALTH["recover_unsure"] == (0 if mark_then == "cleared_meanwhile" else 1)
    assert not redis.kv[mc.INBOX]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer_then", ["gone", "there"])
async def test_a_send_this_process_knows_sent_nothing_is_cleared_with_its_answer_written_back(redis, answer_then):
    """CodeRabbit #288: a later event of the person's (or the recovery) looks at an earlier one whose send this process
    knows sent nothing, its mark left by a clear that failed, and the earlier one's answer went from Redis meanwhile:
    the mark is cleared with that answer written back, in one step, so the same replies go again from it, never from a
    second turn. Rule 5: an answer there is never written over."""
    k = "m1"
    mark = f"{time.time() - 1000}:{mc.BOOT}"
    replies = [{"text": "About: Hi"}]
    await redis.zadd(mc.INBOX, {k: 1.0})
    redis.kv[mc._sending_key(k)] = mark
    mc._UNSENT[k] = (mark, event().model_dump_json(), replies)
    theirs = json.dumps({"replies": [{"text": "Theirs"}], "by": "api"})
    if answer_then == "there":
        redis.kv["msgr:event:m1"] = theirs
    assert await mc._send_state(redis, k) == "none"
    assert mc._sending_key(k) not in redis.kv and not mc._UNSENT
    expected = {"replies": replies, "by": "api"} if answer_then == "gone" else json.loads(theirs)
    assert json.loads(redis.kv["msgr:event:m1"]) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("meta_time", ["later", "same"])
@pytest.mark.parametrize("later", ["taken_before", "delivered_during"])
async def test_a_late_send_never_goes_after_a_later_events_replies(chat, redis, monkeypatch, later, meta_time):
    """CodeRabbit #288, the person's order: as above, process B let go of an event whose send A knows sent nothing,
    A cut off from Redis. A later message of the person's taken before A is back (its replies maybe out already): A
    never sends the earlier replies after them (dropped, late_dropped), whatever Meta's times of the two (the same
    included: it is when the later one was taken that counts). One delivered while A's late send is on its way waits
    behind it (409): the event is back in the inbox, its mark a new one of A's, and the later one goes once the send
    is over, after it. Rule 5: with no later message, they go late, once
    (test_a_send_that_sent_nothing_goes_from_its_process_after_another_let_its_event_go)."""
    import asyncio

    await _fail_once(chat, redis)
    _turns(chat)
    reply = "About: Hi, two seats to Istanbul in December?"
    sending, go_on = asyncio.Event(), asyncio.Event()
    answers = [{"sent": 0, "failed": {"kind": "gateway"}}]
    sent: list = []

    async def gw(page_id, psid, messages, tag=None):
        sent.append(messages[0]["text"])
        if len(sent) == 2 and later == "delivered_during":  # A's late send, on its way
            sending.set()
            await go_on.wait()
        return answers.pop(0) if answers else {"sent": 1}

    def become(boot, knows):
        monkeypatch.setattr(mc, "BOOT", boot)
        mc._UNSENT.clear()
        mc._UNSENT.update(knows)

    second = event(mid="m2", text="Second", timestamp=200 if meta_time == "later" else 100)
    sending_s = mc.SENDING_S
    with patch.object(mc, "send", new=gw):
        blinks = _blink_send_marks(redis, monkeypatch)
        become("process-a", {})
        assert (await mc.run_messenger_recover())["waiting"] == 1
        monkeypatch.setattr(mc, "SENDING_S", -1.0)
        a_knows = mc._UNSENT.copy()
        redis.kv.pop("msgr:alive:process-a")  # A cut off from Redis past ALIVE_S
        blinks["on"] = False
        become("process-b", {})
        assert (await mc.run_messenger_recover())["answered"] == 1 and not redis.kv[mc.INBOX], "B let it go"
        monkeypatch.setattr(mc, "SENDING_S", sending_s)
        if later == "taken_before":
            out = await mc.handle(second)
            assert [r["text"] for r in out["replies"]] == ["About: Second"]
            become("process-a", a_knows)  # A reaches Redis again
            await mc.run_messenger_recover()
            assert sent == [reply], "never sent after the later one's"
            assert mc.MESSENGER_HEALTH["late_dropped"] == 1 and not mc._UNSENT and not redis.kv[mc.INBOX]
            return
        become("process-a", a_knows)  # A reaches Redis again
        late = asyncio.create_task(mc.run_messenger_recover())
        await asyncio.wait_for(sending.wait(), 1)
        become("process-b", {})
        try:
            with pytest.raises(mc.StillAnswering):
                await asyncio.wait_for(mc.handle(second), 1)  # behind the earlier one's late send
        finally:
            go_on.set()
        await asyncio.wait_for(late, 2)
        out = await mc.handle(second)
    assert sent == [reply, reply] and [r["text"] for r in out["replies"]] == ["About: Second"], "in order"
    assert mc.MESSENGER_HEALTH["sent_late"] == 1 and len(chat.requests) == 3
    assert "m1" not in redis.kv[mc.INBOX] and redis.kv[f"{mc.INBOX}:sending:m1"] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("putting_back", ["ran_its_answer_lost", "refused_before_it_ran"])
async def test_a_late_sends_putting_back_whose_answer_was_lost_sends_its_replies_once_at_the_next_minute(
        chat, redis, monkeypatch, putting_back):
    """CodeRabbit #288: as above, process B let go of an event whose send A knows sent nothing, A cut off from Redis.
    A, back, puts the event back in the inbox under a new mark (REKEEP_LUA), and Redis runs it and loses its answer:
    A holds the new mark beside the old one, and its next minute finds the new one there and sends the replies then,
    once, never from a second turn; meanwhile another process reads the new mark as on its way (A alive). Rule 5:
    Redis refusing the putting back before it ran, A's next minute tries it again from the old mark: the same
    replies, once."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    await _fail_once(chat, redis)
    _turns(chat)
    reply = "About: Hi, two seats to Istanbul in December?"
    gw = Gateway([{"sent": 0, "failed": {"kind": "gateway"}}])
    sending_s = mc.SENDING_S

    def become(boot, knows, putting):
        monkeypatch.setattr(mc, "BOOT", boot)
        mc._UNSENT.clear()
        mc._UNSENT.update(knows)
        mc._PUTTING_BACK.clear()
        mc._PUTTING_BACK.update(putting)

    with patch.object(mc, "send", new=gw):
        blinks = _blink_send_marks(redis, monkeypatch)
        become("process-a", {}, {})
        assert (await mc.run_messenger_recover())["waiting"] == 1
        monkeypatch.setattr(mc, "SENDING_S", -1.0)
        a_knows = mc._UNSENT.copy()
        redis.kv.pop("msgr:alive:process-a")  # A cut off from Redis past ALIVE_S
        blinks["on"] = False
        become("process-b", {}, {})
        assert (await mc.run_messenger_recover())["answered"] == 1 and not redis.kv[mc.INBOX], "B let it go"
        monkeypatch.setattr(mc, "SENDING_S", sending_s)
        real_eval = redis.eval
        once = {"on": True}

        async def eval_(script, numkeys, *keys_and_args):
            if script == mc.REKEEP_LUA and once["on"]:
                once["on"] = False
                if putting_back == "refused_before_it_ran":
                    raise ResponseError("LOADING Redis is loading the dataset in memory")
                await real_eval(script, numkeys, *keys_and_args)
                raise RedisConnectionError("Connection closed by server.")  # it ran: only its answer lost
            return await real_eval(script, numkeys, *keys_and_args)

        monkeypatch.setattr(redis, "eval", eval_)
        become("process-a", a_knows, {})  # A reaches Redis again
        await mc.run_messenger_recover()  # its minute: the late send, its putting back's answer lost
        assert gw.sent == [reply] and not once["on"], "nothing sent while the putting back's answer was lost"
        a_putting = dict(getattr(mc, "_PUTTING_BACK", {}))
        a_knows = mc._UNSENT.copy()
        redis_client.set_client_for_tests(redis)  # the cooldown its failure began, over
        if putting_back == "ran_its_answer_lost":
            assert "m1" in redis.kv[mc.INBOX], "back in the inbox under the new mark"
            become("process-b", {}, {})
            assert (await mc.run_messenger_recover())["waiting"] == 1, "the new mark on its way for another process"
            assert gw.sent == [reply]
        become("process-a", a_knows, a_putting)
        await mc.run_messenger_recover()  # its next minute
        await mc.run_messenger_recover()
    assert gw.sent == [reply, reply] and mc.MESSENGER_HEALTH["sent_late"] == 1 and len(chat.requests) == 2
    assert not mc._UNSENT and not mc._PUTTING_BACK and redis.kv[f"{mc.INBOX}:sending:m1"] == "done"
    assert "m1" not in redis.kv[mc.INBOX]


@pytest.mark.asyncio
@pytest.mark.parametrize("since", ["a_later_one_taken", "nothing_taken"])
async def test_a_late_sends_putting_back_whose_answer_was_lost_never_goes_after_a_later_events_replies(
        chat, redis, monkeypatch, since):
    """CodeRabbit #288: as above, A's putting back runs and its answer is lost (the event back in the inbox under A's
    new mark, nothing sent), and A is cut off again past ALIVE_S: process B takes the new mark for a send begun, lets
    the event go, and takes and answers the person's later message. A, back, finds its new mark there and sends late
    from it through the same check as from the old one (_send_late): an event of the person's taken since, they are
    never sent after its replies (late_dropped). Rule 5: nothing taken since, A sends them late, once."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    await _fail_once(chat, redis)
    _turns(chat)
    reply = "About: Hi, two seats to Istanbul in December?"
    gw = Gateway([{"sent": 0, "failed": {"kind": "gateway"}}])
    sending_s = mc.SENDING_S

    def become(boot, knows, putting):
        monkeypatch.setattr(mc, "BOOT", boot)
        mc._UNSENT.clear()
        mc._UNSENT.update(knows)
        mc._PUTTING_BACK.clear()
        mc._PUTTING_BACK.update(putting)

    async def b_lets_it_go():
        redis.kv.pop("msgr:alive:process-a")  # A cut off from Redis past ALIVE_S
        monkeypatch.setattr(mc, "SENDING_S", -1.0)
        become("process-b", {}, {})
        assert (await mc.run_messenger_recover())["answered"] == 1 and not redis.kv[mc.INBOX], "B let it go"
        monkeypatch.setattr(mc, "SENDING_S", sending_s)

    with patch.object(mc, "send", new=gw):
        blinks = _blink_send_marks(redis, monkeypatch)
        become("process-a", {}, {})
        assert (await mc.run_messenger_recover())["waiting"] == 1
        a_knows = mc._UNSENT.copy()
        blinks["on"] = False
        await b_lets_it_go()
        real_eval = redis.eval
        once = {"on": True}

        async def eval_(script, numkeys, *keys_and_args):
            if script == mc.REKEEP_LUA and once["on"]:
                once["on"] = False
                await real_eval(script, numkeys, *keys_and_args)
                raise RedisConnectionError("Connection closed by server.")  # it ran: only its answer lost
            return await real_eval(script, numkeys, *keys_and_args)

        monkeypatch.setattr(redis, "eval", eval_)
        become("process-a", a_knows, {})  # A reaches Redis again
        await mc.run_messenger_recover()  # its minute: the late send, its putting back's answer lost
        assert gw.sent == [reply] and "m1" in redis.kv[mc.INBOX], "back in the inbox under the new mark, nothing sent"
        a_knows, a_putting = mc._UNSENT.copy(), dict(mc._PUTTING_BACK)
        redis_client.set_client_for_tests(redis)  # the cooldown its failure began, over
        await b_lets_it_go()  # A cut off again: B takes the new mark for a send begun
        if since == "a_later_one_taken":
            out = await mc.handle(event(mid="m2", text="Second", timestamp=200))
            assert [r["text"] for r in out["replies"]] == ["About: Second"]
        become("process-a", a_knows, a_putting)  # A back, its new mark still there
        await mc.run_messenger_recover()
        await mc.run_messenger_recover()
    if since == "a_later_one_taken":
        assert gw.sent == [reply], "never sent after the later one's"
        assert mc.MESSENGER_HEALTH["late_dropped"] == 1 and mc.MESSENGER_HEALTH["sent_late"] == 0
    else:
        assert gw.sent == [reply, reply] and mc.MESSENGER_HEALTH["sent_late"] == 1
    assert not mc._UNSENT and not mc._PUTTING_BACK and not redis.kv[mc.INBOX]


@pytest.mark.asyncio
@pytest.mark.parametrize("since", ["a_later_one_taken", "nothing_taken"])
async def test_a_late_send_never_goes_after_a_later_events_replies_whatever_the_processes_clocks(chat, redis,
                                                                                                  monkeypatch, since):
    """CodeRabbit #288: as above, with the processes' clocks far apart: A's (the late send's) ten minutes ahead, B's
    (which lets the event go, then takes the person's later message and answers it) ten minutes behind. A send's mark
    and every take are stamped on Redis's clock (_redis_now), never on a process's: the later message, taken after
    A's send began, still stops it (late_dropped), and nothing that may have gone is sent again. Rule 5: nothing taken
    since, A sends them late, once, the clocks as far apart."""
    await _fail_once(chat, redis)
    _turns(chat)
    reply = "About: Hi, two seats to Istanbul in December?"
    gw = Gateway([{"sent": 0, "failed": {"kind": "gateway"}}])
    clock = _Clock()
    monkeypatch.setattr(mc, "time", clock)
    redis.wall = time.time  # Redis's own clock, whatever the processes' say

    def become(boot, knows, offset):
        monkeypatch.setattr(mc, "BOOT", boot)
        mc._UNSENT.clear()
        mc._UNSENT.update(knows)
        clock.offset = offset

    sending_s = mc.SENDING_S
    with patch.object(mc, "send", new=gw):
        blinks = _blink_send_marks(redis, monkeypatch)
        become("process-a", {}, 600.0)
        assert (await mc.run_messenger_recover())["waiting"] == 1
        a_knows = mc._UNSENT.copy()
        redis.kv.pop("msgr:alive:process-a")  # A cut off from Redis past ALIVE_S
        blinks["on"] = False
        # A's send over and its event old enough for B's minute, however far apart A's clock and B's are.
        monkeypatch.setattr(mc, "SENDING_S", -1500.0)
        redis.kv[mc.INBOX]["m1"] -= 1200
        become("process-b", {}, -600.0)
        assert (await mc.run_messenger_recover())["answered"] == 1 and not redis.kv[mc.INBOX], "B let it go"
        monkeypatch.setattr(mc, "SENDING_S", sending_s)
        if since == "a_later_one_taken":
            out = await mc.handle(event(mid="m2", text="Second", timestamp=200))
            assert [r["text"] for r in out["replies"]] == ["About: Second"]
        become("process-a", a_knows, 600.0)  # A reaches Redis again
        await mc.run_messenger_recover()
        await mc.run_messenger_recover()
    if since == "a_later_one_taken":
        assert gw.sent == [reply], "never sent after the later one's"
        assert mc.MESSENGER_HEALTH["late_dropped"] == 1 and mc.MESSENGER_HEALTH["sent_late"] == 0
    else:
        assert gw.sent == [reply, reply] and mc.MESSENGER_HEALTH["sent_late"] == 1
    assert not mc._UNSENT and not redis.kv[mc.INBOX]


@pytest.mark.asyncio
async def test_a_send_whose_end_could_not_be_written_is_let_go_of_by_its_process(chat, redis, monkeypatch):
    """CodeRabbit #288: a recovered send went out and neither its end ("done") nor the event's leaving the inbox
    could be written (Redis failing). Another process meeting its mark waits while this one is alive (it is a mark
    this one knows of), and this one's next minute lets go of the event, the lock its or not: the replies are never
    sent again."""
    await _fail_once(chat, redis)
    _turns(chat)
    reply = "About: Hi, two seats to Istanbul in December?"
    real_set, real_zrem = redis.set, redis.zrem
    failing = {"on": True}

    async def set_(key, value, *a, **kw):
        if failing["on"] and str(key).startswith(f"{mc.INBOX}:sending:") and value == "done":
            raise ConnectionError("the connection dropped")
        return await real_set(key, value, *a, **kw)

    async def zrem(key, *members):
        if failing["on"] and key == mc.INBOX:
            raise ConnectionError("the connection dropped")
        return await real_zrem(key, *members)

    def become(boot, ended):
        monkeypatch.setattr(mc, "BOOT", boot)
        mc._ENDED.clear()
        mc._ENDED.update(ended)

    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        monkeypatch.setattr(redis, "set", set_)
        monkeypatch.setattr(redis, "zrem", zrem)
        become("process-a", {})
        await mc.run_messenger_recover()
        assert gw.sent == [reply] and "m1" in redis.kv[mc.INBOX], "sent, and still in the inbox"
        a_ended = dict(mc._ENDED)
        assert a_ended, "this process knows its send is over"
        failing["on"] = False
        redis_client.set_client_for_tests(redis)  # the cooldown over
        monkeypatch.setattr(mc, "SENDING_S", -1.0)
        become("process-b", {})
        assert (await mc.run_messenger_recover())["waiting"] == 1, "its process alive: a mark it knows of"
        redis.kv[mc.RECOVER_LOCK] = "a-pass-of-process-b"
        become("process-a", a_ended)
        assert (await mc.run_messenger_recover())["state"] == "busy"
        redis.kv.pop(mc.RECOVER_LOCK)
    assert not redis.kv[mc.INBOX] and gw.sent == [reply] and mc.MESSENGER_HEALTH["recover_unsure"] == 0


@pytest.mark.asyncio
async def test_a_send_mark_whose_answer_was_lost_is_cleared_by_its_process_and_the_replies_go(chat, redis, monkeypatch):
    """CodeRabbit #288: a send's mark written (SET NX) and its answer lost: nothing was sent. This process takes it
    for a mark of a send that sent nothing: its next minute clears it, and the same replies go, once, with no second
    turn (never taken for a send begun, however long it waited)."""
    await _fail_once(chat, redis)
    _turns(chat)
    reply = "About: Hi, two seats to Istanbul in December?"
    real_set = redis.set
    lose = {"on": True}

    async def set_(key, value, *a, **kw):
        out = await real_set(key, value, *a, **kw)
        if lose["on"] and str(key).startswith(f"{mc.INBOX}:sending:") and kw.get("nx"):
            lose["on"] = False
            raise ConnectionError("the connection dropped before the answer")
        return out

    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        monkeypatch.setattr(redis, "set", set_)
        assert (await mc.run_messenger_recover())["state"] == "failed"
        assert f"{mc.INBOX}:sending:m1" in redis.kv and gw.sent == []
        redis_client.set_client_for_tests(redis)  # the cooldown over
        monkeypatch.setattr(mc, "SENDING_S", -1.0)
        out = await mc.run_messenger_recover()
    assert out["answered"] == 1 and gw.sent == [reply] and len(chat.requests) == 2
    assert not redis.kv[mc.INBOX] and mc.MESSENGER_HEALTH["recover_unsure"] == 0


@pytest.mark.asyncio
async def test_another_processs_send_mark_is_never_cleared_for_one_that_sent_nothing(chat, redis, monkeypatch):
    """CodeRabbit #288: this process's send never reached the gateway, and the clear of its mark applied on Redis but
    its answer was lost. Another process then begins a send of the same replies and stops in the middle of it. This
    process clears only the mark it wrote (compare and delete): the other's stays, is taken for a send begun once that
    process is gone (maybe with the person, counted), and the replies are never sent again."""
    import asyncio

    await _fail_once(chat, redis)
    _turns(chat)
    reply = "About: Hi, two seats to Istanbul in December?"
    calls: list = []
    hanging = asyncio.Event()

    async def gw(page_id, psid, messages, tag=None):
        calls.append(messages[0]["text"])
        if len(calls) == 1:
            return {"sent": 0, "failed": {"kind": "gateway"}}
        if len(calls) == 2:
            hanging.set()
            await asyncio.sleep(30)  # the other process stops in the middle of it
        return {"sent": 1}

    real_eval, real_delete = redis.eval, redis.delete
    lose = {"on": True}

    def sending(key) -> bool:
        return str(key).startswith(f"{mc.INBOX}:sending:")

    async def eval_(script, numkeys, *keys_and_args):
        out = await real_eval(script, numkeys, *keys_and_args)
        if lose["on"] and script == mc.RELEASE_LUA and sending(keys_and_args[0]):
            lose["on"] = False
            raise ConnectionError("the connection dropped before the answer")
        return out

    async def delete(*keys):
        out = await real_delete(*keys)
        if lose["on"] and any(sending(k) for k in keys):
            lose["on"] = False
            raise ConnectionError("the connection dropped before the answer")
        return out

    def become(boot, knows):
        monkeypatch.setattr(mc, "BOOT", boot)
        mc._UNSENT.clear()
        mc._UNSENT.update(knows)

    with patch.object(mc, "send", new=gw):
        monkeypatch.setattr(redis, "eval", eval_)
        monkeypatch.setattr(redis, "delete", delete)
        become("process-a", type(mc._UNSENT)())
        assert (await mc.run_messenger_recover())["waiting"] == 1
        assert f"{mc.INBOX}:sending:m1" not in redis.kv, "the clear applied, its answer lost"
        redis_client.set_client_for_tests(redis)  # the cooldown its failure began, over
        a_knows = mc._UNSENT.copy()
        assert a_knows, "this process takes its send for one that sent nothing"
        become("process-b", type(mc._UNSENT)())
        b = asyncio.create_task(mc.run_messenger_recover())
        await asyncio.wait_for(hanging.wait(), 1)
        b.cancel()
        await asyncio.gather(b, return_exceptions=True)
        assert f"{mc.INBOX}:sending:m1" in redis.kv, "the other's send begun, never over"
        redis.kv.pop("msgr:alive:process-b")  # and that process gone
        monkeypatch.setattr(mc, "SENDING_S", -1.0)
        become("process-a", a_knows)
        out = await mc.run_messenger_recover()
    assert out["answered"] == 1 and mc.MESSENGER_HEALTH["recover_unsure"] == 1
    assert calls == [reply, reply], "the other's send may be with the person: never made again"
    assert not redis.kv[mc.INBOX]


@pytest.mark.asyncio
async def test_an_event_that_keeps_failing_is_given_up_after_its_tries(chat, redis):
    await _fail_once(chat, redis)
    redis.kv[f"{mc.INBOX}:tries:m1"] = str(mc.RECOVER_TRIES)
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        out = await mc.run_messenger_recover()
    assert out["given_up"] == 1 and not redis.kv[mc.INBOX] and f"{mc.INBOX}:m1" not in redis.kv
    assert gw.sent == [] and mc.MESSENGER_HEALTH["recover_given_up"] == 1


@pytest.mark.asyncio
async def test_the_recovery_leaves_alone_what_is_recent_and_runs_only_when_on(chat, redis, monkeypatch):
    """Rule 5: an event kept a moment ago is still the gateway's to deliver again; off, nothing runs."""
    async def broken(req, add_message):
        raise RuntimeError("database away")

    chat.answer = broken
    with pytest.raises(mc.EventFailed):
        await mc.handle(event())
    with patch.object(mc, "send", new=Gateway([])):
        assert (await mc.run_messenger_recover())["state"] == "empty"
        monkeypatch.setenv("MESSENGER_CHANNEL", "false")
        assert (await mc.run_messenger_recover())["state"] == "off"
    assert "m1" in redis.kv[mc.INBOX]


def _turns(chat, fail_first: int = 0):
    """A turn that keeps the person's words and answers about them; the first `fail_first` fail (the database
    away)."""
    calls = {"n": 0}

    async def turn(req, add_message):
        calls["n"] += 1
        if calls["n"] <= fail_first:
            raise RuntimeError("database away")
        await add_message(req.conversation_id, "user", req.message)
        row = await add_message(req.conversation_id, "ai", f"About: {req.message}")
        return ChatResponse(conversation_id=req.conversation_id, message=row["content"], type="ai", model_used="x")

    chat.answer = turn
    return calls


@pytest.mark.asyncio
async def test_a_later_message_never_comes_before_an_earlier_one_whose_turn_failed(chat, redis):
    """The person's first message failed (kept in the inbox, CodeRabbit #288); their second, from the gateway, has
    the first answered and its replies sent before its own turn: in order, each once, nothing left for the
    recovery."""
    _turns(chat, fail_first=1)
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))
    gw = Gateway([])
    turn, at_second = chat.answer, []

    async def watched(req, add_message):
        if req.message == "Second":
            at_second.append(list(gw.sent))  # what had gone out when the second turn began
        return await turn(req, add_message)

    chat.answer = watched
    with patch.object(mc, "send", new=gw):
        out = await mc.handle(event(mid="m2", text="Second", timestamp=200))
        assert at_second == [["About: First"]], "the first reply went out before the second turn began"
        assert gw.sent == ["About: First"] and [r["text"] for r in out["replies"]] == ["About: Second"]
        assert [(r["role"], r["content"]) for r in chat.rows] == [
            ("user", "First"), ("ai", "About: First"), ("user", "Second"), ("ai", "About: Second")]
        assert not redis.kv[mc.INBOX] and mc.MESSENGER_HEALTH["answered_first"] == 1
        # The recovery finds nothing to do; a delivery made again of either gets nothing new.
        assert (await mc.run_messenger_recover())["answered"] == 0
        assert (await mc.handle(event(mid="m2", text="Second", timestamp=200)))["replies"] == out["replies"]
        assert (await mc.handle(event(mid="m1", text="First")))["replies"] == []
    assert gw.sent == ["About: First"]
    # Rule 5: another person's message does not wait for this person's.
    assert (await mc.handle(event(mid="q1", psid="PSID-8", text="Hello")))["replies"] == [{"text": "About: Hello"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("clocks", ["apart", "in_step"])
async def test_a_later_message_never_comes_before_an_earlier_one_whatever_the_processes_clocks(chat, redis,
                                                                                               monkeypatch, clocks):
    """CodeRabbit #288: the person's first message failed on process A (kept in the inbox), and the gateway delivers
    their second to process B, whose clock is twenty minutes behind A's. Each event's place among the person's is
    taken on Redis's clock (_redis_now), never on its process's: the first is still the earlier one, answered and
    sent before the second's turn. Rule 5: the clocks in step, the same."""
    clock = _Clock()
    monkeypatch.setattr(mc, "time", clock)
    redis.wall = time.time  # Redis's own clock, whatever the processes' say
    apart = 600.0 if clocks == "apart" else 0.0
    _turns(chat, fail_first=1)
    clock.offset = apart  # process A
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))
    clock.offset = -apart  # process B
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        out = await mc.handle(event(mid="m2", text="Second", timestamp=200))
    assert gw.sent == ["About: First"] and [r["text"] for r in out["replies"]] == ["About: Second"], "in order"
    assert not redis.kv[mc.INBOX] and mc.MESSENGER_HEALTH["answered_first"] == 1


@pytest.mark.asyncio
async def test_a_later_message_waits_while_an_earlier_one_is_still_being_answered_or_sent(chat, redis):
    _turns(chat, fail_first=1)
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))
    # The recovery is on m1 right now: m2 waits (409, the gateway tries again), and is not left marked pending.
    redis.kv["msgr:event:m1"] = "pending"
    with pytest.raises(mc.StillAnswering):
        await mc.handle(event(mid="m2", text="Second", timestamp=200))
    assert "msgr:event:m2" not in redis.kv and not [r for r in chat.rows if r["content"] == "Second"]
    # m1 answered by the recovery, its replies not sent yet (the gateway was down): m2 still waits for them.
    redis.kv["msgr:event:m1"] = json.dumps({"replies": [{"text": "About: First"}], "by": "api"})
    with pytest.raises(mc.StillAnswering):
        await mc.handle(event(mid="m2", text="Second", timestamp=200))
    # Their send on its way (begun a moment ago): m2 still waits.
    redis.kv[f"{mc.INBOX}:sending:m1"] = str(time.time())
    with pytest.raises(mc.StillAnswering):
        await mc.handle(event(mid="m2", text="Second", timestamp=200))
    # A send a stopped process began long ago: maybe out, never sent again; m1 leaves the inbox, and m2 goes on.
    redis.kv[f"{mc.INBOX}:sending:m1"] = str(time.time() - mc.SENDING_S - 1)
    with patch.object(mc, "send", new=Gateway([])) as gw:
        out = await mc.handle(event(mid="m2", text="Second", timestamp=200))
    assert [r["text"] for r in out["replies"]] == ["About: Second"] and gw.sent == []
    assert mc.MESSENGER_HEALTH["recover_unsure"] == 1 and "m1" not in redis.kv[mc.INBOX]


@pytest.mark.asyncio
async def test_a_later_message_waits_while_the_gateway_is_sending_an_earlier_ones_replies(chat, redis, monkeypatch):
    """CodeRabbit #288: the person's first and second messages failed (both kept). The gateway delivers the first
    again and its replies go back to it: the gateway takes the right to send them, and may still be sending them. The
    first stays in the inbox, the second behind it: the recovery sends nothing of the second, and a delivery of it
    from the gateway waits (409), until the gateway's send is over (SENDING_S). Then the first is let go of, never
    sent by the API, and the second goes. Rule 5: with no later message waiting, a delivery from the gateway lets go
    of its event at once (test_a_later_message_never_comes_before_an_earlier_one_whose_turn_failed)."""
    _turns(chat, fail_first=2)
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m2", text="Second", timestamp=200))
    assert set(redis.kv[mc.INBOX]) == {"m1", "m2"}
    delivered = await mc.handle(event(mid="m1", text="First"))  # the gateway's, again
    assert [r["text"] for r in delivered["replies"]] == ["About: First"]
    assert set(redis.kv[mc.INBOX]) == {"m1", "m2"}, "the first stays while the gateway may be sending its replies"
    monkeypatch.setattr(mc, "RECOVER_AFTER_S", -1)  # both old enough for the recovery (their places kept as taken)
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        out = await mc.run_messenger_recover()
        assert out["waiting"] == 2 and gw.sent == [], "nothing of the second while the first may be on its way"
        with pytest.raises(mc.StillAnswering):
            await mc.handle(event(mid="m2", text="Second", timestamp=200))
        monkeypatch.setattr(mc, "GATEWAY_SENDING_S", -1.0)  # the gateway's send over
        out = await mc.run_messenger_recover()
    assert out["answered"] == 2 and gw.sent == ["About: Second"] and not redis.kv[mc.INBOX]
    assert len(chat.requests) == 4, "two failed turns, then one turn each"


async def _given_with_a_later_one_waiting(chat, redis) -> None:
    """The person's first and second messages failed (both kept); the gateway delivers the first again and its replies
    go back to it, the second waiting behind it."""
    _turns(chat, fail_first=2)
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m2", text="Second", timestamp=200))
    delivered = await mc.handle(event(mid="m1", text="First"))
    assert [r["text"] for r in delivered["replies"]] == ["About: First"]
    assert set(redis.kv[mc.INBOX]) == {"m1", "m2"} and redis.kv[mc._sending_key("m1")].startswith("gateway:")


@pytest.mark.asyncio
@pytest.mark.parametrize("given", ["100_s_ago", "past_the_window"])
async def test_the_gateways_right_covers_its_call_to_the_api_and_its_send_after(chat, redis, given):
    """CodeRabbit #288: the gateway gets a delivery's answer within its own call to the API (75 s), begun before the
    right to send its replies was taken, and sends them within 60 s after: a later event of the person's waits for
    the right that long (GATEWAY_SENDING_S), past the API's own SENDING_S (an answer that reached the gateway 40 s
    late is still being sent 90 s after the right). Rule 5: past it, the first is let go of, never sent by the API,
    and the second goes."""
    await _given_with_a_later_one_waiting(chat, redis)
    at = time.time() - (100 if given == "100_s_ago" else getattr(mc, "GATEWAY_SENDING_S", mc.SENDING_S) + 1)
    redis.kv[mc._sending_key("m1")] = f"gateway:{at}"
    if given == "100_s_ago":
        with pytest.raises(mc.StillAnswering):
            await mc.handle(event(mid="m2", text="Second", timestamp=200))  # the gateway may still be sending them
        assert set(redis.kv[mc.INBOX]) == {"m1", "m2"} and len(chat.requests) == 3
        return
    out = await mc.handle(event(mid="m2", text="Second", timestamp=200))
    assert [r["text"] for r in out["replies"]] == ["About: Second"] and not redis.kv[mc.INBOX]


class _Clock:
    """messenger_channel's time, moved by `offset` seconds (the rest of the time module as it is)."""

    def __init__(self):
        self.offset = 0.0

    def time(self) -> float:
        return time.time() + self.offset

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.mark.asyncio
async def test_a_giving_redis_did_not_answer_is_stamped_when_it_is_made_again_never_at_its_first_try(chat, redis,
                                                                                                    monkeypatch):
    """CodeRabbit #288: the person's first and second messages failed (both kept); the gateway delivers the first
    again, and Redis refuses the giving of its replies before it runs, long ago now: none given (409), the giving held.
    Its process makes it again at its next minute: the right is stamped then, never with its first try's time, so the
    recovery reads it as on its way (the gateway gets the replies only when it delivers the event again) and the
    second waits; the gateway's delivery made again gets the replies, the right taken again from then, and the second
    goes once that send is over. Rule 5: a giving Redis answers at once is stamped then
    (test_a_later_message_waits_while_the_gateway_is_sending_an_earlier_ones_replies)."""
    _turns(chat, fail_first=2)
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m2", text="Second", timestamp=200))
    clock = _Clock()
    monkeypatch.setattr(mc, "time", clock)
    clock.offset = -(getattr(mc, "GATEWAY_SENDING_S", mc.SENDING_S) + 60)  # its first try, long ago
    refuse = _refuse_answers(redis, monkeypatch, "msgr:event:m1")
    with pytest.raises(mc.StillAnswering):
        await mc.handle(event(mid="m1", text="First"))  # the gateway's, again: its giving refused before it ran
    assert not refuse["on"] and mc._UNGIVEN, "held"
    clock.offset = 0.0
    redis_client.set_client_for_tests(redis)  # the cooldown its failure began, over
    monkeypatch.setattr(mc, "RECOVER_AFTER_S", -1)  # both old enough for the recovery
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        out = await mc.run_messenger_recover()  # its minute: the giving made again, then the pass
        assert out["waiting"] == 2 and gw.sent == [], "the first's right on its way from now: the second waits"
        again = await mc.handle(event(mid="m1", text="First"))  # the gateway's, again: it gets them now
        assert [r["text"] for r in again["replies"]] == ["About: First"]
        monkeypatch.setattr(mc, "GATEWAY_SENDING_S", -1.0)  # the gateway's send over
        out = await mc.run_messenger_recover()
    assert gw.sent == ["About: Second"] and not redis.kv[mc.INBOX] and len(chat.requests) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("since", ["kept", "let_go_nothing_taken", "let_go_a_later_one_taken"])
async def test_replies_given_to_the_gateway_and_delivered_again_are_given_from_then_never_after_a_later_ones(
        chat, redis, since):
    """CodeRabbit #288: the gateway never got a delivery's answer (or stopped before sending its replies) and delivers
    the event again, later than the right's window may allow: it gets the same replies, no second turn, and sends them
    from then. The event still kept (a later one of the person's waiting behind it): the right is taken again from
    then, so the later one waits for that send. The event let go of since and a later event of the person's taken
    after the right (its replies may be out already): none given, never after them (late_dropped). Rule 5: let go of,
    nothing taken since, the same replies (the gateway sends the person's events one after another)."""
    if since == "kept":
        await _given_with_a_later_one_waiting(chat, redis)
        old = time.time() - 140  # near the end of the right's window
        redis.kv[mc._sending_key("m1")] = f"gateway:{old}"
    else:
        _turns(chat)
        first = await mc.handle(event(mid="m1", text="First"))  # given, and let go of: nothing waits behind it
        assert [r["text"] for r in first["replies"]] == ["About: First"] and not redis.kv[mc.INBOX]
        if since == "let_go_a_later_one_taken":
            await mc.handle(event(mid="m2", text="Second", timestamp=200))
    turns = len(chat.requests)
    again = await mc.handle(event(mid="m1", text="First"))  # its first answer never reached the gateway
    assert len(chat.requests) == turns, "no second turn"
    if since == "let_go_a_later_one_taken":
        assert again["replies"] == [] and mc.MESSENGER_HEALTH["late_dropped"] == 1
        return
    assert [r["text"] for r in again["replies"]] == ["About: First"] and mc.MESSENGER_HEALTH["late_dropped"] == 0
    if since == "kept":
        right = redis.kv[mc._sending_key("m1")]
        assert float(right[8:]) > old + 100, "taken again from now"
        with pytest.raises(mc.StillAnswering):
            await mc.handle(event(mid="m2", text="Second", timestamp=200))  # behind the gateway's send from now


@pytest.mark.asyncio
@pytest.mark.parametrize("since", ["a_later_one_taken", "nothing_taken"])
async def test_replies_delivered_again_are_never_given_after_a_later_ones_whatever_the_processes_clocks(
        chat, redis, monkeypatch, since):
    """CodeRabbit #288: as above, the first given to the gateway by process A, whose clock is ten minutes ahead, and
    let go of (nothing waits behind it); process B, ten minutes behind, takes the person's later message and gives its
    replies. The gateway delivers the first again: the right and the takes are stamped on Redis's clock
    (_redis_now), so the later one is still found taken after the right, and the first's replies are never given after
    its (late_dropped). Rule 5: nothing taken since, the same replies, the clocks as far apart."""
    clock = _Clock()
    monkeypatch.setattr(mc, "time", clock)
    redis.wall = time.time  # Redis's own clock, whatever the processes' say
    _turns(chat)
    clock.offset = 600.0  # process A
    first = await mc.handle(event(mid="m1", text="First"))  # given, and let go of: nothing waits behind it
    assert [r["text"] for r in first["replies"]] == ["About: First"] and not redis.kv[mc.INBOX]
    clock.offset = -600.0  # process B
    if since == "a_later_one_taken":
        assert [r["text"] for r in (await mc.handle(event(mid="m2", text="Second", timestamp=200)))["replies"]] == [
            "About: Second"]
    turns = len(chat.requests)
    again = await mc.handle(event(mid="m1", text="First"))  # its first answer never reached the gateway
    assert len(chat.requests) == turns, "no second turn"
    if since == "a_later_one_taken":
        assert again["replies"] == [] and mc.MESSENGER_HEALTH["late_dropped"] == 1
        return
    assert [r["text"] for r in again["replies"]] == ["About: First"] and mc.MESSENGER_HEALTH["late_dropped"] == 0


@pytest.mark.asyncio
async def test_an_event_whose_later_ones_could_not_be_looked_up_stays_until_the_gateways_send_is_over(chat, redis,
                                                                                                 monkeypatch):
    """CodeRabbit #288: as above, the first delivered again and given to the gateway while the second waits, and the
    look for a later one of the person's fails (Redis): never taken for "none waiting", the first stays in the inbox,
    so the second still waits for the gateway's send of its replies. Rule 5: a look that answers "none" lets the first
    go at once (test_a_later_message_never_comes_before_an_earlier_one_whose_turn_failed)."""
    _turns(chat, fail_first=2)
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m2", text="Second", timestamp=200))
    real_range = redis.zrangebyscore

    async def later_not_read(key, lo, hi, *a, **kw):
        if hi == "+inf" and lo != "-inf":  # the look for a later one only (_later_kept)
            raise ResponseError("LOADING Redis is loading the dataset in memory")
        return await real_range(key, lo, hi, *a, **kw)

    monkeypatch.setattr(redis, "zrangebyscore", later_not_read)
    delivered = await mc.handle(event(mid="m1", text="First"))  # the gateway's, again
    assert [r["text"] for r in delivered["replies"]] == ["About: First"]
    assert set(redis.kv[mc.INBOX]) == {"m1", "m2"}, "kept: its later ones could not be looked up"
    monkeypatch.setattr(redis, "zrangebyscore", real_range)
    redis_client.set_client_for_tests(redis)  # the cooldown its failure began, over
    with pytest.raises(mc.StillAnswering):
        await mc.handle(event(mid="m2", text="Second", timestamp=200))  # behind the gateway's send of the first


@pytest.mark.asyncio
async def test_an_earlier_event_that_could_not_be_read_keeps_the_later_one_behind_it(chat, redis, monkeypatch):
    """CodeRabbit #288: m1 is in the inbox and its payload's read fails: m2 is never answered past it (it fails,
    kept, and its turn does not run); once Redis answers, m1 goes first. Rule 5: one there and unreadable is left to
    the recovery, and m2 goes on."""
    _turns(chat, fail_first=1)
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))
    real_get = redis.get

    async def blinks(key):
        if key == f"{mc.INBOX}:m1":
            raise ResponseError("LOADING Redis is loading the dataset in memory")
        return await real_get(key)

    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        monkeypatch.setattr(redis, "get", blinks)
        with pytest.raises(mc.EventFailed) as failed:
            await mc.handle(event(mid="m2", text="Second", timestamp=200))
        assert failed.value.kept and not [r for r in chat.rows if r["content"] == "Second"] and gw.sent == []
        monkeypatch.setattr(redis, "get", real_get)
        out = await mc.handle(event(mid="m2", text="Second", timestamp=200))
        assert gw.sent == ["About: First"] and [r["text"] for r in out["replies"]] == ["About: Second"]
        # Rule 5: m3 there and unreadable (not Redis failing): m4 goes on, m3 is the recovery's to give up.
        _turns(chat, fail_first=1)
        with pytest.raises(mc.EventFailed):
            await mc.handle(event(mid="m3", text="Third", timestamp=300))
        redis.kv[f"{mc.INBOX}:m3"] = "not an event"
        out = await mc.handle(event(mid="m4", text="Fourth", timestamp=400))
    assert [r["text"] for r in out["replies"]] == ["About: Fourth"] and set(redis.kv[mc.INBOX]) == {"m3"}


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["kept", "found", "down"])
async def test_a_cooldown_after_the_later_event_is_kept_never_lets_it_past_an_earlier_one(chat, redis, monkeypatch,
                                                                                         when):
    """CodeRabbit #288: another call's failure starts Redis's cooldown (process wide) right after m2 is kept, or right
    after m1 is found before it: m1 is still answered and sent first, then m2 (the inbox looked at all the same), and
    both are written done. Rule 5: Redis really failing then, m2 fails, kept, its turn not run."""
    _turns(chat, fail_first=1)
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))

    def cool():
        if when == "down":
            redis.down = True
        else:
            redis_client.redis_failed("another call", ConnectionError("reset by peer"))

    real_keep, real_found = mc._keep_event, mc._earlier_kept

    async def kept_then(ev, client=None):
        out = await real_keep(ev, client)
        if ev.mid == "m2" and when in ("kept", "down"):
            cool()
        return out

    async def found_then(*a):
        out = await real_found(*a)
        if when == "found":
            cool()
        return out

    monkeypatch.setattr(mc, "_keep_event", kept_then)
    monkeypatch.setattr(mc, "_earlier_kept", found_then)
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        if when == "down":
            with pytest.raises(mc.EventFailed) as failed:
                await mc.handle(event(mid="m2", text="Second", timestamp=200))
            assert failed.value.kept and gw.sent == [] and not [r for r in chat.rows if r["content"] == "Second"]
            return
        out = await mc.handle(event(mid="m2", text="Second", timestamp=200))
    assert redis_client.get_redis() is None, "the cooldown is on"
    assert gw.sent == ["About: First"] and [r["text"] for r in out["replies"]] == ["About: Second"]
    assert [(r["role"], r["content"]) for r in chat.rows] == [
        ("user", "First"), ("ai", "About: First"), ("user", "Second"), ("ai", "About: Second")]
    assert not redis.kv[mc.INBOX] and json.loads(redis.kv["msgr:event:m2"])["by"] == "gateway"


@pytest.mark.asyncio
async def test_an_event_answered_in_a_cooldown_is_never_answered_again_by_the_recovery(chat, redis):
    """A cooldown begun during the turn (another call's failure, process wide, while Redis answers): the event's mark
    and its place in the inbox are written all the same, so the recovery finds nothing left and never sends the person
    a second answer."""
    async def turn(req, add_message):
        redis_client.redis_failed("another call", ConnectionError("reset by peer"))
        await add_message(req.conversation_id, "user", req.message)
        row = await add_message(req.conversation_id, "ai", f"About: {req.message}")
        return ChatResponse(conversation_id=req.conversation_id, message=row["content"], type="ai", model_used="x")

    chat.answer = turn
    out = await mc.handle(event())
    assert not redis.kv[mc.INBOX]
    assert json.loads(redis.kv["msgr:event:m1"]) == {"replies": out["replies"], "by": "gateway"}
    redis_client.set_client_for_tests(redis)  # the cooldown over
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        assert (await mc.run_messenger_recover())["answered"] == 0
        # Delivered again by the gateway: the same replies, no second turn.
        assert (await mc.handle(event()))["replies"] == out["replies"]
    assert gw.sent == [] and len(chat.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("then", ["cooling", "down"])
async def test_a_cooldown_begun_during_a_recovery_pass_never_answers_its_event_again(chat, redis, monkeypatch, then):
    """CodeRabbit #288: the first recovery's send never reached the gateway (its replies kept). In the next pass,
    another call's failure starts Redis's cooldown (process wide) after the pass took its client: the pass reads the
    event's mark through that client, finds the replies kept and sends them, with no second turn. Rule 5: Redis
    really failing then, the event is left for a later pass, kept, its turn not run again; once Redis answers, the
    kept replies go."""
    await _fail_once(chat, redis)
    _turns(chat)
    reply = "About: Hi, two seats to Istanbul in December?"
    gw = Gateway([{"sent": 0, "failed": {"kind": "gateway"}}])
    real_handle = mc.handle

    async def after_the_client(ev, **kw):
        if then == "cooling":
            redis_client.redis_failed("another call", ConnectionError("reset by peer"))
        else:
            redis.down = True
        return await real_handle(ev, **kw)

    with patch.object(mc, "send", new=gw):
        assert (await mc.run_messenger_recover())["waiting"] == 1  # its send never reached the gateway
        monkeypatch.setattr(mc, "handle", after_the_client)
        out = await mc.run_messenger_recover()
        monkeypatch.setattr(mc, "handle", real_handle)
        redis.down = False
        redis_client.set_client_for_tests(redis)  # the cooldown over
        if then == "down":
            assert out["state"] == "failed" and "m1" in redis.kv[mc.INBOX] and gw.sent == [reply]
            redis.kv.pop(mc.RECOVER_LOCK, None)  # not let go while Redis failed: it expires
            out = await mc.run_messenger_recover()
    assert out["answered"] == 1 and gw.sent == [reply, reply] and not redis.kv[mc.INBOX]
    assert len(chat.requests) == 2, "one turn for the failure, one after it: never a third"


@pytest.mark.asyncio
async def test_an_earlier_answer_is_sent_even_when_the_later_turn_then_fails(chat, redis):
    """CodeRabbit #288: m1 answered on m2's delivery, then m2's own turn fails. m1's replies went out before m2's
    turn, once, and are never lost with m2's failure; m2, delivered again, gets its own."""
    calls = {"n": 0}
    gw = Gateway([])
    at_second: list = []

    async def turn(req, add_message):
        calls["n"] += 1
        if req.message == "Second":
            at_second.append(list(gw.sent))  # what had gone out when m2's turn began
        if calls["n"] in (1, 3):  # m1's first try, then m2's first
            raise RuntimeError("database away")
        await add_message(req.conversation_id, "user", req.message)
        row = await add_message(req.conversation_id, "ai", f"About: {req.message}")
        return ChatResponse(conversation_id=req.conversation_id, message=row["content"], type="ai", model_used="x")

    chat.answer = turn
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))
    with patch.object(mc, "send", new=gw):
        with pytest.raises(mc.EventFailed) as failed:
            await mc.handle(event(mid="m2", text="Second", timestamp=200))
        assert at_second == [["About: First"]], "m1's reply went out before m2's turn began, not after it failed"
        assert failed.value.kept and gw.sent == ["About: First"] and set(redis.kv[mc.INBOX]) == {"m2"}
        out = await mc.handle(event(mid="m2", text="Second", timestamp=200))
        assert (await mc.run_messenger_recover())["answered"] == 0
    assert [r["text"] for r in out["replies"]] == ["About: Second"] and gw.sent == ["About: First"]
    assert calls["n"] == 4 and not redis.kv[mc.INBOX], "m1's turn once, never again for m2's sake"


@pytest.mark.asyncio
async def test_an_earlier_answer_the_gateway_could_not_take_goes_with_the_recovery_and_the_later_waits(chat, redis):
    """Rule 5: m1's replies never reached the gateway (nothing went out): kept as the recovery's to send, m2 waits
    behind them; the recovery sends them without a second turn, then m2 goes on."""
    calls = _turns(chat, fail_first=1)
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))
    gw = Gateway([{"sent": 0, "failed": {"kind": "gateway"}}])
    with patch.object(mc, "send", new=gw):
        with pytest.raises(mc.StillAnswering):
            await mc.handle(event(mid="m2", text="Second", timestamp=200))
        assert set(redis.kv[mc.INBOX]) == {"m1", "m2"} and f"{mc.INBOX}:sending:m1" not in redis.kv
        redis.kv[mc.INBOX]["m1"] -= mc.RECOVER_AFTER_S + 1
        assert (await mc.run_messenger_recover())["answered"] == 1
        out = await mc.handle(event(mid="m2", text="Second", timestamp=200))
    # The first call never reached the gateway; the recovery's did.
    assert gw.sent == ["About: First", "About: First"] and [r["text"] for r in out["replies"]] == ["About: Second"]
    assert calls["n"] == 3 and not redis.kv[mc.INBOX]


@pytest.mark.asyncio
async def test_an_earlier_message_failing_again_keeps_the_later_one_behind_it_until_both_are_answered(chat, redis):
    calls = _turns(chat, fail_first=3)
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="m1", text="First"))
    with pytest.raises(mc.EventFailed) as failed:
        await mc.handle(event(mid="m2", text="Second", timestamp=200))
    assert failed.value.kept and set(redis.kv[mc.INBOX]) == {"m1", "m2"} and chat.rows == []
    for k in ("m1", "m2"):
        redis.kv[mc.INBOX][k] -= mc.RECOVER_AFTER_S + 1
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        # m1 fails again: m2 waits behind it this minute, not even tried.
        first = await mc.run_messenger_recover()
        assert first["waiting"] == 2 and calls["n"] == 3 and gw.sent == []
        out = await mc.run_messenger_recover()
    # In order, each once: m1's turn, then m2's, never a second try of m1 for m2's sake.
    assert out["answered"] == 2 and gw.sent == ["About: First", "About: Second"] and calls["n"] == 5
    assert not redis.kv[mc.INBOX]


@pytest.mark.asyncio
async def test_a_recovered_answer_the_gateway_may_have_sent_is_never_sent_again(chat, redis):
    """The gateway had the call and its answer never came ("unknown"): answered, never sent a second time; only
    one that never reached it ("gateway") goes again the next minute."""
    await _fail_once(chat, redis)
    _turns(chat)
    gw = Gateway([{"sent": 0, "failed": {"kind": "unknown"}}])
    with patch.object(mc, "send", new=gw):
        assert (await mc.run_messenger_recover())["answered"] == 1
        assert (await mc.run_messenger_recover())["state"] == "empty"
    assert gw.sent == ["About: Hi, two seats to Istanbul in December?"] and not redis.kv[mc.INBOX]


@pytest.mark.asyncio
async def test_recovered_replies_sent_and_left_in_the_inbox_are_never_sent_again(chat, redis, monkeypatch):
    """CodeRabbit #288: the replies sent, then the inbox not cleared (the process stopped right after, or Redis
    failed): the next minute takes the event out of the inbox and sends nothing."""
    await _fail_once(chat, redis)
    _turns(chat)
    real = mc._release_event
    tries = {"n": 0}

    async def not_cleared_once(k, who=None):
        tries["n"] += 1
        if tries["n"] > 1:
            await real(k, who)

    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        monkeypatch.setattr(mc, "_release_event", not_cleared_once)
        assert (await mc.run_messenger_recover())["answered"] == 1
        assert "m1" in redis.kv[mc.INBOX] and redis.kv[f"{mc.INBOX}:sending:m1"] == "done"
        assert (await mc.run_messenger_recover())["answered"] == 1
    assert gw.sent == ["About: Hi, two seats to Istanbul in December?"] and not redis.kv[mc.INBOX]
    # Its send's mark stays until it expires: nothing takes a send of them again.
    assert mc.MESSENGER_HEALTH["recover_unsure"] == 0 and redis.kv[f"{mc.INBOX}:sending:m1"] == "done"


@pytest.mark.asyncio
async def test_replies_a_stopped_process_was_sending_are_never_sent_again(chat, redis):
    """A process stopped in the middle of the send (begun, never over): maybe with the person, never sent again
    (counted). Rule 5: begun a moment ago, its process may still be at it: waits, still in the inbox."""
    await _fail_once(chat, redis)
    redis.kv["msgr:event:m1"] = json.dumps({"replies": [{"text": "Sorry for the wait!"}], "by": "api"})
    redis.kv[f"{mc.INBOX}:sending:m1"] = str(time.time() - 5)
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        assert (await mc.run_messenger_recover())["waiting"] == 1 and "m1" in redis.kv[mc.INBOX]
        redis.kv[f"{mc.INBOX}:sending:m1"] = str(time.time() - mc.SENDING_S - 1)
        assert (await mc.run_messenger_recover())["answered"] == 1
    assert gw.sent == [] and not redis.kv[mc.INBOX] and mc.MESSENGER_HEALTH["recover_unsure"] == 1
    assert len(chat.requests) == 1, "no second turn"


@pytest.mark.asyncio
async def test_a_pass_lets_go_only_of_its_own_lock_and_starts_nothing_past_its_budget(chat, redis, monkeypatch):
    """CodeRabbit #288: a pass that outlived its lock (another took it after it expired) leaves that other one's
    alone when it lets go; past its budget it starts no event (the next minute does). Rule 5: its own lock goes, and
    with time the event is answered."""
    await _fail_once(chat, redis)
    _turns(chat)
    real_tell_left = mc._tell_left

    async def outlived(r, until):
        redis.kv[mc.RECOVER_LOCK] = "another-pass"
        return 0

    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        monkeypatch.setattr(mc, "_tell_left", outlived)
        monkeypatch.setattr(mc, "RECOVER_BUDGET_S", 0.0)
        out = await mc.run_messenger_recover()
        assert redis.kv[mc.RECOVER_LOCK] == "another-pass" and out["answered"] == 0 and "m1" in redis.kv[mc.INBOX]
        redis.kv.pop(mc.RECOVER_LOCK)
        monkeypatch.setattr(mc, "_tell_left", real_tell_left)
        monkeypatch.setattr(mc, "RECOVER_BUDGET_S", 120.0)
        out = await mc.run_messenger_recover()
    assert out["answered"] == 1 and mc.RECOVER_LOCK not in redis.kv and gw.sent == [
        "About: Hi, two seats to Istanbul in December?"]


@pytest.mark.asyncio
async def test_the_agents_notes_are_told_a_part_at_a_time_and_the_events_still_get_theirs(chat, redis, monkeypatch):
    """CodeRabbit #288: a database slow for the notes never keeps the events waiting: each pass tells TELL_BATCH of
    them at most, within TELL_BUDGET_S, and goes on from where the last one stopped; the inbox's event is answered in
    the same pass, even when the notes get no time at all."""
    await _fail_once(chat, redis)
    _turns(chat)
    # The first part (two deliveries a live process is on) stays: a pass that started over at cursor 0 every time
    # would never reach the notes after it.
    redis.kv["msgr:alive:alive-one"] = "1"
    stays = {f"a{i}": json.dumps({"cid": CONV, "boot": "alive-one", "state": "waiting", "at": time.time()})
             for i in range(2)}
    notes = {f"e{i}": json.dumps({"cid": CONV, "boot": "gone-one", "state": "untold", "note": f"Note {i}",
                                  "at": time.time()}) for i in range(5)}
    redis.kv[mc.OUTBOX] = {**stays, **notes}
    monkeypatch.setattr(mc, "TELL_BATCH", 2)
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        monkeypatch.setattr(mc, "TELL_BUDGET_S", 0.0)
        first = await mc.run_messenger_recover()
        assert first["told"] == 0 and first["answered"] == 1 and len(await redis.hgetall(mc.OUTBOX)) == 7
        monkeypatch.setattr(mc, "TELL_BUDGET_S", 30.0)
        assert (await mc.run_messenger_recover())["told"] == 0 and redis.kv[mc.OUTBOX_CURSOR] == "2"
        # The next pass goes on from the cursor the last one kept: the second part's notes.
        assert (await mc.run_messenger_recover())["told"] == 2
        assert sorted(r["content"] for r in chat.rows if r["role"] == "system") == ["Note 0", "Note 1"]
        passes = [(await mc.run_messenger_recover())["told"] for _ in range(2)]
    assert passes == [2, 1] and set(await redis.hgetall(mc.OUTBOX)) == set(stays)
    assert sorted(r["content"] for r in chat.rows if r["role"] == "system") == [f"Note {i}" for i in range(5)]


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", [False, True])
async def test_a_pass_keeps_its_lock_while_a_slow_event_runs_and_starts_nothing_once_it_is_lost(chat, redis,
                                                                                               monkeypatch, lost):
    """CodeRabbit #288: an event that takes longer than the lock's own time: the pass renews its lock while it runs,
    so a pass that starts meanwhile finds it busy, and both events are answered by the one pass. Rule 5: the lock
    found lost (another pass holds it now), the pass starts no other event and leaves that pass's lock alone. The
    lock's time runs on a clock the test moves (FakeClock), only after a renewal: never on the machine's speed."""
    import asyncio

    from tests.fake_redis import FakeClock

    await _fail_once(chat, redis)
    redis.kv[mc.INBOX]["m1"] -= 1  # the oldest
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="q1", psid="PSID-8", text="Hello"))
    redis.kv[mc.INBOX]["q1"] -= mc.RECOVER_AFTER_S + 1
    clock = FakeClock()
    redis.clock = clock
    entered, go_on, renewed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    renewals: list = []  # each renewal of the lock: 1 renewed, 0 found another's token
    real_eval = redis.eval

    async def watched(script, numkeys, *keys_and_args):
        out = await real_eval(script, numkeys, *keys_and_args)
        if script == mc.RENEW_LUA and keys_and_args[0] == mc.RECOVER_LOCK:
            renewals.append(out)
            renewed.set()
        return out

    async def slow(ev, *, by="gateway", client=None):
        if ev.mid == "m1":
            entered.set()
            await go_on.wait()
        return {"replies": []}

    monkeypatch.setattr(redis, "eval", watched)
    monkeypatch.setattr(mc, "RECOVER_LOCK_S", 0.3)
    monkeypatch.setattr(mc, "handle", slow)
    first = asyncio.create_task(mc.run_messenger_recover())
    await asyncio.wait_for(entered.wait(), 1)  # m1 on its way
    if lost:
        await redis.set(mc.RECOVER_LOCK, "another-pass", ex=60)
        while not renewals or renewals[-1] != 0:  # the pass's keeper has found another's token
            renewed.clear()
            await asyncio.wait_for(renewed.wait(), 2)
    else:
        # Twice the lock's own time, in steps shorter than it, each after a renewal.
        for _ in range(4):
            renewed.clear()
            await asyncio.wait_for(renewed.wait(), 2)
            clock.advance(mc.RECOVER_LOCK_S / 2)
    second = await mc.run_messenger_recover()
    go_on.set()
    out = await asyncio.wait_for(first, 2)
    assert second["state"] == "busy"
    if lost:
        assert out["answered"] == 1 and set(redis.kv[mc.INBOX]) == {"q1"}
        assert redis.kv[mc.RECOVER_LOCK] == "another-pass"
    else:
        assert all(renewals) and out["answered"] == 2 and not redis.kv[mc.INBOX] and mc.RECOVER_LOCK not in redis.kv


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["kept", "lost"])
async def test_an_event_whose_pass_lost_its_lock_mid_turn_is_sent_at_most_once(chat, redis, monkeypatch, claim):
    """CodeRabbit #288, the real handle: the first pass's lock cannot be renewed (Redis failing for it) and expires
    while the event's turn runs, before its send is written down, and a second pass takes the lock and the same event.
    The turn's claim is renewed while it runs: the second pass finds the event still being answered and leaves it.
    Rule 5: the claim lost too (its renewal failing as well), the second pass answers and sends it; the first, ending
    after, sends nothing (the right to send is taken by one sender, and a send's mark stays past the release)."""
    import asyncio

    from tests.fake_redis import FakeClock

    await _fail_once(chat, redis)
    _turns(chat)
    turn = chat.answer
    inside, release = asyncio.Event(), asyncio.Event()
    started = {"n": 0}

    async def first_hangs(req, add_message):
        started["n"] += 1
        if started["n"] == 1:
            inside.set()
            await release.wait()
        return await turn(req, add_message)

    chat.answer = first_hangs
    clock = FakeClock()
    redis.clock = clock
    renewed = asyncio.Event()
    real_eval = redis.eval

    async def renewal_fails(script, numkeys, *keys_and_args):
        if script == mc.RENEW_LUA and (keys_and_args[0] == mc.RECOVER_LOCK or claim == "lost"):
            raise ResponseError("LOADING Redis is loading the dataset in memory")
        out = await real_eval(script, numkeys, *keys_and_args)
        if script == mc.RENEW_LUA:
            renewed.set()  # the claim renewed
        return out

    monkeypatch.setattr(redis, "eval", renewal_fails)
    monkeypatch.setattr(mc, "RECOVER_LOCK_S", 0.3)
    monkeypatch.setattr(mc, "PENDING_S", 0.3)
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        first = asyncio.create_task(mc.run_messenger_recover())
        await asyncio.wait_for(inside.wait(), 1)  # the first pass's turn begun, its send not written down
        if claim == "kept":
            # Past the lock's own time, in steps shorter than the claim's, each after the claim's renewal: the lock
            # (never renewed) runs out, the claim does not. On a clock the test moves (FakeClock).
            for _ in range(3):
                renewed.clear()
                await asyncio.wait_for(renewed.wait(), 2)
                clock.advance(mc.PENDING_S / 2)
        else:
            clock.advance(mc.PENDING_S * 1.5)  # both run out: neither is renewed
        second = await mc.run_messenger_recover()
        release.set()
        out = await asyncio.wait_for(first, 2)
    assert gw.sent == ["About: Hi, two seats to Istanbul in December?"], "one send, whichever pass made it"
    assert not redis.kv[mc.INBOX]
    if claim == "kept":
        assert second["waiting"] == 1 and out["answered"] == 1 and started["n"] == 1
    else:
        assert second["answered"] == 1 and out["waiting"] == 1 and started["n"] == 2


@pytest.mark.asyncio
async def test_a_delivery_from_the_gateway_and_a_recovery_that_lost_its_claim_never_both_send(chat, redis,
                                                                                            monkeypatch):
    """CodeRabbit #288, the real handle: a recovery pass of a kept event is in its turn when its claim runs out (its
    renewal failing), and a delivery of the same event from the gateway (one that never got X-Messenger-Kept) answers
    it: the gateway's delivery takes the right to send them, as the API's sends take it, so the recovery, ending
    after, sends nothing."""
    import asyncio

    from tests.fake_redis import FakeClock

    await _fail_once(chat, redis)
    _turns(chat)
    turn = chat.answer
    inside, release = asyncio.Event(), asyncio.Event()
    started = {"n": 0}

    async def first_hangs(req, add_message):
        started["n"] += 1
        if started["n"] == 1:
            inside.set()
            await release.wait()
        return await turn(req, add_message)

    chat.answer = first_hangs
    clock = FakeClock()
    redis.clock = clock
    real_eval = redis.eval

    async def claim_renewal_fails(script, numkeys, *keys_and_args):
        if script == mc.RENEW_LUA and str(keys_and_args[0]).startswith("msgr:event:"):
            raise ResponseError("LOADING Redis is loading the dataset in memory")
        return await real_eval(script, numkeys, *keys_and_args)

    monkeypatch.setattr(redis, "eval", claim_renewal_fails)
    monkeypatch.setattr(mc, "PENDING_S", 0.3)
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        recovery = asyncio.create_task(mc.run_messenger_recover())
        await asyncio.wait_for(inside.wait(), 1)
        clock.advance(mc.PENDING_S * 2)  # the recovery's claim runs out
        delivered = await mc.handle(event())  # the gateway's, again
        release.set()
        out = await asyncio.wait_for(recovery, 2)
    reply = "About: Hi, two seats to Istanbul in December?"
    assert [r["text"] for r in delivered["replies"]] == [reply], "the gateway sends them"
    assert gw.sent == [] and out["waiting"] == 1, "the recovery, ending after, sends nothing"
    assert redis.kv[f"{mc.INBOX}:sending:m1"].startswith(f"{mc.SENT_BY_GATEWAY}:")
    # The recovery's turn, ending after the delivery that took its event, never writes its answer over that one's.
    assert json.loads(redis.kv["msgr:event:m1"])["by"] == "gateway" and mc.MESSENGER_HEALTH["claim_lost"] == 1


@pytest.mark.asyncio
async def test_a_delivery_from_the_gateway_leaves_the_replies_to_a_recovery_that_took_them_first(chat, redis):
    """Rule 5 of the same: the recovery took the right to send first (its send on its way) and the event's mark is
    lost meanwhile: the gateway's delivery, answering it again, gets no replies; the recovery's go, once."""
    import asyncio

    await _fail_once(chat, redis)
    _turns(chat)
    sending, go_on = asyncio.Event(), asyncio.Event()
    sent: list = []

    async def slow(page_id, psid, messages, tag=None):
        sent.append(messages[0]["text"])
        sending.set()
        await go_on.wait()
        return {"sent": 1}

    with patch.object(mc, "send", new=slow):
        recovery = asyncio.create_task(mc.run_messenger_recover())
        await asyncio.wait_for(sending.wait(), 1)
        redis.kv.pop("msgr:event:m1")  # the event's mark lost meanwhile
        delivered = await mc.handle(event())
        go_on.set()
        out = await asyncio.wait_for(recovery, 2)
    assert delivered["replies"] == [] and mc.MESSENGER_HEALTH["duplicates"] == 1
    assert sent == ["About: Hi, two seats to Istanbul in December?"] and out["answered"] == 1
    assert not redis.kv[mc.INBOX]


@pytest.mark.asyncio
@pytest.mark.parametrize("ends", ["first_fails", "first_answers_before", "first_answers_after"])
async def test_a_turn_whose_claim_ran_out_never_clears_or_answers_over_the_delivery_that_took_the_event(
        chat, redis, monkeypatch, ends):
    """CodeRabbit #288: a delivery's turn (the first) runs past its claim, its renewal failing, and a delivery made
    again (the second) takes the event, its own claim under its own token, and is in its turn when the first ends.
    The first failing never clears the second's claim: a third delivery still waits (409). The first answering, before
    the second or after it, never writes its answer over the second's claim or answer (claim_lost), and gives the
    gateway none: only the second's replies go back, once. Rule 5: the second's answer is the one kept, and a
    delivery made again gets its replies, with no third turn."""
    import asyncio

    from tests.fake_redis import FakeClock

    calls = {"n": 0}
    first_in, first_go, second_in, second_go = (asyncio.Event() for _ in range(4))

    async def two_turns(req, add_message):
        calls["n"] += 1
        n = calls["n"]
        (first_in if n == 1 else second_in).set()
        await (first_go if n == 1 else second_go).wait()
        if n == 1 and ends == "first_fails":
            raise RuntimeError("database away")
        row = await add_message(req.conversation_id, "ai", f"Answer {n}")
        return ChatResponse(conversation_id=req.conversation_id, message=row["content"], type="ai", model_used="x")

    chat.answer = two_turns
    clock = FakeClock()
    redis.clock = clock
    real_eval = redis.eval

    async def claim_renewal_fails(script, numkeys, *keys_and_args):
        if script == mc.RENEW_LUA and str(keys_and_args[0]).startswith("msgr:event:"):
            raise ResponseError("LOADING Redis is loading the dataset in memory")
        return await real_eval(script, numkeys, *keys_and_args)

    monkeypatch.setattr(redis, "eval", claim_renewal_fails)
    first = asyncio.create_task(mc.handle(event()))
    await asyncio.wait_for(first_in.wait(), 1)
    clock.advance(mc.PENDING_S * 2)  # the first's claim runs out
    second = asyncio.create_task(mc.handle(event()))
    await asyncio.wait_for(second_in.wait(), 1)
    if ends == "first_fails":
        first_go.set()
        with pytest.raises(mc.EventFailed):
            await asyncio.wait_for(first, 1)
        with pytest.raises(mc.StillAnswering):
            await asyncio.wait_for(mc.handle(event()), 1)  # the second's claim is still there
    elif ends == "first_answers_before":
        first_go.set()
        assert (await asyncio.wait_for(first, 1))["replies"] == [], "its claim lost: it gives none"
        with pytest.raises(mc.StillAnswering):
            await asyncio.wait_for(mc.handle(event()), 1)  # the second's claim, never the first's answer
    second_go.set()
    assert [r["text"] for r in (await asyncio.wait_for(second, 1))["replies"]] == ["Answer 2"]
    if ends == "first_answers_after":
        first_go.set()
        assert (await asyncio.wait_for(first, 1))["replies"] == [], "its claim lost: it gives none"
    assert json.loads(redis.kv["msgr:event:m1"])["replies"] == [{"text": "Answer 2"}], "the second's answer is kept"
    assert mc.MESSENGER_HEALTH["claim_lost"] == (0 if ends == "first_fails" else 1)
    again = await mc.handle(event())
    assert [r["text"] for r in again["replies"]] == ["Answer 2"] and calls["n"] == 2, "no third turn"


@pytest.mark.asyncio
async def test_a_turn_whose_claim_ran_out_and_whose_giving_redis_never_answered_gives_none(chat, redis, monkeypatch):
    """CodeRabbit #288: a delivery's turn (the first) runs past its claim, its renewal failing, and a delivery made
    again (the second) takes the event and gives its replies back. Then the first's giving gets no answer from Redis:
    it gives the gateway none (409), never its replies beside the second's, and its giving, made again at its next
    minute, finds the second's answer and is dropped (claim_lost). Rule 5: a first whose giving Redis answers gives
    none for its claim lost (test_a_turn_whose_claim_ran_out_never_clears_or_answers_over_the_delivery_that_took_
    the_event)."""
    import asyncio

    from tests.fake_redis import FakeClock

    calls = {"n": 0}
    first_in, first_go = asyncio.Event(), asyncio.Event()

    async def two_turns(req, add_message):
        calls["n"] += 1
        n = calls["n"]
        if n == 1:
            first_in.set()
            await first_go.wait()
        row = await add_message(req.conversation_id, "ai", f"Answer {n}")
        return ChatResponse(conversation_id=req.conversation_id, message=row["content"], type="ai", model_used="x")

    chat.answer = two_turns
    clock = FakeClock()
    redis.clock = clock
    real_eval = redis.eval
    gives = {"n": 0}

    async def flaky(script, numkeys, *keys_and_args):
        if script == mc.RENEW_LUA and str(keys_and_args[0]).startswith("msgr:event:"):
            raise ResponseError("LOADING Redis is loading the dataset in memory")
        if script == mc.GIVE_TO_GATEWAY_LUA:
            gives["n"] += 1
            if gives["n"] == 2:  # the first's, ending after the second's
                raise ResponseError("OOM command not allowed when used memory > 'maxmemory'")
        return await real_eval(script, numkeys, *keys_and_args)

    monkeypatch.setattr(redis, "eval", flaky)
    first = asyncio.create_task(mc.handle(event()))
    await asyncio.wait_for(first_in.wait(), 1)
    clock.advance(mc.PENDING_S * 2)  # the first's claim runs out
    second = await asyncio.wait_for(mc.handle(event()), 1)
    assert [r["text"] for r in second["replies"]] == ["Answer 2"]
    first_go.set()
    with pytest.raises(mc.StillAnswering):
        await asyncio.wait_for(first, 1)  # none given beside the second's
    redis_client.set_client_for_tests(redis)  # the cooldown its failure began, over
    await mc.run_messenger_recover()  # its next minute: its giving made again
    assert not mc._UNGIVEN and not mc._UNKEPT and mc.MESSENGER_HEALTH["claim_lost"] == 1
    assert json.loads(redis.kv["msgr:event:m1"])["replies"] == [{"text": "Answer 2"}], "the second's answer stands"
    again = await mc.handle(event())
    assert [r["text"] for r in again["replies"]] == ["Answer 2"] and calls["n"] == 2, "no third turn"


@pytest.mark.asyncio
@pytest.mark.parametrize("first_ends", ["after_the_second", "before_the_second"])
async def test_two_turns_whose_givings_redis_never_ran_are_both_held_and_the_claims_one_is_given(chat, redis,
                                                                                                monkeypatch,
                                                                                                first_ends):
    """CodeRabbit #288: a delivery's turn (the first) runs past its claim, its renewal failing, and a delivery made
    again (the second) takes the event. Both turns end with Redis refusing their giving before it runs, in either
    order: this process holds both, never one over the other. Once Redis answers, while the second's claim still
    stands, a delivery made again gets the second's replies, given once with their right, and the first's is dropped
    (claim_lost): two turns, never a third. Rule 5: one turn held, its own giving made again
    (test_a_delivery_whose_giving_redis_did_not_take_waits_and_then_gets_the_replies_once)."""
    import asyncio

    from tests.fake_redis import FakeClock

    calls = {"n": 0}
    first_in, first_go = asyncio.Event(), asyncio.Event()
    second_in, second_go = asyncio.Event(), asyncio.Event()

    async def two_turns(req, add_message):
        calls["n"] += 1
        n = calls["n"]
        entered, go = (first_in, first_go) if n == 1 else (second_in, second_go)
        entered.set()
        await go.wait()
        row = await add_message(req.conversation_id, "ai", f"Answer {n}")
        return ChatResponse(conversation_id=req.conversation_id, message=row["content"], type="ai", model_used="x")

    chat.answer = two_turns
    clock = FakeClock()
    redis.clock = clock
    real_eval = redis.eval
    refusing = {"on": True}

    async def flaky(script, numkeys, *keys_and_args):
        if script == mc.RENEW_LUA and str(keys_and_args[0]).startswith("msgr:event:") and refusing["on"]:
            raise ResponseError("LOADING Redis is loading the dataset in memory")
        if script == mc.GIVE_TO_GATEWAY_LUA and refusing["on"]:
            raise ResponseError("OOM command not allowed when used memory > 'maxmemory'")  # never ran
        return await real_eval(script, numkeys, *keys_and_args)

    monkeypatch.setattr(redis, "eval", flaky)
    first = asyncio.create_task(mc.handle(event()))
    await asyncio.wait_for(first_in.wait(), 1)
    clock.advance(mc.PENDING_S * 2)  # the first's claim runs out
    second = asyncio.create_task(mc.handle(event()))
    await asyncio.wait_for(second_in.wait(), 1)
    second_claim = redis.kv["msgr:event:m1"]
    order = [second_go, first_go] if first_ends == "after_the_second" else [first_go, second_go]
    turns = [second, first] if first_ends == "after_the_second" else [first, second]
    for go, turn in zip(order, turns):
        go.set()
        with pytest.raises(mc.StillAnswering):
            await asyncio.wait_for(turn, 1)  # none given while Redis did not say whose the right is
    assert redis.kv["msgr:event:m1"] == second_claim, "the second's claim still stands"
    refusing["on"] = False
    redis_client.set_client_for_tests(redis)  # the cooldown its failure began, over
    again = await mc.handle(event())  # a delivery made again, the second's claim still there
    assert [r["text"] for r in again["replies"]] == ["Answer 2"], "the second's replies, given once"
    assert redis.kv[mc._sending_key("m1")].startswith("gateway:") and calls["n"] == 2, "no third turn"
    assert not mc._UNKEPT and not mc._UNGIVEN and not mc._HELD_BESIDE and mc.MESSENGER_HEALTH["claim_lost"] == 1


@pytest.mark.asyncio
async def test_a_claim_let_go_of_between_its_set_and_get_is_taken_again_before_the_turn(redis, monkeypatch):
    """CodeRabbit #288: another delivery's claim is there at SET NX and gone at GET (it ran out, or its turn failed):
    the claim is taken again, under this turn's own token, before the turn starts, so a delivery after it waits
    (pending), never a second turn beside it. Rule 5: a claim still there reads as being answered
    (test_a_delivery_made_again_gets_the_same_replies_and_no_second_turn)."""
    key = "msgr:event:m1"
    redis.kv[key] = "pending:theirs"
    real_get = redis.get
    gone = {"once": True}

    async def get(k):
        if gone["once"] and k == key:
            gone["once"] = False
            redis.kv.pop(k, None)  # let go of between SET NX and GET
            return None
        return await real_get(k)

    monkeypatch.setattr(redis, "get", get)
    state, kept = await mc._claim(key)
    assert state == "new" and redis.kv.get(key) == kept["token"], "taken, under its own token"
    assert (await mc._claim(key))[0] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("known", ["written_meanwhile", "there_before"])
async def test_a_send_known_to_have_sent_nothing_written_down_meanwhile_is_never_dropped_by_an_older_read(redis,
                                                                                                    monkeypatch, known):
    """CodeRabbit #288: _send_state reads an event's send mark and finds none, and while Redis answers a send of this
    process writes its own mark, sends nothing and could not clear it (_UNSENT): the read, older than it, never drops
    what that send wrote down, so this process still clears its mark itself. Rule 5: what was written down before the
    read, its mark found gone, is dropped."""
    k = "m1"
    newer = (f"{time.time()}:{mc.BOOT}", event().model_dump_json(), [{"text": "Hi"}])
    if known == "there_before":
        mc._UNSENT[k] = newer
    real_get = redis.get

    async def get(key):
        out = await real_get(key)
        if key == mc._sending_key(k) and known == "written_meanwhile":
            mc._UNSENT[k] = newer  # written down by a send of this process while Redis answered
        return out

    monkeypatch.setattr(redis, "get", get)
    assert await mc._send_state(redis, k) == "none"
    assert (mc._UNSENT.get(k) == newer) == (known == "written_meanwhile")


@pytest.mark.asyncio
async def test_a_newer_held_answer_is_never_dropped_by_the_write_of_an_older_one(redis, monkeypatch):
    """CodeRabbit #288: this process writes an answer it holds, and while Redis answers, a newer turn of the same event
    has its own held (its write refused): the older one's write never drops the newer one, which stays to be
    written. Rule 5: the one written, still the one held, is dropped
    (test_a_recovered_answer_redis_did_not_take_is_written_before_it_is_sent_never_answered_twice)."""
    key = "msgr:event:m1"
    mc._UNKEPT[key] = ("pending:older", json.dumps({"replies": [], "by": "api"}))
    newer = ("pending:newer", json.dumps({"replies": [{"text": "Newer"}], "by": "api"}))
    real_eval = redis.eval

    async def eval_(script, numkeys, *keys_and_args):
        out = await real_eval(script, numkeys, *keys_and_args)
        if script == mc.KEEP_ANSWER_LUA:
            mc._UNKEPT[key] = newer  # held meanwhile by a newer turn
        return out

    monkeypatch.setattr(redis, "eval", eval_)
    assert await mc._write_held(redis, key)
    assert mc._UNKEPT.get(key) == newer


@pytest.mark.asyncio
@pytest.mark.parametrize("held_by", ["mine", "theirs"])
async def test_a_turns_claim_is_renewed_only_while_it_holds_the_turns_own_token(redis, monkeypatch, held_by):
    """CodeRabbit #288: a turn's renewal finds another delivery's claim on the event (its own ran out, and the event
    was taken since): it stops, never giving the other's claim a new life as its own. Rule 5: its own claim is
    renewed while it runs."""
    import asyncio

    from tests.fake_redis import FakeClock

    clock = FakeClock()
    redis.clock = clock
    monkeypatch.setattr(mc, "PENDING_S", 0.3)
    key = "msgr:event:m1"
    await redis.set(key, f"pending:{held_by}", px=300)
    before = redis.expires[key]
    clock.advance(0.2)
    keeper = asyncio.create_task(mc._keep_pending(redis, key, "pending:mine"))
    if held_by == "theirs":
        await asyncio.wait_for(keeper, 1)  # it stops by itself: not its own
        assert redis.expires[key] == before and redis.kv[key] == "pending:theirs"
        return
    for _ in range(100):
        if redis.expires[key] != before:
            break
        await asyncio.sleep(0.01)
    keeper.cancel()
    await asyncio.gather(keeper, return_exceptions=True)
    assert redis.expires[key] == pytest.approx(clock() + 0.3), "renewed from now, for PENDING_S"


@pytest.mark.asyncio
async def test_notes_that_keep_failing_never_keep_the_others_from_their_turn(chat, redis, monkeypatch):
    """CodeRabbit #288: Redis gives the outbox whole in one part (HSCAN's COUNT is only a hint), and the first notes
    in it keep failing to be saved: each pass goes on after the last delivery it looked at, so the others are told
    in the next passes. Rule 5: once the database takes them, the failing ones are told too, once."""
    redis.scan_whole = True
    redis.kv[mc.OUTBOX] = {f"e{i}": json.dumps({"cid": CONV, "boot": "gone-one", "state": "untold",
                                                 "note": f"Note {i}", "at": time.time()}) for i in range(6)}
    monkeypatch.setattr(mc, "TELL_BATCH", 2)
    real = mc._tell_agent
    failing = {"Note 0", "Note 1"}

    async def refuses_some(cid, note, note_id=None):
        return False if note in failing else await real(cid, note, note_id)

    monkeypatch.setattr(mc, "_tell_agent", refuses_some)
    passes = [(await mc.run_messenger_recover())["told"] for _ in range(3)]
    assert passes == [0, 2, 2] and set(await redis.hgetall(mc.OUTBOX)) == {"e0", "e1"}
    failing.clear()
    passes = [(await mc.run_messenger_recover())["told"] for _ in range(2)]
    assert sum(passes) == 2 and not await redis.hgetall(mc.OUTBOX)
    assert sorted(r["content"] for r in chat.rows if r["role"] == "system") == [f"Note {i}" for i in range(6)]


@pytest.mark.asyncio
async def test_the_notes_after_failing_ones_are_told_while_the_deliveries_between_them_end(chat, redis, monkeypatch):
    """CodeRabbit #288: the outbox in one part (HSCAN's COUNT a hint), its first notes failing pass after pass, and
    the deliveries after them (a live process's) done with and crossed out by it between passes, one each time: each
    pass goes on from the deliveries not looked at yet, never from the start again, so the later notes are told. Rule
    5: the part gone through, the failing ones get their turn again, and are told once the database takes them."""
    redis.scan_whole = True
    redis.kv["msgr:alive:alive-one"] = "1"

    def note(i):
        return json.dumps({"cid": CONV, "boot": "gone-one", "state": "untold", "note": f"Note {i}", "at": time.time()})

    def live():
        return json.dumps({"cid": CONV, "boot": "alive-one", "state": "sending", "at": time.time()})

    redis.kv[mc.OUTBOX] = {"e0": note(0), "e1": note(1), "d0": live(), "d1": live(), "d2": live(),
                           "e2": note(2), "e3": note(3), "e4": note(4), "e5": note(5)}
    monkeypatch.setattr(mc, "TELL_BATCH", 2)
    real = mc._tell_agent
    failing = {"Note 0", "Note 1"}

    async def refuses_some(cid, note, note_id=None):
        return False if note in failing else await real(cid, note, note_id)

    monkeypatch.setattr(mc, "_tell_agent", refuses_some)
    passes = []
    for done in ("d0", "d1", "d2"):
        passes.append((await mc.run_messenger_recover())["told"])
        await redis.hdel(mc.OUTBOX, done)  # its process done with it, between passes
    assert passes == [0, 2, 2] and set(await redis.hgetall(mc.OUTBOX)) == {"e0", "e1"}
    assert sorted(r["content"] for r in chat.rows if r["role"] == "system") == [f"Note {i}" for i in range(2, 6)]
    failing.clear()
    later = [(await mc.run_messenger_recover())["told"] for _ in range(2)]
    assert sum(later) == 2 and not await redis.hgetall(mc.OUTBOX)
    assert sorted(r["content"] for r in chat.rows if r["role"] == "system") == [f"Note {i}" for i in range(6)]


@pytest.mark.asyncio
async def test_a_note_whose_save_hangs_is_cut_at_the_notes_time_and_the_events_still_get_theirs(chat, redis,
                                                                                               monkeypatch):
    """CodeRabbit #288: one note's save hangs (the database slow): it is cut at TELL_BUDGET_S, and the inbox's event
    is answered in the same pass. Rule 5: the database back, that note is told, once."""
    import asyncio

    await _fail_once(chat, redis)
    _turns(chat)
    redis.kv[mc.OUTBOX] = {"e-slow": json.dumps({"cid": CONV, "boot": "gone-one", "state": "untold", "note": "Slow",
                                                  "at": time.time()})}
    hang = {"on": True}
    real = mc._tell_agent

    async def slow(cid, note, note_id=None):
        if hang["on"]:
            await asyncio.sleep(30)
        return await real(cid, note, note_id)

    monkeypatch.setattr(mc, "_tell_agent", slow)
    monkeypatch.setattr(mc, "TELL_BUDGET_S", 0.1)
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        out = await asyncio.wait_for(mc.run_messenger_recover(), 2)
        assert out["told"] == 0 and out["answered"] == 1 and len(await redis.hgetall(mc.OUTBOX)) == 1
        hang["on"] = False
        later = [(await mc.run_messenger_recover())["told"] for _ in range(2)]
    assert sum(later) == 1 and [r["content"] for r in chat.rows if r["role"] == "system"] == ["Slow"]
    assert gw.sent == ["About: Hi, two seats to Istanbul in December?"] and not await redis.hgetall(mc.OUTBOX)


@pytest.mark.asyncio
async def test_after_the_sale_a_message_not_saved_is_delivered_again_through_the_real_turn(redis):
    """The real chat handler (app.api.chat.chat), not a stand-in: a completed chat, the client's message not saved
    (503 from the turn), the event fails; delivered again, the message is kept and the ack goes back."""
    from app.services import conversation_lock
    from tests.fake_redis import compare_and_delete

    redis.register(conversation_lock.RELEASE_LUA, compare_and_delete)
    cid = "8b0f3d3e-6f0a-4c5e-9d55-1f2a3b4c5d6e"
    vid = mc.visitor_id_for(PAGE, PSID)
    conv = {"id": cid, "status": "completed", "mode": "ai", "visitor_id": vid, "updated_at": now_iso(),
            "metadata": {"chat_channel": "messenger", "closing_sent_at": now_iso(hours=-1)}}
    c = Chat(latest=conv, conv=conv)
    c.saves["user"] = ["fail"]
    ps = [
        patch("app.db.supabase.add_message", new=c.add_message),
        patch("app.realtime.manager.manager.push", new=AsyncMock()),
        patch("app.db.supabase.latest_conversation_of_visitor", new=AsyncMock(return_value=conv)),
        patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=conv)),
        patch("app.db.supabase.count_messages", new=AsyncMock(return_value=4)),
        patch("app.db.supabase.get_recent_messages", new=AsyncMock(return_value=[])),
        patch("app.api.chat.is_blocked", new=AsyncMock(return_value=False)),
    ]
    for p in ps:
        p.start()
    try:
        with pytest.raises(mc.EventFailed) as failed:
            await mc.handle(event(text="One more question about my booking"))
        assert getattr(failed.value.__cause__, "status_code", None) == 503 and c.rows == []
        out = await mc.handle(event(text="One more question about my booking"))
    finally:
        for p in ps:
            p.stop()
    assert [(r["role"], r["content"]) for r in c.rows][0] == ("user", "One more question about my booking")
    assert [r["text"] for r in out["replies"]] == [c.rows[1]["content"]]
    assert out["replies"][0]["text"].startswith("Thank you! Your consultant will reach out")


def test_health_says_whether_it_is_on_and_each_count():
    h = mc.health()
    assert h["enabled"] is True and {"events", "turns", "send_failed", "window_closed", "kept_unanswered"} <= set(h)
