"""The search box's other answers: what was said (GET /api/search/messages) and the chats I opened lately
(GET /api/search/recent, noted by the chat's detail). Every name and word here is invented."""

import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import search as search_api  # noqa: E402
from app.db import supabase as db  # noqa: E402
from app.infra import redis_client  # noqa: E402
from app.security.auth import get_current_user  # noqa: E402
from app.services import recent_chats  # noqa: E402
from app.services.chat_search import SEARCH_HEALTH  # noqa: E402
from tests.fake_redis import FakeRedis  # noqa: E402

ME, OTHER = "aaaaaaaa-0000-4000-8000-000000000001", "aaaaaaaa-0000-4000-8000-000000000002"


@pytest.fixture
def redis():
    fake = FakeRedis()
    redis_client.set_client_for_tests(fake)
    with patch("config.settings.settings.redis_url", "redis://fake"):
        yield fake
    redis_client.set_client_for_tests(None)


@pytest.fixture(autouse=True)
def _fresh_local_counts():
    search_api._local_counts.clear()
    yield
    search_api._local_counts.clear()


def _client(role, *, tunnel_scope="sales", user_id=ME):
    app = FastAPI()
    app.include_router(search_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "role": role, "tunnel_scope": tunnel_scope}
    return TestClient(app)


def _chat(cid, **kw):
    base = {"id": cid, "tunnel": "sales", "team_id": None, "status": "closed", "mode": "ai",
            "assigned_agent_id": OTHER, "visitor_name": f"Client {cid}", "updated_at": "2026-11-20T10:00:00+00:00",
            "engaged_agent_id": None, "chat_number": 100, "queued_at": None}
    base.update(kw)
    return base


def _hit(cid, content="We could fly to Lisbon on the 14th", role="user"):
    return {"id": f"m-{cid}", "conversation_id": cid, "role": role, "content": content,
            "created_at": "2026-11-20T10:00:00+00:00"}


def _search(role, q, hits, chats, **client_kw):
    found = AsyncMock(return_value=hits)
    brief = AsyncMock(return_value={c["id"]: c for c in chats})
    with patch.object(db, "search_messages", new=found), patch.object(db, "get_conversations_brief", new=brief):
        r = _client(role, **client_kw).get("/api/search/messages", params={"q": q})
    return r, found


# ── what was said ─────────────────────────────────────────────


def test_a_manager_finds_the_words_in_any_chat():
    chats = [_chat("c1"), _chat("c2", tunnel="support"), _chat("c3", status="needs_agent", assigned_agent_id=None)]
    r, found = _search("owner", "Lisbon", [_hit("c1"), _hit("c2"), _hit("c3")], chats)
    assert r.status_code == 200
    assert [d["conversation_id"] for d in r.json()["data"]] == ["c1", "c2", "c3"]
    assert found.await_args.args[0] == "Lisbon" and found.await_args.kwargs["since_iso"]


def test_an_agent_never_finds_a_chat_waiting_blind_and_stays_in_their_tunnel():
    chats = [
        _chat("open"),                                                       # a colleague's closed chat
        _chat("blind", status="needs_agent", assigned_agent_id=None),        # waiting for a person
        _chat("queued", status="active", assigned_agent_id=None, queued_at="2026-11-20T09:00:00+00:00"),
        _chat("theirs", status="active", mode="human", assigned_agent_id=ME),
        _chat("engaged", status="active", assigned_agent_id=None, mode="human", engaged_agent_id=ME),
        _chat("support", tunnel="support"),
    ]
    r, _ = _search("sales", "Lisbon", [_hit(c["id"]) for c in chats], chats)
    assert r.status_code == 200
    assert [d["conversation_id"] for d in r.json()["data"]] == ["open", "theirs", "engaged"]


@pytest.mark.parametrize("role", ["supervisor", "project_manager", "viewer", ""])
def test_who_does_not_search_the_words(role):
    r, found = _search(role, "Lisbon", [_hit("c1")], [_chat("c1")])
    assert r.status_code == 403
    found.assert_not_awaited()


@pytest.mark.parametrize("q, status", [("ab", 422), ("   ab   ", 400), ("x" * 101, 422)])
def test_too_short_or_too_long_is_refused_before_reading(q, status):
    r, found = _search("owner", q, [], [])
    assert r.status_code == status
    found.assert_not_awaited()


def test_the_snippet_is_the_words_around_the_match():
    long = "Hello there. " * 20 + "We could fly to LISBON on the 14th, business class for two. " + "Thanks! " * 20
    r, _ = _search("owner", "lisbon", [_hit("c1", content=long)], [_chat("c1")])
    snip = r.json()["data"][0]["snippet"]
    assert "LISBON" in snip and snip.startswith("…") and snip.endswith("…") and len(snip) < 150
    assert r.json()["data"][0]["said_by"] == "client"


def test_a_chat_gone_since_the_message_was_read_is_left_out():
    r, _ = _search("owner", "Lisbon", [_hit("c1"), _hit("gone")], [_chat("c1")])
    assert [d["conversation_id"] for d in r.json()["data"]] == ["c1"]


def test_a_failed_read_is_a_502_and_counted():
    before = SEARCH_HEALTH["messages_failed"]
    with patch.object(db, "search_messages", new=AsyncMock(side_effect=RuntimeError("down"))):
        r = _client("owner").get("/api/search/messages", params={"q": "Lisbon"})
    assert r.status_code == 502 and SEARCH_HEALTH["messages_failed"] == before + 1


