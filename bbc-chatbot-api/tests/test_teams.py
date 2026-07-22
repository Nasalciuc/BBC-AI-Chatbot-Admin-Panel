"""Teams Phase 1 — role registration, admin CRUD, and the PM-cannot-see-leads trap.

Auth is injected via FastAPI dependency_overrides on get_current_user, so each
test can act as an arbitrary role without minting real JWTs.
"""

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
from app.api.users import VALID_ROLES, CAN_LIST_USERS, PRIVILEGED
from app.db.supabase import _MANAGEMENT_ROLES, _OPERATOR_ROLES, _HANDS_ON_ROLES


def _as(role: str, user_id: str = "u-1"):
    """Override the auth dependency to act as the given role."""
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id,
        "role": role,
        "email": f"{role}@bbc.com",
        "name": role,
        "tunnel_scope": "all",
    }


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── 1 & 2. Role registration ────────────────────────────────────────


def test_project_manager_in_valid_roles():
    assert "project_manager" in VALID_ROLES
    assert "project_manager" in CAN_LIST_USERS
    assert "project_manager" not in PRIVILEGED


def test_project_manager_role_classification():
    assert "project_manager" in _MANAGEMENT_ROLES
    assert "project_manager" not in _OPERATOR_ROLES
    assert "project_manager" not in _HANDS_ON_ROLES


# ── 3. THE TRAP: PM must NOT see leads ───────────────────────────────


@pytest.mark.asyncio
async def test_pm_forbidden_on_leads():
    _as("project_manager")
    async with _client() as c:
        r = await c.get("/api/leads")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_supervisor_and_qa_still_access_leads():
    with patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=([], 0)):
        for role in ("supervisor", "qa", "owner", "admin"):
            _as(role)
            async with _client() as c:
                r = await c.get("/api/leads")
            assert r.status_code == 200, f"{role} should access leads"


# ── 4. Create teams: only PRIVILEGED ─────────────────────────────────


@pytest.mark.asyncio
async def test_create_team_privileged_ok():
    created = {"id": "t-1", "name": "Alpha", "is_active": True}
    with (
        patch("app.db.supabase.get_teams", new_callable=AsyncMock, return_value=[]),
        patch("app.db.supabase.create_team", new_callable=AsyncMock, return_value=created),
    ):
        _as("owner")
        async with _client() as c:
            r = await c.post("/api/admin/teams", json={"name": "Alpha"})
    assert r.status_code == 200
    assert r.json()["data"]["id"] == "t-1"


@pytest.mark.asyncio
async def test_create_team_forbidden_for_non_privileged():
    # PM can now create teams (see test below); supervisor/sales/qa cannot.
    for role in ("supervisor", "sales", "qa"):
        _as(role)
        async with _client() as c:
            r = await c.post("/api/admin/teams", json={"name": "Alpha"})
        assert r.status_code == 403, f"{role} must not create teams"


@pytest.mark.asyncio
async def test_project_manager_can_create_team_owns_it():
    """PM creates a team; pm_id is forced to the creating PM (they own it)."""
    pm_user = {"id": "pm-1", "role": "project_manager"}
    captured = {}

    async def _fake_create(payload):
        captured.update(payload)
        return {"id": "t-9", **payload, "is_active": True}

    with (
        patch("app.db.supabase.get_user_by_id", new_callable=AsyncMock, return_value=pm_user),
        patch("app.db.supabase.get_teams", new_callable=AsyncMock, return_value=[]),
        patch("app.db.supabase.create_team", side_effect=_fake_create),
    ):
        _as("project_manager", "pm-1")
        async with _client() as c:
            r = await c.post("/api/admin/teams", json={"name": "Alpha", "pm_id": "someone-else"})
    assert r.status_code == 200
    # pm_id forced to the creator, ignoring the body value
    assert captured.get("pm_id") == "pm-1"


