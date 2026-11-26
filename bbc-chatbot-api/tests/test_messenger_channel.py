"""Facebook Messenger, the API's side (app.services.messenger_channel, contracts/messenger.md).

The gateway (bbc-messenger) is tested in its Go suite; here, what the API does with its events: the call it signs,
the person's chat, the same turn a widget message takes, what goes back to the gateway and what goes out through it
(an agent's message), what is kept without a reply, and the counters. Every id, name and text here is invented.
"""

import json
import os
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
    monkeypatch.setattr(mc, "_CLOSING", False)
    monkeypatch.setenv("MESSENGER_CHANNEL", "true")
    monkeypatch.setattr(settings, "messenger_gateway_url", "https://gateway.example.test")
    monkeypatch.setattr(settings, "messenger_gateway_secret", SECRET)
    yield


@pytest.fixture
def redis():
    from tests.fake_redis import compare_and_delete, compare_and_pexpire

    r = FakeRedis()
    # The recovery's lock is renewed and let go only while it is still the pass's own (conversation_lock's scripts).
    r.register(mc.RELEASE_LUA, compare_and_delete)
    r.register(mc.RENEW_LUA, compare_and_pexpire)
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
                          on_stored=None):
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

    async def stored(c, r, t, m=None, x=0.0, *, message_id=None, on_stored=None):
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
    """CodeRabbit #288: the agent's message stored, then its live push slow: a line stored after it, for the same
    chat, goes out after it (its place in the chat's order is taken as it is stored, before the push). Rule 5: the
    agent's message held up in its outbox write is not stored yet, so a line stored meanwhile is the earlier one in
    the chat, and goes first."""
    import asyncio

    redis.kv[f"msgr:conv:{CONV}"] = json.dumps({"page_id": PAGE, "psid": PSID})
    order: list = []

    async def gw(page_id, psid, messages, tag=None):
        order.append(messages[0]["text"])
        return {"sent": 1}

    gate, holding = asyncio.Event(), asyncio.Event()
    real_hset = redis.hset

    async def push(conversation_id, row):
        if held == "push" and row["role"] == "agent":
            holding.set()
            await gate.wait()

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
            await add_message(CONV, "system", "Ana shared the itinerary")
            gate.set()
            await agent
            await mc.drain(CONV)
            await asyncio.sleep(0)
            await mc.drain(CONV)
    finally:
        c.stop()
    stored = [r["content"] for r in c.rows]
    assert order == stored, "out in the order stored"
    assert stored == (["Hi, I'm Ana.", "Ana shared the itinerary"] if held == "push"
                      else ["Ana shared the itinerary", "Hi, I'm Ana."])


@pytest.mark.asyncio
async def test_a_rows_place_is_taken_as_the_database_stores_it_before_its_activity_clocks(redis):
    """CodeRabbit #288, the real db.add_message: the agent's message inserted, then the chat's activity clocks slow to
    update: it takes its place in the chat's order the moment it is stored, so a line stored after it goes out after
    it. Rule 5: a row the database refused takes no place and goes nowhere."""
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
        await add_message(CONV, "system", "Ana shared the itinerary")
        go_on.set()
        assert (await agent)["content"] == "Hi, I'm Ana."
        refuse["on"] = True
        assert await add_message(CONV, "system", "Refused by the database") is None
        await mc.drain(CONV)
        await asyncio.sleep(0)
        await mc.drain(CONV)
    assert [r["content"] for r in stored] == ["Hi, I'm Ana.", "Ana shared the itinerary"]
    assert order == ["Hi, I'm Ana.", "Ana shared the itinerary"], "out in the order stored"
    assert not await redis.hgetall(mc.OUTBOX)


@pytest.mark.asyncio
async def test_an_agents_message_finds_its_thread_when_redis_forgot_it(redis):
    conv = {"id": CONV, "metadata": {"chat_channel": "messenger", "messenger": {"page_id": PAGE, "psid": PSID}}}
    gw = Gateway([])
    c = Chat().start()
    try:
        with patch.object(mc, "send", new=gw), \
                patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=conv)):
            from app.services.conversation_service import add_message

            await add_message(CONV, "agent", "Following up on your trip.")
            await mc.drain(CONV)
    finally:
        c.stop()
    assert gw.sent == ["Following up on your trip."]
    assert json.loads(redis.kv[f"msgr:conv:{CONV}"]) == {"page_id": PAGE, "psid": PSID}