def test_a_few_searches_a_minute_on_redis(redis, monkeypatch):
    monkeypatch.setenv("SEARCH_MESSAGES_PER_MINUTE", "3")
    codes = [_search("owner", "Lisbon", [], [])[0].status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    other = _search("owner", "Lisbon", [], [], user_id=OTHER)[0]
    assert other.status_code == 200, "the limit is each user's own"


def test_without_redis_the_limit_counts_in_the_process(monkeypatch):
    monkeypatch.setenv("SEARCH_MESSAGES_PER_MINUTE", "2")
    redis_client.set_client_for_tests(None)
    codes = [_search("sales", "Lisbon", [], [])[0].status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_a_redis_that_fails_falls_back_to_the_process(redis, monkeypatch):
    monkeypatch.setenv("SEARCH_MESSAGES_PER_MINUTE", "1")
    redis.down = True
    codes = [_search("owner", "Lisbon", [], [])[0].status_code for _ in range(2)]
    assert codes == [200, 429]


# ── opened lately ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_last_opened_comes_first_once_and_at_most_ten(redis):
    for i in range(12):
        assert await recent_chats.remember(ME, f"c{i}")
    await recent_chats.remember(ME, "c5")
    ids = await recent_chats.recent_ids(ME)
    assert ids[0] == "c5" and ids.count("c5") == 1 and len(ids) == recent_chats.KEEP
    assert "c0" not in ids and "c1" not in ids
    assert await redis.ttl(recent_chats.key_for(ME)) > 29 * 86400


@pytest.mark.asyncio
async def test_without_redis_nothing_is_noted_and_the_list_cannot_say():
    redis_client.set_client_for_tests(None)
    with patch("config.settings.settings.redis_url", ""):
        assert await recent_chats.remember(ME, "c1") is False
        assert await recent_chats.recent_ids(ME) is None


@pytest.mark.asyncio
async def test_a_redis_that_fails_is_counted_not_raised(redis):
    redis.down = True
    before = dict(recent_chats.RECENT_HEALTH)
    assert await recent_chats.remember(ME, "c1") is False
    redis_client.set_client_for_tests(redis)  # clears the cooldown
    assert await recent_chats.recent_ids(ME) is None
    assert recent_chats.RECENT_HEALTH["remember_failed"] == before["remember_failed"] + 1
    assert recent_chats.RECENT_HEALTH["read_failed"] == before["read_failed"] + 1


@pytest.mark.asyncio
async def test_the_list_is_read_fresh_in_the_users_tunnel_and_masked_for_a_supervisor(redis):
    for cid in ("c1", "support", "gone", "c2"):
        await recent_chats.remember(ME, cid)
    chats = {"c1": _chat("c1", visitor_name="Dana Example"), "c2": _chat("c2", visitor_name="Lee Sample"),
             "support": _chat("support", tunnel="support")}
    with patch.object(db, "get_conversations_brief", new=AsyncMock(return_value=chats)):
        sales = _client("sales").get("/api/search/recent").json()
        owner = _client("owner").get("/api/search/recent").json()
        sup = _client("supervisor").get("/api/search/recent").json()
    assert [d["id"] for d in sales["data"]] == ["c2", "c1"]
    assert [d["id"] for d in owner["data"]] == ["c2", "support", "c1"]
    assert all(d["visitor_name"] not in ("Dana Example", "Lee Sample") for d in sup["data"])


def test_no_list_yet_is_an_empty_answer_that_says_whether_it_could_look(redis):
    r = _client("sales").get("/api/search/recent")
    assert r.status_code == 200 and r.json() == {"success": True, "data": [], "count": 0, "available": True}
    redis_client.set_client_for_tests(None)
    with patch("config.settings.settings.redis_url", ""):
        r = _client("sales").get("/api/search/recent")
    assert r.json()["available"] is False


def test_opening_a_chat_notes_it(redis):
    from app.api import conversations as conv_api

    app = FastAPI()
    app.include_router(conv_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": ME, "role": "owner"}
    conv = {"id": "c9", "tunnel": "sales", "status": "closed", "mode": "ai", "metadata": {}}
    with patch.object(db, "get_conversation", new=AsyncMock(return_value=conv)):
        assert TestClient(app).get("/api/conversations/c9").status_code == 200
    import asyncio
    assert asyncio.run(recent_chats.recent_ids(ME)) == ["c9"]


def test_a_chat_that_does_not_open_is_not_noted(redis):
    """Rule 5: a 404 or a 403 is not an opened chat."""
    from app.api import conversations as conv_api

    app = FastAPI()
    app.include_router(conv_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": ME, "role": "sales", "tunnel_scope": "sales"}
    with patch.object(db, "get_conversation", new=AsyncMock(return_value=None)):
        assert TestClient(app).get("/api/conversations/c9").status_code == 404
    other_tunnel = {"id": "c8", "tunnel": "support", "status": "closed", "mode": "ai", "metadata": {}}
    with patch.object(db, "get_conversation", new=AsyncMock(return_value=other_tunnel)):
        assert TestClient(app).get("/api/conversations/c8").status_code == 403
    import asyncio
    assert asyncio.run(recent_chats.recent_ids(ME)) == []
