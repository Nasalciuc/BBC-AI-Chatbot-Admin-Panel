"""Users & access — the list, the access log, what deactivating touches, and
who may change owners.

What is pinned here:
  * the list tells owner/admin/dev how each person signs in and where their
    invite stands — never the password hash, never the invite token;
  * a failed sign-in read still serves the list;
  * the access log comes from the rows the server already writes, with the
    role an invite was sent for;
  * only an owner changes owners (admin AND dev are refused, the invite
    path included), and nobody changes their own role.
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

from app.security.auth import get_current_user  # noqa: E402
from app.services.access_log import build_access_log  # noqa: E402

USERS = {
    "owner-1": {"id": "owner-1", "name": "Alex Moore", "role": "owner", "created_at": "2026-05-01T00:00:00+00:00"},
    "admin-1": {"id": "admin-1", "name": "Cristina Popa", "role": "admin", "created_at": "2026-05-01T00:00:00+00:00"},
    "elena": {"id": "elena", "name": "Elena Vasile", "role": "qa", "created_at": "2026-10-01T09:00:00+00:00"},
    "paul": {"id": "paul", "name": "Paul Ursu", "role": "sales", "created_at": "2026-11-06T13:52:00+00:00"},
}


def test_access_log_reads_what_the_server_writes():
    log = build_access_log(
        audit=[
            {"target_user_id": "elena", "changed_by_user_id": "owner-1", "old_role": "supervisor", "new_role": "qa",
             "old_tunnel_scope": "sales", "new_tunnel_scope": "all", "old_is_active": True, "new_is_active": True,
             "changed_fields": ["role", "tunnel_scope"], "created_at": "2026-11-05T18:20:00+00:00"},
            {"target_user_id": "paul", "changed_by_user_id": "owner-1", "old_is_active": True, "new_is_active": False,
             "changed_fields": ["is_active"], "created_at": "2026-11-07T10:00:00+00:00"},
        ],
        toggles=[{"user_id": "admin-1", "action": "chat_enabled_toggle", "target_id": "paul",
                  "details": {"from": False, "to": True}, "created_at": "2026-11-06T14:00:00+00:00"}],
        invites=[
            {"user_id": "paul", "created_by": "admin-1", "created_at": "2026-11-06T13:52:30+00:00"},
            # Elena was invited as a supervisor, before the change to QA.
            {"user_id": "elena", "created_by": "owner-1", "created_at": "2026-10-01T09:00:10+00:00"},
        ],
        users=USERS,
    )
    assert [(e["kind"], e["target_name"]) for e in log] == [
        ("deactivated", "Paul Ursu"),
        ("chats_on", "Paul Ursu"),
        ("invited", "Paul Ursu"),
        ("role", "Elena Vasile"),
        ("invited", "Elena Vasile"),
    ]
    assert log[3]["old"] == "supervisor" and log[3]["new"] == "qa" and log[3]["actor_name"] == "Alex Moore"
    assert log[4]["role"] == "supervisor" and log[2]["role"] == "sales"


def test_a_later_invite_to_an_old_account_is_not_the_first_one():
    log = build_access_log(audit=[], toggles=[], users=USERS,
                           invites=[{"user_id": "elena", "created_by": "owner-1", "created_at": "2026-11-01T09:00:00+00:00"}])
    assert log[0]["kind"] == "invite_again"


def test_the_log_is_in_time_order_whatever_the_offset():
    # 10:00+02:00 is 08:00 UTC: earlier than 09:30 UTC, though it sorts after it as text.
    log = build_access_log(audit=[], toggles=[], users=USERS, invites=[
        {"user_id": "paul", "created_by": "admin-1", "created_at": "2026-11-07T10:00:00+02:00"},
        {"user_id": "elena", "created_by": "owner-1", "created_at": "2026-11-07T09:30:00+00:00"},
    ])
    assert [e["target_name"] for e in log] == ["Elena Vasile", "Paul Ursu"]
    assert log[1]["at"] == "2026-11-07T08:00:00+00:00"


# ── Endpoints ────────────────────────────────────────────────────────


def _users_client(role: str, uid: str = "actor-1") -> TestClient:
    from app.api.users import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": uid, "role": role, "tunnel_scope": "all",
                                                          "email": "a@example.com"}
    return TestClient(app)


LIST = [{"id": "paul", "name": "Paul Ursu", "role": "sales", "is_active": True, "last_seen_at": None}]


def test_list_tells_how_each_person_signs_in_without_secrets():
    facts = {"paul": {"has_password": False, "invite": {"expires_at": "2026-11-08T13:52:00+00:00", "used_at": None,
                                                        "sent_at": "2026-11-06T13:52:00+00:00", "sent_by": "admin-1"}}}
    with patch("app.db.supabase.get_users", new=AsyncMock(return_value=([dict(r) for r in LIST], 1))), \
         patch("app.db.supabase.get_sign_in_facts", new=AsyncMock(return_value=facts)):
        body = _users_client("admin").get("/api/admin/users").json()
    row = body["data"][0]
    assert row["has_password"] is False and row["invite"]["expires_at"].startswith("2026-11-08")
    assert "password_hash" not in row and "token" not in row["invite"]


def test_a_failed_sign_in_read_still_serves_the_list():
    with patch("app.db.supabase.get_users", new=AsyncMock(return_value=([dict(r) for r in LIST], 1))), \
         patch("app.db.supabase.get_sign_in_facts", new=AsyncMock(side_effect=RuntimeError("boom"))):
        r = _users_client("owner").get("/api/admin/users")
    assert r.status_code == 200 and "invite" not in r.json()["data"][0]


def test_supervisors_list_without_sign_in_facts():
    facts = AsyncMock(return_value={})
    with patch("app.db.supabase.get_users", new=AsyncMock(return_value=([dict(r) for r in LIST], 1))), \
         patch("app.db.supabase.get_team_ids_for_supervisor", new=AsyncMock(return_value=["t1"])), \
         patch("app.db.supabase.get_sign_in_facts", new=facts):
        assert _users_client("supervisor").get("/api/admin/users").status_code == 200
    assert facts.await_count == 0


@pytest.mark.parametrize("role", ["supervisor", "project_manager", "qa", "sales"])
def test_access_log_and_handover_are_for_owner_admin_dev(role):
    c = _users_client(role)
    assert c.get("/api/admin/access-log").status_code == 403
    assert c.get("/api/admin/users/paul/handover").status_code == 403


def test_handover_failure_is_a_502():
    with patch("app.db.supabase.get_handover_facts", new=AsyncMock(side_effect=RuntimeError("boom"))):
        assert _users_client("owner").get("/api/admin/users/paul/handover").status_code == 502


def test_handover_says_the_sticky_window():
    facts = {"open_chats": [{"id": "c1", "chat_number": 4810, "tunnel": "sales", "first_name": "Michael"}],
             "clients_90_days": 34, "leads": 7}
    with patch("app.db.supabase.get_handover_facts", new=AsyncMock(return_value=facts)):
        body = _users_client("admin").get("/api/admin/users/victor/handover").json()
    assert body["data"]["window_days"] == 90 and body["data"]["clients_90_days"] == 34


@pytest.mark.parametrize("actor", ["admin", "dev"])
def test_only_an_owner_changes_owners(actor):
    owner_row = {"id": "owner-1", "role": "owner", "is_active": True}
    sales_row = {"id": "paul", "role": "sales", "is_active": True}
    c = _users_client(actor)
    with patch("app.db.supabase.get_user_by_id", new=AsyncMock(return_value=owner_row)), \
         patch("app.db.supabase.update_user", new=AsyncMock(return_value=owner_row)) as upd:
        assert c.patch("/api/admin/users/owner-1", json={"is_active": False}).status_code == 403
    assert upd.await_count == 0
    with patch("app.db.supabase.get_user_by_id", new=AsyncMock(return_value=sales_row)), \
         patch("app.db.supabase.update_user", new=AsyncMock(return_value=sales_row)) as upd:
        assert c.patch("/api/admin/users/paul", json={"role": "owner"}).status_code == 403
        # Everything else still works for them.
        with patch("app.db.supabase.create_user_access_audit", new=AsyncMock()):
            assert c.patch("/api/admin/users/paul", json={"role": "support", "tunnel_scope": "support"}).status_code == 200


def test_nobody_changes_their_own_role():
    me = {"id": "owner-1", "role": "owner", "is_active": True}
    with patch("app.db.supabase.get_user_by_id", new=AsyncMock(return_value=me)), \
         patch("app.db.supabase.update_user", new=AsyncMock(return_value=me)) as upd:
        r = _users_client("owner", uid="owner-1").patch("/api/admin/users/owner-1", json={"role": "admin"})
    assert r.status_code == 403 and upd.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["admin", "dev"])
async def test_an_invite_is_not_the_way_around_it(actor):
    from fastapi import HTTPException
    from app.api import auth_routes
    from app.api.auth_routes import InviteRequest

    req = InviteRequest(email="new.owner@example.com", name="New Owner", role="owner", tunnel_scope="all")
    with patch("app.db.supabase.get_user_by_email", new=AsyncMock(return_value=None)), \
         patch("app.db.supabase.create_user", new=AsyncMock()) as create:
        with pytest.raises(HTTPException) as exc:
            await auth_routes.invite_user(req, {"role": actor, "id": "x"})
    assert exc.value.status_code == 403 and create.await_count == 0
