"""The follow-up lottery at the edges: Offers to send, the chat detail, the heartbeat, giving by hand.

Every name, contact and route here is invented."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import conversations as conv_api
from app.api import followups, offers
from app.db import supabase as db
from app.infra import redis_client
from app.security.auth import get_current_user
from tests.fake_redis import FakeRedis

C1 = "aaaaaaaa-0000-4000-8000-000000000001"
ME = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
NOW = datetime.now(timezone.utc)


def _client(router, role="sales", uid=ME):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": uid, "role": role, "tunnel_scope": "sales",
                                                          "email": "agent@example.com"}
    return TestClient(app)


def _lead(cid=C1, **over):
    row = {
        "id": "lead-1", "conversation_id": cid, "chat_number": 101,
        "created_at": (NOW - timedelta(hours=1)).isoformat(), "origin_code": "NYC", "destination_code": "LON",
        "route_display": "NYC → LON", "visitor_name": "Dana Example", "visitor_email": "dana@example.com",
        "visitor_phone": None, "tunnel": "sales", "conversation_status": "closed", "conversation_mode": "ai",
        "team_id": "team-1", "assigned_agent_id": None, "closed_by_agent_id": None,
    }
    row.update(over)
    return row


@pytest.fixture
def lottery_on(monkeypatch):
    monkeypatch.setenv("FOLLOWUP_LOTTERY", "true")


def _universe(rows, now=None):
    """The universe as cached, and the chats as they are now (`now`: {cid: overrides}, else as cached)."""
    by_id = {r["conversation_id"]: r for r in rows}

    def fresh(ids):
        out = {}
        for i in ids:
            r = by_id.get(i, {})
            out[i] = {"id": i, "status": r.get("conversation_status", "closed"), "mode": r.get("conversation_mode"),
                      "assigned_agent_id": r.get("assigned_agent_id"), "engaged_agent_id": r.get("engaged_agent_id"),
                      "closed_by_agent_id": r.get("closed_by_agent_id"),
                      **((now or {}).get(i) or {})}
        return out

    return patch.multiple(
        db,
        get_leads_universe=AsyncMock(return_value=rows),
        get_user_names=AsyncMock(return_value={ME: "Me Agent", OTHER: "Other Agent"}),
        get_blind_inputs_many=AsyncMock(side_effect=fresh),
    )


def test_a_lead_moved_to_another_agent_hides_its_contact_before_the_cache_knows(lottery_on):
    with _universe([_lead(assigned_agent_id=ME)], now={C1: {"assigned_agent_id": OTHER}}):
        row = _client(offers.router).get("/api/offers").json()["data"]["waiting"][0]
    assert row["hidden"] and row["email"] is None


def test_a_lead_taken_back_is_nobodys_before_the_cache_knows(lottery_on):
    with _universe([_lead(assigned_agent_id=ME)], now={C1: {"assigned_agent_id": None}}):
        row = _client(offers.router).get("/api/offers").json()["data"]["waiting"][0]
    assert row["blind"] and row["email"] is None


def test_a_lead_whose_owner_now_cannot_be_read_keeps_its_contact(lottery_on):
    with _universe([_lead(assigned_agent_id=ME)]), \
            patch.object(db, "get_blind_inputs_many", new=AsyncMock(side_effect=RuntimeError("down"))):
        row = _client(offers.router).get("/api/offers").json()["data"]["waiting"][0]
    assert row["email"] is None


def test_a_chat_reopened_since_it_was_closed_is_the_agent_on_it_now(lottery_on):
    """Closed by another agent, reopened and taken by me: who closed it no longer names the owner, the fresh
    status says it is not closed (CodeRabbit #273)."""
    with _universe([_lead(closed_by_agent_id=OTHER)],
                   now={C1: {"status": "active", "mode": "human", "engaged_agent_id": ME}}):
        row = _client(offers.router).get("/api/offers").json()["data"]["waiting"][0]
    assert not row["hidden"] and row["agent_name"] == "Me Agent"


def test_a_closed_chat_is_still_its_closers(lottery_on):
    with _universe([_lead(closed_by_agent_id=OTHER)]):
        row = _client(offers.router).get("/api/offers").json()["data"]["waiting"][0]
    assert row["hidden"] and row["agent_name"] == "Other Agent"


def test_the_row_names_the_owner_the_rules_read_now_not_the_cached_one(lottery_on):
    """Cached as mine, moved to another agent since: hidden from me, and named as theirs (CodeRabbit #273)."""
    with _universe([_lead(assigned_agent_id=ME)], now={C1: {"assigned_agent_id": OTHER}}):
        row = _client(offers.router).get("/api/offers").json()["data"]["waiting"][0]
    assert row["hidden"] and row["agent_id"] == OTHER and row["agent_name"] == "Other Agent"


def test_an_engaged_agent_is_named_as_the_owner(lottery_on):
    with _universe([_lead(engaged_agent_id=OTHER)]):
        row = _client(offers.router).get("/api/offers").json()["data"]["waiting"][0]
    assert row["hidden"] and row["agent_name"] == "Other Agent"


def test_with_the_lottery_on_another_agents_lead_shows_route_and_owner_only(lottery_on):
    with _universe([_lead(assigned_agent_id=OTHER)]):
        data = _client(offers.router).get("/api/offers").json()["data"]
    row = data["waiting"][0]
    assert data["lottery"] is True
    assert row["hidden"] and row["email"] is None and row["route"] == "NYC → LON"
    assert row["agent_name"] == "Other Agent"


def test_with_the_lottery_on_a_lead_nobody_has_is_who_and_since_when(lottery_on):
    with _universe([_lead()]):
        row = _client(offers.router).get("/api/offers").json()["data"]["waiting"][0]
    assert row["blind"] and row["route"] is None and row["email"] is None


def test_with_the_lottery_off_the_list_is_todays():
    """Rule 5: nothing changes for anyone while FOLLOWUP_LOTTERY is off."""
    with _universe([_lead(assigned_agent_id=OTHER)]):
        data = _client(offers.router).get("/api/offers").json()["data"]
    row = data["waiting"][0]
    assert data["lottery"] is False and row["email"] == "dana@example.com" and not row["hidden"]


def test_a_manager_always_sees_the_whole_lead(lottery_on):
    with _universe([_lead(assigned_agent_id=OTHER)]):
        row = _client(offers.router, role="owner").get("/api/offers").json()["data"]["waiting"][0]
    assert row["email"] == "dana@example.com" and not row["hidden"]


def _conv(**over):
    return {"id": C1, "tunnel": "sales", "team_id": "team-1", "status": "closed", "mode": "ai", "metadata": {}, **over}


def test_with_the_lottery_on_an_agent_marks_only_their_own(lottery_on):
    saved = AsyncMock(return_value=True)
    with patch.multiple(db, get_conversation_simple=AsyncMock(return_value=_conv(assigned_agent_id=OTHER)),
                        get_blind_inputs=AsyncMock(return_value=_conv()), patch_conversation_presence=saved,
                        invalidate_leads_universe=MagicMock()):
        resp = _client(offers.router).post(f"/api/offers/{C1}", json={"state": "sent"})
    assert resp.status_code == 403 and "another agent" in resp.json()["detail"]
    saved.assert_not_awaited()


def test_marking_your_lottery_lead_confirms_it(lottery_on):
    pending = {"state": "pending", "agent_id": ME, "by": "lottery", "misses": []}
    confirm = AsyncMock(return_value=True)
    with patch.multiple(db, get_conversation_simple=AsyncMock(
            return_value=_conv(assigned_agent_id=ME, metadata={"followup": pending})),
            get_blind_inputs=AsyncMock(return_value=_conv()), patch_conversation_presence=AsyncMock(return_value=True),
            invalidate_leads_universe=MagicMock()), \
            patch("app.services.lead_lottery.confirm", new=confirm):
        resp = _client(offers.router).post(f"/api/offers/{C1}", json={"state": "sent"})
    assert resp.status_code == 200, resp.text
    confirm.assert_awaited_once()
    assert confirm.await_args.args[0] == C1 and confirm.await_args.args[3] == "marked"


def _detail(conv, uid=ME):
    confirm = AsyncMock(return_value=True)
    with patch.object(conv_api.db, "get_conversation", AsyncMock(return_value=conv)), \
            patch("app.services.lead_lottery.confirm", new=confirm):
        resp = _client(conv_api.router, uid=uid).get(f"/api/conversations/{C1}")
    assert resp.status_code == 200, resp.text
    return confirm


def test_opening_your_pending_lead_confirms_it():
    pending = {"state": "pending", "agent_id": ME}
    confirm = _detail({**_conv(assigned_agent_id=ME, metadata={"followup": pending}), "messages": []})
    confirm.assert_awaited_once()
    assert confirm.await_args.args[3] == "opened"


def test_opening_a_lead_given_to_you_by_hand_takes_it_off_your_badge():
    """lead_lottery.confirm takes a hand-given lead off its agent's badge (and confirms nothing)."""
    manual = {"state": "manual", "agent_id": ME, "by": "manual"}
    confirm = _detail({**_conv(assigned_agent_id=ME, metadata={"followup": manual}), "messages": []})
    confirm.assert_awaited_once()
    assert confirm.await_args.args[2] == manual and confirm.await_args.args[3] == "opened"


def test_marking_a_lead_given_to_you_by_hand_takes_it_off_your_badge(lottery_on):
    manual = {"state": "manual", "agent_id": ME, "by": "manual"}
    confirm = AsyncMock(return_value=False)
    with patch.multiple(db, get_conversation_simple=AsyncMock(
            return_value=_conv(assigned_agent_id=ME, metadata={"followup": manual})),
            get_blind_inputs=AsyncMock(return_value=_conv()), patch_conversation_presence=AsyncMock(return_value=True),
            invalidate_leads_universe=MagicMock()), \
            patch("app.services.lead_lottery.confirm", new=confirm):
        resp = _client(offers.router).post(f"/api/offers/{C1}", json={"state": "sent"})
    assert resp.status_code == 200, resp.text
    confirm.assert_awaited_once()
    assert confirm.await_args.args[2] == manual and confirm.await_args.args[3] == "marked"


@pytest.mark.parametrize("conv", [
    {"assigned_agent_id": OTHER, "metadata": {"followup": {"state": "pending", "agent_id": OTHER}}},
    {"assigned_agent_id": OTHER, "metadata": {"followup": {"state": "manual", "agent_id": OTHER}}},
    {"assigned_agent_id": ME, "metadata": {"followup": {"state": "acked", "agent_id": ME}}},
    {"assigned_agent_id": ME, "metadata": {}},
])
def test_any_other_open_confirms_nothing(conv):
    """Rule 5: the opens that do NOT confirm."""
    _detail({**_conv(**conv), "messages": []}).assert_not_awaited()


@pytest.mark.asyncio
async def test_the_heartbeat_carries_the_lottery_badge(monkeypatch):
    from app.api import agent as agent_api
    from config.settings import settings

    r = FakeRedis()
    monkeypatch.setattr(settings, "redis_url", "redis://fake")
    redis_client.set_client_for_tests(r)
    await r.zadd(f"followup:new:{ME}", {"c1": 1, "c2": 2})
    row = {"id": ME, "role": "sales", "is_active": True, "chat_enabled": True, "tunnel_scope": "sales"}
    with patch.object(agent_api.db, "update_user_last_seen", new=AsyncMock(return_value=row)), \
            patch.object(agent_api.db, "get_queue_for_operator", new=AsyncMock(return_value=[])), \
            patch("app.services.presence.record_presence_tick", new=AsyncMock()):
        out = await agent_api.heartbeat(agent_api.HeartbeatBody(), user={"id": ME})
    assert out["followups_new"] == 2


@pytest.mark.asyncio
async def test_no_badge_for_someone_who_is_not_an_operator():
    from app.api import agent as agent_api

    row = {"id": ME, "role": "admin", "is_active": True}
    with patch.object(agent_api.db, "update_user_last_seen", new=AsyncMock(return_value=row)), \
            patch("app.services.presence.record_presence_tick", new=AsyncMock()):
        out = await agent_api.heartbeat(agent_api.HeartbeatBody(), user={"id": ME})
    assert out["followups_new"] is None


@pytest.mark.parametrize("role", ["sales", "support", "qa", "project_manager"])
def test_only_managers_give_by_hand(role):
    resp = _client(followups.router, role=role).post(f"/api/followups/{C1}/assign", json={"agent_id": OTHER})
    assert resp.status_code == 403


def test_giving_by_hand_checks_the_ids_and_the_agent():
    c = _client(followups.router, role="admin")
    assert c.post("/api/followups/not-a-chat/assign", json={"agent_id": OTHER}).status_code == 400
    with patch.object(db, "get_operator_rows", new=AsyncMock(return_value=[])):
        assert c.post(f"/api/followups/{C1}/assign", json={"agent_id": OTHER}).status_code == 404
    with patch.object(db, "get_operator_rows", new=AsyncMock(return_value=None)):
        assert c.post(f"/api/followups/{C1}/assign", json={"agent_id": OTHER}).status_code == 502


def test_giving_by_hand_answers_in_the_admin_shape():
    agent = {"id": OTHER, "role": "sales", "is_active": True, "chat_enabled": True, "tunnel_scope": "sales"}
    given = AsyncMock(return_value={"state": "manual", "agent_id": OTHER, "by": "manual"})
    with patch.object(db, "get_operator_rows", new=AsyncMock(return_value=[agent])), \
            patch("app.services.lead_lottery.give_by_hand", new=given):
        resp = _client(followups.router, role="admin").post(f"/api/followups/{C1}/assign", json={"agent_id": OTHER})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True and body["count"] == 1 and body["data"]["state"] == "manual"


def test_an_open_chat_is_refused_with_the_reason():
    agent = {"id": OTHER, "role": "sales", "is_active": True, "chat_enabled": True, "tunnel_scope": "sales"}
    with patch.object(db, "get_operator_rows", new=AsyncMock(return_value=[agent])), \
            patch("app.services.lead_lottery.give_by_hand", new=AsyncMock(side_effect=ValueError("Only a closed chat"))):
        resp = _client(followups.router, role="admin").post(f"/api/followups/{C1}/assign", json={"agent_id": OTHER})
    assert resp.status_code == 400 and "closed" in resp.json()["detail"]


@pytest.mark.parametrize("agent_team, status", [("team-1", 200), ("team-2", 403), (None, 403)])
def test_a_supervisor_gives_only_to_an_agent_of_their_teams(agent_team, status):
    """The chat's team was checked; the agent's was not, so a team's lead could go to any team's agent."""
    agent = {"id": OTHER, "role": "sales", "is_active": True, "chat_enabled": True, "tunnel_scope": "sales",
             "team_id": agent_team}
    given = AsyncMock(return_value={"state": "manual", "agent_id": OTHER, "by": "manual"})
    with patch.object(db, "get_conversation_simple", new=AsyncMock(return_value={"id": C1, "team_id": "team-1"})), \
            patch.object(db, "get_team_ids_for_supervisor", new=AsyncMock(return_value=["team-1"])), \
            patch.object(db, "get_operator_rows", new=AsyncMock(return_value=[agent])), \
            patch("app.services.lead_lottery.give_by_hand", new=given):
        resp = _client(followups.router, role="supervisor").post(f"/api/followups/{C1}/assign",
                                                                  json={"agent_id": OTHER})
    assert resp.status_code == status
    assert given.await_count == (1 if status == 200 else 0)