@pytest.mark.asyncio
async def test_pm_can_update_own_team_but_not_others():
    own = {"id": "t-own", "pm_id": "pm-1", "is_active": True, "name": "Mine"}
    other = {"id": "t-other", "pm_id": "pm-2", "is_active": True, "name": "Theirs"}
    with (
        patch("app.db.supabase.get_team", new_callable=AsyncMock, side_effect=lambda tid: own if tid == "t-own" else other),
        patch("app.db.supabase.update_team", new_callable=AsyncMock, return_value={"id": "t-own", "name": "Renamed"}),
    ):
        _as("project_manager", "pm-1")
        async with _client() as c:
            ok = await c.patch("/api/admin/teams/t-own", json={"name": "Renamed"})
            forbidden = await c.patch("/api/admin/teams/t-other", json={"name": "Hijack"})
    assert ok.status_code == 200
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_pm_can_delete_own_team_only():
    own = {"id": "t-own", "pm_id": "pm-1", "is_active": True, "name": "Mine"}
    other = {"id": "t-other", "pm_id": "pm-2", "is_active": True, "name": "Theirs"}
    with (
        patch("app.db.supabase.get_team", new_callable=AsyncMock, side_effect=lambda tid: own if tid == "t-own" else other),
        patch("app.db.supabase.count_team_members", new_callable=AsyncMock, return_value=0),
        patch("app.db.supabase.update_team", new_callable=AsyncMock, return_value={"id": "t-own", "is_active": False}),
    ):
        _as("project_manager", "pm-1")
        async with _client() as c:
            ok = await c.delete("/api/admin/teams/t-own")
            forbidden = await c.delete("/api/admin/teams/t-other")
    assert ok.status_code == 200
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_pm_can_assign_operator_to_own_team_only():
    with (
        patch("app.db.supabase.get_team_ids_for_pm", new_callable=AsyncMock, return_value=["t-own"]),
        patch("app.db.supabase.get_team", new_callable=AsyncMock, return_value={"id": "t-own", "is_active": True}),
        patch("app.db.supabase.get_user_by_id", new_callable=AsyncMock,
              return_value={"id": "op-1", "role": "sales", "team_id": None}),
        patch("app.db.supabase.update_user", new_callable=AsyncMock,
              return_value={"id": "op-1", "team_id": "t-own"}),
        patch("app.db.supabase.create_user_access_audit", new_callable=AsyncMock, return_value=None),
    ):
        _as("project_manager", "pm-1")
        async with _client() as c:
            ok = await c.patch("/api/admin/users/op-1", json={"team_id": "t-own"})
            forbidden = await c.patch("/api/admin/users/op-1", json={"team_id": "t-not-mine"})
    assert ok.status_code == 200
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_pm_cannot_change_user_role():
    """PM may only touch team_id — never role/tunnel/is_active."""
    _as("project_manager", "pm-1")
    async with _client() as c:
        r = await c.patch("/api/admin/users/op-1", json={"role": "admin"})
    assert r.status_code == 403


# ── 5. List scoping by role ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_teams_scoping():
    with patch("app.db.supabase.get_teams", new_callable=AsyncMock, return_value=[]) as gt:
        _as("admin", "admin-1")
        async with _client() as c:
            await c.get("/api/admin/teams")
        assert gt.await_args.kwargs.get("pm_id") is None
        assert gt.await_args.kwargs.get("supervisor_id") is None

        gt.reset_mock()
        _as("project_manager", "pm-1")
        async with _client() as c:
            await c.get("/api/admin/teams")
        assert gt.await_args.kwargs.get("pm_id") == "pm-1"

        gt.reset_mock()
        _as("supervisor", "sup-1")
        async with _client() as c:
            await c.get("/api/admin/teams")
        assert gt.await_args.kwargs.get("supervisor_id") == "sup-1"


@pytest.mark.asyncio
async def test_list_teams_forbidden_for_operator():
    _as("sales")
    async with _client() as c:
        r = await c.get("/api/admin/teams")
    assert r.status_code == 403


# ── 6. One supervisor ⇒ one active team ──────────────────────────────