@pytest.mark.asyncio
async def test_a_widget_chat_sends_nothing_anywhere(redis, monkeypatch):
    """Rule 5: a chat that is not on Messenger: nothing goes out; with the channel off, nothing is even looked up."""
    gw = Gateway([])
    c = Chat().start()
    try:
        with patch.object(mc, "send", new=gw), \
                patch("app.db.supabase.get_conversation_simple",
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
            with patch("app.db.supabase.get_conversation_simple",
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
                         on_stored=None):
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
                         on_stored=None):
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
    gate = asyncio.Event()
    real_hset = redis.hset

    async def held_up(*a, **kw):
        if lands:
            await real_hset(*a, **kw)
        await gate.wait()
        return 1

    c = Chat().start()
    gw = Gateway([])
    try:
        with patch.object(mc, "send", new=gw):
            from app.services.conversation_service import add_message

            monkeypatch.setattr(redis, "hset", held_up)
            saving = asyncio.create_task(add_message(CONV, "agent", "Cancelled while written down"))
            await asyncio.sleep(0.01)
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
                           on_stored=None):
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
                patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=conv)):
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
    }
    chats = {"site-chat": {"id": "site-chat", "metadata": {"site": "bbc"}}}
    c = Chat().start()
    c.rows.append({"id": mc._note_id("e-told"), "conversation_id": CONV, "role": "system",
                   "content": mc.NOT_SENT["unknown"], "created_at": now_iso()})
    c.saves["system"] = ["", "", "fail"]
    try:
        with patch("app.db.supabase.get_conversation_simple", new=AsyncMock(side_effect=lambda cid: chats.get(cid))):
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
        if blinks["on"] and script == mc.RELEASE_LUA and sending(keys_and_args[0]):
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
@pytest.mark.parametrize("owner", ["alive", "gone", "stale"])
async def test_a_send_that_sent_nothing_is_left_to_its_process_by_another_on_the_same_redis(chat, redis, monkeypatch,
                                                                                            owner):
    """CodeRabbit #288: two API processes on one Redis. Process A's send never reached the gateway and its mark could
    not be cleared (Redis failing): process B, meeting the mark past SENDING_S while A is alive, waits; A's next minute
    clears it, the lock held by B or not, and the same replies go, once, with no second turn. Rule 5: A gone before it
    could, what it knew went with it: B takes the mark for a send begun, maybe out, never sent again (counted), as an
    outcome the gateway may have had is (test_a_recovered_answer_the_gateway_may_have_sent_is_never_sent_again); and
    so does B for a mark of A's older than A's minutes could have left it (SENDING_S + ALIVE_S), A alive or not."""
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
        blinks["on"] = False
        monkeypatch.setattr(mc, "SENDING_S", -1.0)  # however long it waited: past SENDING_S
        a_knows = mc._UNSENT.copy()
        become("process-b", type(mc._UNSENT)())
        if owner in ("gone", "stale"):
            if owner == "gone":
                redis.kv.pop("msgr:alive:process-a")
            else:
                monkeypatch.setattr(mc, "SENDING_S", -1.0 - mc.ALIVE_S)
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

    async def kept_then(ev):
        out = await real_keep(ev)
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
    found lost (another pass holds it now), the pass starts no other event and leaves that pass's lock alone."""
    import asyncio

    await _fail_once(chat, redis)
    redis.kv[mc.INBOX]["m1"] -= 1  # the oldest
    with pytest.raises(mc.EventFailed):
        await mc.handle(event(mid="q1", psid="PSID-8", text="Hello"))
    redis.kv[mc.INBOX]["q1"] -= mc.RECOVER_AFTER_S + 1

    async def slow(ev, *, by="gateway"):
        if lost and ev.mid == "m1":
            await redis.set(mc.RECOVER_LOCK, "another-pass", ex=60)
        await asyncio.sleep(0.5)
        return {"replies": []}

    monkeypatch.setattr(mc, "RECOVER_LOCK_S", 0.3)
    monkeypatch.setattr(mc, "handle", slow)
    first = asyncio.create_task(mc.run_messenger_recover())
    await asyncio.sleep(0.4)  # past the lock's own time, m1 still on its way
    second = await mc.run_messenger_recover()
    out = await first
    assert second["state"] == "busy"
    if lost:
        assert out["answered"] == 1 and set(redis.kv[mc.INBOX]) == {"q1"}
        assert redis.kv[mc.RECOVER_LOCK] == "another-pass"
    else:
        assert out["answered"] == 2 and not redis.kv[mc.INBOX] and mc.RECOVER_LOCK not in redis.kv


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["kept", "lost"])
async def test_an_event_whose_pass_lost_its_lock_mid_turn_is_sent_at_most_once(chat, redis, monkeypatch, claim):
    """CodeRabbit #288, the real handle: the first pass's lock cannot be renewed (Redis failing for it) and expires
    while the event's turn runs, before its send is written down, and a second pass takes the lock and the same event.
    The turn's claim is renewed while it runs: the second pass finds the event still being answered and leaves it.
    Rule 5: the claim lost too (its renewal failing as well), the second pass answers and sends it; the first, ending
    after, sends nothing (the right to send is taken by one sender, and a send's mark stays past the release)."""
    import asyncio

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
    real_eval = redis.eval

    async def renewal_fails(script, numkeys, *keys_and_args):
        if script == mc.RENEW_LUA and (keys_and_args[0] == mc.RECOVER_LOCK or claim == "lost"):
            raise ResponseError("LOADING Redis is loading the dataset in memory")
        return await real_eval(script, numkeys, *keys_and_args)

    monkeypatch.setattr(redis, "eval", renewal_fails)
    monkeypatch.setattr(mc, "RECOVER_LOCK_S", 0.3)
    monkeypatch.setattr(mc, "PENDING_S", 0.3)
    gw = Gateway([])
    with patch.object(mc, "send", new=gw):
        first = asyncio.create_task(mc.run_messenger_recover())
        await asyncio.wait_for(inside.wait(), 1)  # the first pass's turn begun, its send not written down
        await asyncio.sleep(0.45)  # past the lock's own time (and the claim's, when its renewal fails too)
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
