"""Where the client came from (the ad, the campaign, the Google search, the pages, the replay of the visit) is not
an agent's (the owner, 25 Nov 2026: an agent can't open "Where they came from"). The rows an agent gets leave it
out, on a chat that is theirs as well; the brand, the card's moment and the screen stay. Everyone else's rows keep
it. Every name and address here is invented."""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

with patch("app.db.supabase.get_client"):
    from app.main import app

from app.security.auth import get_current_user
from app.security.pii import strip_marketing_metadata

AGENT = "aaaaaaaa-0000-4000-8000-0000000000a1"

SOURCES = {
    "utm_source": "kayak", "utm_medium": "cpc", "utm_campaign": "winter-sale", "utm_term": "business class rome",
    "gclid": "g-1", "msclkid": "m-1", "fbclid": "f-1", "referrer": "https://www.kayak.example/",
    "page_url": "https://www.site.example/flights/rome", "posthog_session_id": "0192f3a4-5b6c-7d8e-9f01-23456789abcd",
}
# The brand the agent answers for, why the card opened the chat, the screen and the presence.
KEPT = {"site": "bct", "proactive_trigger": "kayak", "user_agent": "Mozilla/5.0 (iPhone)", "widget_open": True}


def _as(role: str, user_id: str = AGENT):
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id, "role": role, "email": f"{role}@example.test", "name": role,
        "tunnel_scope": "sales" if role in ("sales", "support") else "all",
    }


@pytest.fixture(autouse=True)
def _clear():
    yield
    app.dependency_overrides.clear()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _chat(**over) -> dict:
    # The agent's own chat: taken, so nothing of it is blind to them.
    row = {
        "id": "c-1", "tunnel": "sales", "status": "active", "mode": "human", "assigned_agent_id": AGENT,
        "team_id": "alpha", "visitor_name": "Test Visitor", "metadata": {**SOURCES, **KEPT},
    }
    row.update(over)
    return row


async def _detail(role: str) -> dict:
    with patch("app.db.supabase.get_conversation", new_callable=AsyncMock, return_value=_chat()):
        _as(role)
        async with _client() as c:
            r = await c.get("/api/conversations/c-1")
    assert r.status_code == 200
    return r.json()["data"]


async def _list(role: str) -> dict:
    with (
        patch("app.db.supabase.get_conversations", new_callable=AsyncMock, return_value=([_chat()], 1)),
        patch("app.db.supabase.get_last_message_previews", new_callable=AsyncMock, return_value={}),
    ):
        _as(role)
        async with _client() as c:
            r = await c.get("/api/conversations?status=active&assigned_to=me")
    assert r.status_code == 200
    return r.json()["data"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["sales", "support"])
async def test_an_agent_gets_their_chat_without_where_the_client_came_from(role):
    meta = (await _detail(role))["metadata"]
    assert not set(SOURCES) & set(meta), sorted(set(SOURCES) & set(meta))
    assert meta == KEPT


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["sales", "support"])
async def test_an_agents_list_rows_come_without_it_too(role):
    row = await _list(role)
    assert not set(SOURCES) & set(row["metadata"])
    assert row["metadata"] == KEPT


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "admin", "dev", "qa"])
async def test_everyone_else_keeps_it(role):
    """Rule 5: the rows of a role that sees "Where they came from" are the rows as they were."""
    assert (await _detail(role))["metadata"] == {**SOURCES, **KEPT}
    assert (await _list(role))["metadata"] == {**SOURCES, **KEPT}


def test_a_bing_ad_click_is_marketing_too():
    # A supervisor's rows (mask_visitor_row) lost Google's and Facebook's ad clicks but kept Bing's.
    assert "msclkid" not in strip_marketing_metadata({"msclkid": "m-1", "site": "bbc"})