@pytest.mark.asyncio
async def test_create_team_supervisor_already_owns_409():
    supervisor = {"id": "sup-1", "role": "supervisor"}
    with (
        patch("app.db.supabase.get_user_by_id", new_callable=AsyncMock, return_value=supervisor),
        patch("app.db.supabase.get_teams", new_callable=AsyncMock,
              return_value=[{"id": "t-existing", "name": "Beta"}]),
        patch("app.db.supabase.create_team", new_callable=AsyncMock) as ct,
    ):
        _as("owner")
        async with _client() as c:
            r = await c.post("/api/admin/teams", json={"name": "Alpha", "supervisor_id": "sup-1"})
    assert r.status_code == 409
    ct.assert_not_awaited()


# ── 7. Role validation for supervisor_id / pm_id ─────────────────────


@pytest.mark.asyncio
async def test_create_team_supervisor_id_must_be_supervisor():
    not_supervisor = {"id": "x-1", "role": "sales"}
    with patch("app.db.supabase.get_user_by_id", new_callable=AsyncMock, return_value=not_supervisor):
        _as("owner")
        async with _client() as c:
            r = await c.post("/api/admin/teams", json={"name": "Alpha", "supervisor_id": "x-1"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_team_pm_id_must_be_project_manager():
    not_pm = {"id": "x-2", "role": "supervisor"}
    with patch("app.db.supabase.get_user_by_id", new_callable=AsyncMock, return_value=not_pm):
        _as("owner")
        async with _client() as c:
            r = await c.post("/api/admin/teams", json={"name": "Alpha", "pm_id": "x-2"})
    assert r.status_code == 400


# ── 8. User team_id assignment ───────────────────────────────────────


@pytest.mark.asyncio
async def test_update_user_valid_team_id_ok():
    active_team = {"id": "t-1", "is_active": True}
    existing_user = {"id": "u-9", "role": "sales", "tunnel_scope": "sales", "is_active": True}
    updated = {**existing_user, "team_id": "t-1"}
    with (
        patch("app.db.supabase.get_team", new_callable=AsyncMock, return_value=active_team),
        patch("app.db.supabase.get_user_by_id", new_callable=AsyncMock, return_value=existing_user),
        patch("app.db.supabase.update_user", new_callable=AsyncMock, return_value=updated),
    ):
        _as("owner")
        async with _client() as c:
            r = await c.patch("/api/admin/users/u-9", json={"team_id": "t-1"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_update_user_nonexistent_team_400():
    with patch("app.db.supabase.get_team", new_callable=AsyncMock, return_value=None):
        _as("owner")
        async with _client() as c:
            r = await c.patch("/api/admin/users/u-9", json={"team_id": "ghost"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_user_null_team_id_ok():
    existing_user = {"id": "u-9", "role": "sales", "tunnel_scope": "sales", "is_active": True}
    updated = {**existing_user, "team_id": None}
    with (
        patch("app.db.supabase.get_user_by_id", new_callable=AsyncMock, return_value=existing_user),
        patch("app.db.supabase.update_user", new_callable=AsyncMock, return_value=updated) as uu,
        patch("app.db.supabase.create_user_access_audit", new_callable=AsyncMock, return_value=None),
    ):
        _as("owner")
        async with _client() as c:
            r = await c.patch("/api/admin/users/u-9", json={"team_id": None})
    assert r.status_code == 200
    # explicit null must be forwarded to the DB (not dropped by exclude_none)
    assert uu.await_args.args[1].get("team_id") is None


# ── 9. Regression: existing roles unchanged ──────────────────────────


@pytest.mark.asyncio
async def test_regression_sales_still_tunnel_forced_on_conversations():
    # sales is not privileged in _enforce_tunnel → asking for the other tunnel is 403.
    with patch("app.db.supabase.get_conversations", new_callable=AsyncMock, return_value=([], 0)):
        _as("sales")
        # sales tunnel_scope defaults to 'all' in our stub, so force a mismatch:
        app.dependency_overrides[get_current_user] = lambda: {
            "id": "s-1", "role": "sales", "tunnel_scope": "sales",
        }
        async with _client() as c:
            r = await c.get("/api/conversations?tunnel=support")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_regression_owner_conversations_ok():
    with patch("app.db.supabase.get_conversations", new_callable=AsyncMock, return_value=([], 0)):
        _as("owner")
        async with _client() as c:
            r = await c.get("/api/conversations")
    assert r.status_code == 200
