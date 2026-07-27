"""Personalized chat — operator identity on messages + operator→visitor typing.

The visitor should see a real person (photo + name, labeled "Consultant") and a
live "is typing…" while the operator writes. Identity must never leak onto AI or
system messages, and the two typing directions must stay independent.
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.security.auth import get_current_user

AGENT_ID = "11111111-1111-1111-1111-111111111111"
CONV_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
AVATAR = "https://exwxdjfeoekfixnjsreq.supabase.co/storage/v1/object/public/avatars/a/b.png"


def _conv(**over) -> dict:
    base = {
        "id": CONV_ID,
        "tunnel": "sales",
        "mode": "human",
        "status": "active",
        "assigned_agent_id": AGENT_ID,
        "visitor_id": None,
        "metadata": {"engaged_agent_id": AGENT_ID},
    }
    base.update(over)
    return base


def _admin_client(role: str = "sales") -> TestClient:
    from app.api.conversations import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": AGENT_ID,
        "email": f"{role}@bbc.com",
        "name": "Roman",
        "role": role,
        "tunnel_scope": "sales",
    }
    return TestClient(app)


def _widget_client() -> TestClient:
    """Widget endpoints — no operator login, ownership-scoped only."""
    from app.api.chat import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


# ── 1-3. PART A: identity on operator messages ───────────────────────


def _send_as(operator: dict):
    from app.api import conversations as mod

    pushed: list[dict] = []

    async def _capture(_conv_id, payload):
        pushed.append(payload)

    with (
        patch.object(mod.db, "get_user_by_id", new_callable=AsyncMock, return_value=operator),
        patch.object(mod.db, "get_conversation", new_callable=AsyncMock, return_value=_conv()),
        patch.object(mod, "add_message", new_callable=AsyncMock,
                     return_value={"id": "m-1", "role": "agent", "content": "hello",
                                   "created_at": "2026-07-27T10:00:00+00:00"}),
        patch.object(mod.db, "count_agent_messages_since", new_callable=AsyncMock, return_value=2),
        patch.object(mod.db, "update_conversation", new_callable=AsyncMock, return_value=_conv()),
        patch.object(mod.db, "update_user_last_seen", new_callable=AsyncMock),
        patch.object(mod.manager, "push", new=_capture),
        patch("app.realtime.typing_indicator.typing_manager.clear_agent_typing",
              new_callable=AsyncMock) as cleared,
    ):
        resp = _admin_client().post(
            f"/api/conversations/{CONV_ID}/messages", json={"content": "hello"}
        )
    return resp, pushed, cleared


def test_agent_message_carries_name_and_avatar():
    resp, pushed, cleared = _send_as(
        {"id": AGENT_ID, "role": "sales", "name": "Roman", "avatar_url": AVATAR}
    )

    assert resp.status_code == 200, resp.text
    assert pushed and pushed[0]["agent_name"] == "Roman"
    assert pushed[0]["agent_avatar_url"] == AVATAR
    # Returned to the admin caller too
    assert resp.json()["data"]["agent_name"] == "Roman"
    # Sending a reply ends the typing indicator
    cleared.assert_awaited_once_with(CONV_ID)


def test_missing_avatar_keeps_name_and_nulls_photo():
    resp, pushed, _ = _send_as(
        {"id": AGENT_ID, "role": "sales", "name": "Roman", "avatar_url": None}
    )

    assert resp.status_code == 200, resp.text
    assert pushed[0]["agent_name"] == "Roman"
    assert pushed[0]["agent_avatar_url"] is None


def test_nameless_operator_falls_back_to_consultant():
    resp, pushed, _ = _send_as({"id": AGENT_ID, "role": "sales", "name": "", "avatar_url": None})

    assert resp.status_code == 200, resp.text
    assert pushed[0]["agent_name"] == "Consultant"


@pytest.mark.asyncio
async def test_identity_never_attached_to_ai_or_system_messages():
    from app.api.chat import _with_agent_identity

    msgs = [
        {"id": "1", "role": "user", "content": "hi"},
        {"id": "2", "role": "ai", "content": "AI reply"},
        {"id": "3", "role": "system", "content": "Roman has joined"},
        {"id": "4", "role": "agent", "content": "operator reply"},
    ]
    with patch("app.api.chat.db.get_conversation_agent_identity", new_callable=AsyncMock,
               return_value={"name": "Roman", "avatar_url": AVATAR}):
        out = await _with_agent_identity(CONV_ID, msgs)

    by_id = {m["id"]: m for m in out}
    assert by_id["4"]["agent_name"] == "Roman"
    assert by_id["4"]["agent_avatar_url"] == AVATAR
    for mid in ("1", "2", "3"):
        assert "agent_name" not in by_id[mid]
        assert "agent_avatar_url" not in by_id[mid]


@pytest.mark.asyncio
async def test_no_lookup_when_batch_has_no_operator_message():
    """Incremental polls return mostly AI/user rows — don't pay for a lookup."""
    from app.api.chat import _with_agent_identity

    msgs = [{"id": "1", "role": "ai", "content": "hi"}]
    with patch("app.api.chat.db.get_conversation_agent_identity",
               new_callable=AsyncMock) as lookup:
        out = await _with_agent_identity(CONV_ID, msgs)

    lookup.assert_not_awaited()
    assert out == msgs


