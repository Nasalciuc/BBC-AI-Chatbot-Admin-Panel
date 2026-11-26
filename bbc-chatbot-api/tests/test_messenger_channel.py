"""Facebook Messenger, the API's side (app.services.messenger_channel, contracts/messenger.md).

The gateway (bbc-messenger) is tested in its Go suite; here, what the API does with its events: the call it signs,
the person's chat, the same turn a widget message takes, what goes back to the gateway and what goes out through it
(an agent's message), what is kept without a reply, and the counters. Every id, name and text here is invented.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import FastAPI, HTTPException  # noqa: E402
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
    monkeypatch.setattr(mc, "_CLOSING", False)
    monkeypatch.setenv("MESSENGER_CHANNEL", "true")
    monkeypatch.setattr(settings, "messenger_gateway_url", "https://gateway.example.test")
    monkeypatch.setattr(settings, "messenger_gateway_secret", SECRET)
    yield


@pytest.fixture
def redis():
    r = FakeRedis()
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
        # None back all the same).
        self.saves: dict[str, list[str]] = {}
        self.patch_ok = True

    async def add_message(self, conversation_id, role, content, model_used=None, cost=0.0):
        queue = self.saves.get(role)
        what = queue.pop(0) if queue else None
        row = {"id": f"r{len(self.rows) + 1}", "conversation_id": conversation_id, "role": role, "content": content,
               "created_at": now_iso()}
        if what == "fail":
            return None
        self.rows.append(row)
        return None if what == "lost" else row

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

    with patch("app.db.supabase.add_message", new=AsyncMock(side_effect=lambda c, r, t, m=None, x=0.0: {
            "id": "r9", "role": r, "content": t})), patch("app.realtime.manager.manager.push", new=AsyncMock()):
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
