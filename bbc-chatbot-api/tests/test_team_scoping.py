"""Teams Phase 2 — team-scoped visibility + team_id stamping (frozen history).

Auth is injected via dependency_overrides on get_current_user. DB is mocked
(AsyncMock) per repo conventions (see test_teams.py). These tests assert the
CONTRACT the API enforces: which team_ids it passes to the query builder, the
fail-closed empty paths, the message-read guard, and that team scoping ANDs
with (never widens via) other query params.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

with patch("app.db.supabase.get_client"):
    from app.main import app

from app.security.auth import get_current_user

SUP = "11111111-1111-1111-1111-111111111111"
PM = "22222222-2222-2222-2222-222222222222"
OWNER = "33333333-3333-3333-3333-333333333333"
SALES = "44444444-4444-4444-4444-444444444444"
QA = "55555555-5555-5555-5555-555555555555"
CONV = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _as(role: str, user_id: str = "u-1", tunnel_scope: str = "all"):
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id, "role": role, "email": f"{role}@bbc.com",
        "name": role, "tunnel_scope": tunnel_scope,
    }


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── 1. Stamp conversation.team_id on assignment ──────────────────────


@pytest.mark.asyncio
async def test_stamp_team_id_on_assign():
    from app.services.handoff import perform_handoff_to_agent
    conv_cur = {"assigned_agent_id": None, "metadata": {}}
    operator = {"id": "op-1", "team_id": "alpha", "role": "sales"}
    with (
        patch("app.db.supabase.get_conversation_simple", new_callable=AsyncMock, return_value=conv_cur),
        patch("app.db.supabase.get_user_by_id", new_callable=AsyncMock, return_value=operator),
        patch("app.db.supabase.update_conversation", new_callable=AsyncMock, return_value={}) as upd,
        patch("app.services.presence.log_activity", new=MagicMock()),
        patch("app.pipeline.orchestrator._fire_and_forget", new=MagicMock()),
    ):
        await perform_handoff_to_agent(CONV, agent_id="op-1", emit_messages=False)
    payload = upd.await_args.args[1]
    assert payload.get("team_id") == "alpha"
    assert payload.get("assigned_agent_id") == "op-1"


@pytest.mark.asyncio
async def test_stamp_not_applied_when_operator_has_no_team():
    from app.services.handoff import perform_handoff_to_agent
    conv_cur = {"assigned_agent_id": None, "metadata": {}}
    operator = {"id": "op-2", "team_id": None, "role": "sales"}
    with (
        patch("app.db.supabase.get_conversation_simple", new_callable=AsyncMock, return_value=conv_cur),
        patch("app.db.supabase.get_user_by_id", new_callable=AsyncMock, return_value=operator),
        patch("app.db.supabase.update_conversation", new_callable=AsyncMock, return_value={}) as upd,
        patch("app.services.presence.log_activity", new=MagicMock()),
        patch("app.pipeline.orchestrator._fire_and_forget", new=MagicMock()),
    ):
        await perform_handoff_to_agent(CONV, agent_id="op-2", emit_messages=False)
    payload = upd.await_args.args[1]
    # never overwrite a (possibly already-stamped) team with NULL
    assert "team_id" not in payload


# ── 2. Fallback must NOT clear team_id ───────────────────────────────


@pytest.mark.asyncio
async def test_fallback_does_not_clear_team_id():
    from app.services.handoff import fall_back_to_ai
    conv_cur = {"assigned_agent_id": "op-1", "team_id": "alpha", "metadata": {}}
    with (
        patch("app.db.supabase.get_conversation_simple", new_callable=AsyncMock, return_value=conv_cur),
        patch("app.db.supabase.update_conversation", new_callable=AsyncMock, return_value={}) as upd,
        patch("app.services.handoff._handoff_phrase_recently_sent", new_callable=AsyncMock, return_value=False),
        patch("app.services.handoff._safe_system_msg", new_callable=AsyncMock, return_value=None),
        patch("app.db.supabase.get_recent_messages", new_callable=AsyncMock, return_value=[]),
        patch("app.services.presence.log_activity", new=MagicMock()),
        patch("app.pipeline.orchestrator._fire_and_forget", new=MagicMock()),
    ):
        await fall_back_to_ai(CONV)
    payload = upd.await_args.args[1]
    assert payload.get("assigned_agent_id") is None
    assert "team_id" not in payload  # team stays frozen; only agent is cleared


# ── 3. Supervisor sees own team's conversations ──────────────────────


@pytest.mark.asyncio
async def test_supervisor_conversations_scoped_to_team():
    with (
        patch("app.db.supabase.get_team_ids_for_supervisor", new_callable=AsyncMock, return_value=["alpha"]),
        patch("app.db.supabase.get_conversations", new_callable=AsyncMock, return_value=([], 0)) as gc,
    ):
        _as("supervisor", SUP)
        async with _client() as c:
            r = await c.get("/api/conversations")
    assert r.status_code == 200
    assert gc.await_args.kwargs.get("team_ids") == ["alpha"]


# ── 4. Supervisor with NO team → empty (fail closed) ─────────────────


@pytest.mark.asyncio
async def test_supervisor_no_team_sees_nothing():
    with (
        patch("app.db.supabase.get_team_ids_for_supervisor", new_callable=AsyncMock, return_value=[]),
        patch("app.db.supabase.get_conversations", new_callable=AsyncMock, return_value=([{"id": "leak"}], 1)) as gc,
    ):
        _as("supervisor", SUP)
        async with _client() as c:
            r = await c.get("/api/conversations")
    assert r.status_code == 200
    assert r.json()["count"] == 0
    assert r.json()["data"] == []
    gc.assert_not_awaited()  # never query without a team filter


# ── 5. PM sees its teams; not others ─────────────────────────────────


@pytest.mark.asyncio
async def test_pm_conversations_scoped_to_managed_teams():
    with (
        patch("app.db.supabase.get_team_ids_for_pm", new_callable=AsyncMock, return_value=["beta", "gamma"]),
        patch("app.db.supabase.get_conversations", new_callable=AsyncMock, return_value=([], 0)) as gc,
    ):
        _as("project_manager", PM)
        async with _client() as c:
            r = await c.get("/api/conversations")
    assert r.status_code == 200
    assert gc.await_args.kwargs.get("team_ids") == ["beta", "gamma"]


# ── 6. Message-read guard ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_supervisor_reads_own_team_messages():
    with (
        patch("app.db.supabase.get_team_ids_for_supervisor", new_callable=AsyncMock, return_value=["alpha"]),
        patch("app.db.supabase.get_conversation_simple", new_callable=AsyncMock, return_value={"team_id": "alpha"}),
        patch("app.db.supabase.get_messages_after", new_callable=AsyncMock, return_value=[{"id": "m1"}]),
    ):
        _as("supervisor", SUP)
        async with _client() as c:
            r = await c.get(f"/api/conversations/{CONV}/messages")
    assert r.status_code == 200
    assert r.json()["data"] == [{"id": "m1"}]


@pytest.mark.asyncio
async def test_supervisor_cannot_read_other_team_messages():
    with (
        patch("app.db.supabase.get_team_ids_for_supervisor", new_callable=AsyncMock, return_value=["alpha"]),
        patch("app.db.supabase.get_conversation_simple", new_callable=AsyncMock, return_value={"team_id": "other"}),
        patch("app.db.supabase.get_messages_after", new_callable=AsyncMock, return_value=[{"id": "m1"}]) as gm,
    ):
        _as("supervisor", SUP)
        async with _client() as c:
            r = await c.get(f"/api/conversations/{CONV}/messages")
    assert r.status_code == 403
    gm.assert_not_awaited()


# ── 7. Leads scoped for supervisor; PM still 403 ─────────────────────


@pytest.mark.asyncio
async def test_supervisor_leads_scoped_to_team():
    with (
        patch("app.db.supabase.get_team_ids_for_supervisor", new_callable=AsyncMock, return_value=["alpha"]),
        patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=([], 0)) as gl,
        patch("app.db.supabase.get_leads_review_counts", new_callable=AsyncMock, return_value=(0, 0)),
    ):
        _as("supervisor", SUP)
        async with _client() as c:
            r = await c.get("/api/leads")
    assert r.status_code == 200
    assert gl.await_args.kwargs.get("team_ids") == ["alpha"]


@pytest.mark.asyncio
async def test_supervisor_no_team_leads_empty():
    with (
        patch("app.db.supabase.get_team_ids_for_supervisor", new_callable=AsyncMock, return_value=[]),
        patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=([{"id": "leak"}], 1)) as gl,
    ):
        _as("supervisor", SUP)
        async with _client() as c:
            r = await c.get("/api/leads")
    assert r.status_code == 200
    assert r.json()["count"] == 0
    gl.assert_not_awaited()


@pytest.mark.asyncio
async def test_pm_still_403_on_leads():
    _as("project_manager", PM)
    async with _client() as c:
        r = await c.get("/api/leads")
    assert r.status_code == 403


# ── 8. Users list scoped for supervisor/PM ───────────────────────────


@pytest.mark.asyncio
async def test_supervisor_users_scoped_to_team_plus_self():
    with (
        patch("app.db.supabase.get_team_ids_for_supervisor", new_callable=AsyncMock, return_value=["alpha"]),
        patch("app.db.supabase.get_users", new_callable=AsyncMock, return_value=([], 0)) as gu,
    ):
        _as("supervisor", SUP)
        async with _client() as c:
            r = await c.get("/api/admin/users")
    assert r.status_code == 200
    assert gu.await_args.kwargs.get("team_ids") == ["alpha"]
    assert gu.await_args.kwargs.get("include_self_id") == SUP


@pytest.mark.asyncio
async def test_pm_users_not_scoped_for_staffing():
    # PM now manages teams, so they list the full operator pool (team_ids None),
    # like owner/admin — needed to add/move operators. (Supersedes the earlier
    # Phase-2 PM user-scoping.)
    with patch("app.db.supabase.get_users", new_callable=AsyncMock, return_value=([], 0)) as gu:
        _as("project_manager", PM)
        async with _client() as c:
            r = await c.get("/api/admin/users")
    assert r.status_code == 200
    assert gu.await_args.kwargs.get("team_ids") is None


# ── 9. Regression: privileged/operator roles unchanged ───────────────


@pytest.mark.asyncio
async def test_owner_conversations_not_scoped():
    with patch("app.db.supabase.get_conversations", new_callable=AsyncMock, return_value=([], 0)) as gc:
        _as("owner", OWNER)
        async with _client() as c:
            r = await c.get("/api/conversations")
    assert r.status_code == 200
    assert gc.await_args.kwargs.get("team_ids") is None


@pytest.mark.asyncio
async def test_owner_leads_not_scoped():
    with (
        patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=([], 0)) as gl,
        patch("app.db.supabase.get_leads_review_counts", new_callable=AsyncMock, return_value=(0, 0)),
    ):
        _as("owner", OWNER)
        async with _client() as c:
            r = await c.get("/api/leads")
    assert r.status_code == 200
    assert gl.await_args.kwargs.get("team_ids") is None


@pytest.mark.asyncio
async def test_qa_leads_still_all():
    with (
        patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=([], 0)) as gl,
        patch("app.db.supabase.get_leads_review_counts", new_callable=AsyncMock, return_value=(0, 0)),
    ):
        _as("qa", QA)
        async with _client() as c:
            r = await c.get("/api/leads")
    assert r.status_code == 200
    assert gl.await_args.kwargs.get("team_ids") is None
    assert gl.await_args.kwargs.get("assigned_to") == "all"


@pytest.mark.asyncio
async def test_sales_conversations_not_team_scoped():
    with patch("app.db.supabase.get_conversations", new_callable=AsyncMock, return_value=([], 0)) as gc:
        _as("sales", SALES, tunnel_scope="sales")
        async with _client() as c:
            r = await c.get("/api/conversations")
    assert r.status_code == 200
    assert gc.await_args.kwargs.get("team_ids") is None


# ── 10. Leak guard: team filter ANDs with tunnel/status params ───────


@pytest.mark.asyncio
async def test_team_filter_cannot_be_widened_by_other_params():
    with (
        patch("app.db.supabase.get_team_ids_for_supervisor", new_callable=AsyncMock, return_value=["alpha"]),
        patch("app.db.supabase.get_conversations", new_callable=AsyncMock, return_value=([], 0)) as gc,
    ):
        _as("supervisor", SUP)
        async with _client() as c:
            r = await c.get("/api/conversations?tunnel=support&status=active&assigned_to=all")
    assert r.status_code == 200
    kw = gc.await_args.kwargs
    # team filter is ALWAYS applied, regardless of tunnel/status/assigned_to
    assert kw.get("team_ids") == ["alpha"]
    assert kw.get("tunnel") == "support"