# ── 4. PART B: the two typing directions never cross ─────────────────


class _FakeRedis:
    """Minimal SETEX/GET/DELETE store so both key namespaces are observable."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttls[key] = ttl

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)
        self.ttls.pop(key, None)


@pytest.mark.asyncio
async def test_agent_typing_does_not_touch_client_typing_key():
    from app.realtime.typing_indicator import TypingManager

    tm = TypingManager()
    fake = _FakeRedis()
    with patch.object(TypingManager, "_get_client", return_value=fake):
        await tm.set_typing(CONV_ID, "client draft")
        await tm.set_agent_typing(CONV_ID, "Roman")

        assert (await tm.get_typing(CONV_ID)) == {"is_typing": True, "text": "client draft"}
        assert (await tm.get_agent_typing(CONV_ID)) == {"is_typing": True, "name": "Roman"}
        assert set(fake.store) == {f"bbc:typing:{CONV_ID}", f"bbc:typing:agent:{CONV_ID}"}

        # Clearing one direction leaves the other intact
        await tm.clear_agent_typing(CONV_ID)
        assert (await tm.get_agent_typing(CONV_ID)) is None
        assert (await tm.get_typing(CONV_ID)) == {"is_typing": True, "text": "client draft"}

        await tm.clear_typing(CONV_ID)
        assert (await tm.get_typing(CONV_ID)) is None


@pytest.mark.asyncio
async def test_agent_typing_ttl_is_short_so_it_self_heals():
    from app.realtime.typing_indicator import TypingManager

    tm = TypingManager()
    fake = _FakeRedis()
    with patch.object(TypingManager, "_get_client", return_value=fake):
        await tm.set_agent_typing(CONV_ID, "Roman")

    assert fake.ttls[f"bbc:typing:agent:{CONV_ID}"] <= 30


@pytest.mark.asyncio
async def test_agent_typing_never_exposes_draft_text():
    from app.realtime.typing_indicator import TypingManager

    tm = TypingManager()
    fake = _FakeRedis()
    with patch.object(TypingManager, "_get_client", return_value=fake):
        await tm.set_agent_typing(CONV_ID, "Roman")
        state = await tm.get_agent_typing(CONV_ID)

    assert set(state) == {"is_typing", "name"}


# ── 5. Auth surface ──────────────────────────────────────────────────


def test_admin_typing_endpoint_sets_state_with_operator_name():
    from app.api import conversations as mod

    with (
        patch.object(mod.db, "get_conversation_simple", new_callable=AsyncMock,
                     return_value=_conv()),
        patch.object(mod.db, "get_user_by_id", new_callable=AsyncMock,
                     return_value={"id": AGENT_ID, "name": "Roman"}),
        patch("app.realtime.typing_indicator.typing_manager.set_agent_typing",
              new_callable=AsyncMock) as setter,
    ):
        resp = _admin_client().post(
            f"/api/conversations/{CONV_ID}/agent-typing", json={"text": "typing a reply"}
        )

    assert resp.status_code == 200, resp.text
    setter.assert_awaited_once_with(CONV_ID, "Roman")


def test_admin_typing_endpoint_empty_text_clears():
    from app.api import conversations as mod

    with (
        patch.object(mod.db, "get_conversation_simple", new_callable=AsyncMock,
                     return_value=_conv()),
        patch("app.realtime.typing_indicator.typing_manager.clear_agent_typing",
              new_callable=AsyncMock) as clearer,
    ):
        resp = _admin_client().post(
            f"/api/conversations/{CONV_ID}/agent-typing", json={"text": "   "}
        )

    assert resp.status_code == 200, resp.text
    clearer.assert_awaited_once_with(CONV_ID)


def _route_dependency_names(router, path: str, method: str) -> set[str]:
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return {d.call.__name__ for d in route.dependant.dependencies if d.call}
    raise AssertionError(f"route not found: {method} {path}")


def test_operator_typing_endpoints_sit_behind_operator_auth():
    """The write side is operator-only; asserted on the route (the test env has
    a debug auth bypass, so a live request proves nothing here)."""
    from app.api.conversations import router

    for method in ("POST", "DELETE"):
        assert "get_current_user" in _route_dependency_names(
            router, "/conversations/{conversation_id}/agent-typing", method
        )


def test_widget_typing_read_is_not_behind_operator_auth():
    """The visitor has no login — only visitor ownership guards the read side."""
    from app.api.chat import router

    deps = _route_dependency_names(router, "/chat/agent-typing/{conversation_id}", "GET")
    assert "get_current_user" not in deps
    assert "require_visitor_ownership" in deps


def test_widget_typing_read_needs_no_operator_login():
    from app.api import chat as chat_mod

    with (
        patch.object(chat_mod.db, "get_conversation_simple", new_callable=AsyncMock,
                     return_value=_conv()),
        patch("app.realtime.typing_indicator.typing_manager.get_agent_typing",
              new_callable=AsyncMock, return_value={"is_typing": True, "name": "Roman"}),
    ):
        resp = _widget_client().get(f"/api/chat/agent-typing/{CONV_ID}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"is_typing": True, "name": "Roman"}


def test_widget_typing_read_returns_idle_state_when_nobody_types():
    from app.api import chat as chat_mod

    with (
        patch.object(chat_mod.db, "get_conversation_simple", new_callable=AsyncMock,
                     return_value=_conv()),
        patch("app.realtime.typing_indicator.typing_manager.get_agent_typing",
              new_callable=AsyncMock, return_value=None),
    ):
        resp = _widget_client().get(f"/api/chat/agent-typing/{CONV_ID}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"is_typing": False, "name": ""}


def test_widget_typing_read_denied_for_wrong_visitor():
    """Ownership still applies — a stranger with the id but wrong visitor is out."""
    from app.api import chat as chat_mod
    from app.deps import ownership

    with (
        patch.object(ownership.db, "get_conversation_simple", new_callable=AsyncMock,
                     return_value=_conv(visitor_id="visitor-real")),
        patch.object(chat_mod.db, "get_conversation_simple", new_callable=AsyncMock,
                     return_value=_conv(visitor_id="visitor-real")),
    ):
        resp = _widget_client().get(
            f"/api/chat/agent-typing/{CONV_ID}",
            headers={"X-Visitor-Id": "visitor-impostor"},
        )

    assert resp.status_code == 403, resp.text
