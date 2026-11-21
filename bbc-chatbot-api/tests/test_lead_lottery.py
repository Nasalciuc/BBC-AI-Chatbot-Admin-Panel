"""The follow-up lottery: who is a candidate, who wins, the confirmation, by hand, and what agents see."""

import random
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.infra import redis_client
from app.services import conversation_lock as cl
from app.services import lead_lottery as lot
from tests.fake_redis import FakeRedis, compare_and_delete

NOW = datetime.now(timezone.utc)
A, B, C = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", \
    "33333333-3333-4333-8333-333333333333"


def _row(cid="c1", **kw):
    base = {
        "conversation_id": cid, "id": f"lead-{cid}", "conversation_status": "closed", "conversation_mode": "ai",
        "tunnel": "sales", "assigned_agent_id": None, "closed_by_agent_id": None, "last_agent_message_at": None,
        "engaged_agent_id": None, "visitor_email": f"{cid}@example.com", "visitor_phone": None,
        "visitor_name": "Ana Example", "origin_code": "JFK", "destination_code": "LHR",
        "created_at": (NOW - timedelta(hours=2)).isoformat(),
        "conversation_closed_at": (NOW - timedelta(hours=1)).isoformat(),
        "followup": None, "is_decoy": False, "crm_push_gate_reason": None,
        "offer_sent_at": None, "offer_skipped_at": None,
    }
    base.update(kw)
    return base


def _agent(uid, **kw):
    return {"id": uid, "name": uid[:4], "role": "sales", "is_active": True, "chat_enabled": True,
            "tunnel_scope": "sales", **kw}


# ── who is a candidate ──────────────────────────────────────────


def test_a_closed_lead_nobody_has_is_a_candidate():
    assert lot.is_candidate(_row())
    assert lot.is_candidate(_row(conversation_status="completed"))
    assert lot.is_candidate(_row(followup={"state": "missed", "misses": [[A, NOW.isoformat()]]}))


@pytest.mark.parametrize("change", [
    {"conversation_status": "active"},          # the shared line's, not the lottery's (D1)
    {"conversation_status": "needs_agent"},
    {"assigned_agent_id": A},
    {"closed_by_agent_id": A},
    {"last_agent_message_at": NOW.isoformat()},
    {"destination_code": "AAA"},
    {"origin_code": ""},
    {"is_decoy": True},
    {"tunnel": "support"},
    {"offer_sent_at": NOW.isoformat()},
    {"followup": {"state": "pending"}},
    {"followup": {"state": "acked"}},
    {"followup": {"state": "manual"}},
])
def test_everything_else_is_not(change):
    """Rule 5: the leads that do NOT go into the lottery."""
    assert not lot.is_candidate(_row(**change))


# ── who wins ────────────────────────────────────────────────────


def test_the_least_loaded_today_wins():
    agent, how = lot.pick([A, B, C], sticky=None, today={A: 2, B: 0, C: 1}, recent={}, missed=set(),
                          burst=3, daily_cap=0)
    assert (agent, how) == (B, "lottery")


def test_a_tie_is_broken_by_chance():
    winners = {lot.pick([A, B], sticky=None, today={}, recent={}, missed=set(), burst=3, daily_cap=0,
                        rng=random.Random(seed))[0] for seed in range(40)}
    assert winners == {A, B}


def test_the_returning_clients_agent_comes_first_if_present():
    agent, how = lot.pick([A, B], sticky=B, today={B: 5}, recent={}, missed=set(), burst=3, daily_cap=0)
    assert (agent, how) == (B, "sticky")
    agent, how = lot.pick([A], sticky=B, today={}, recent={}, missed=set(), burst=3, daily_cap=0)
    assert (agent, how) == (A, "lottery"), "the returning client's agent is not at the desk"


def test_at_most_three_in_ten_minutes_and_the_daily_cap():
    assert lot.pick([A, B], sticky=None, today={}, recent={A: 3}, missed=set(), burst=3, daily_cap=0)[0] == B
    assert lot.pick([A], sticky=None, today={}, recent={A: 3}, missed=set(), burst=3, daily_cap=0) == \
        (None, "all_busy")
    assert lot.pick([A], sticky=None, today={A: 4}, recent={}, missed=set(), burst=3, daily_cap=4) == \
        (None, "all_busy")


def test_who_let_it_go_is_not_given_it_again_and_nobody_present_says_so():
    assert lot.pick([A, B], sticky=None, today={}, recent={}, missed={A}, burst=3, daily_cap=0)[0] == B
    assert lot.pick([], sticky=None, today={}, recent={}, missed=set(), burst=3, daily_cap=0) == \
        (None, "no_one_present")


def test_counts_read_from_the_followups():
    rows = [
        _row("c1", followup={"by": "lottery", "agent_id": A, "assigned_at": (NOW - timedelta(minutes=5)).isoformat()}),
        _row("c2", followup={"by": "sticky", "agent_id": A, "assigned_at": (NOW - timedelta(hours=3)).isoformat()}),
        _row("c3", followup={"by": "manual", "agent_id": B, "assigned_at": NOW.isoformat()}),
    ]
    today, recent = lot.given_counts(rows, NOW, 10)
    assert recent == {A: 1}
    assert today.get(A, 0) in (1, 2) and B not in today  # c2 is today unless the New York day just turned


def test_the_sticky_agent_is_the_newest_within_90_days():
    rows = [
        _row("old", assigned_agent_id=A, visitor_email="x@example.com",
             conversation_closed_at=(NOW - timedelta(days=100)).isoformat()),
        _row("mid", closed_by_agent_id=B, visitor_email="x@example.com",
             conversation_closed_at=(NOW - timedelta(days=10)).isoformat()),
        _row("new", engaged_agent_id=C, visitor_email="X@example.com",
             conversation_closed_at=(NOW - timedelta(days=2)).isoformat()),
    ]
    assert lot.sticky_agents(rows, NOW)["e:x@example.com"][1] == C


def test_a_miss_bars_the_agent_for_two_hours_only():
    group = [_row(followup={"misses": [[A, (NOW - timedelta(minutes=30)).isoformat()],
                                       [B, (NOW - timedelta(hours=3)).isoformat()]]})]
    assert lot.recent_missers(group, NOW) == {A}


# ── the run ─────────────────────────────────────────────────────


@pytest.fixture
def world(monkeypatch):
    """The lottery on, Redis faked, the database mocked: one place for each test to change."""
    from config.settings import settings

    monkeypatch.setenv("FOLLOWUP_LOTTERY", "true")
    r = FakeRedis()
    r.register(cl.RELEASE_LUA, compare_and_delete)
    monkeypatch.setattr(settings, "redis_url", "redis://fake")
    redis_client.set_client_for_tests(r)
    for k in lot.LOTTERY_HEALTH:
        lot.LOTTERY_HEALTH[k] = 0
    state = {"rows": [_row()], "agents": [_agent(A), _agent(B)], "owners": {}, "present": {A, B}}
    patches = {
        "universe": patch("app.db.supabase.get_leads_universe", new=AsyncMock(side_effect=lambda: state["rows"])),
        "owners": patch("app.db.supabase.get_open_task_owners", new=AsyncMock(side_effect=lambda ids: state["owners"])),
        "operators": patch("app.db.supabase.get_operator_rows",
                           new=AsyncMock(side_effect=lambda ids=None, **k: [a for a in state["agents"]
                                                                              if ids is None or a["id"] in ids])),
        "present": patch("app.services.agent_presence.present_operator_ids",
                         new=AsyncMock(side_effect=lambda: state["present"])),
        "assign": patch("app.db.supabase.assign_closed_if_unowned", new=AsyncMock(return_value=True)),
        "release": patch("app.db.supabase.release_closed_if_agent", new=AsyncMock(return_value=True)),
        "patch_meta": patch("app.db.supabase.patch_conversation_presence", new=AsyncMock(return_value=True)),
        "audit": patch("app.db.supabase.log_audit", new=AsyncMock()),
        "conv": patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=None)),
        "invalidate": patch("app.db.supabase.invalidate_leads_universe"),
        "update": patch("app.db.supabase.update_conversation", new=AsyncMock(return_value={"id": "c1"})),
    }
    mocks = {k: p.start() for k, p in patches.items()}
    yield state, mocks, r
    for p in patches.values():
        p.stop()


@pytest.mark.asyncio
async def test_off_by_default(monkeypatch):
    monkeypatch.delenv("FOLLOWUP_LOTTERY", raising=False)
    assert await lot.run_lead_lottery() == {"state": "off"}


@pytest.mark.asyncio
async def test_a_lead_goes_to_the_least_loaded_present_agent(world):
    state, m, r = world
    state["rows"] = [_row(), _row("prev", followup={"by": "lottery", "agent_id": A,
                                                     "assigned_at": (NOW - timedelta(hours=1)).isoformat()},
                                assigned_agent_id=A, visitor_email="other@example.com")]
    out = await lot.run_lead_lottery()
    assert out["state"] == "ok" and out["assigned"] == 1
    m["assign"].assert_awaited_once_with("c1", B)
    written = m["patch_meta"].await_args.args[1]["followup"]
    assert written["state"] == "pending" and written["agent_id"] == B and written["by"] == "lottery"
    due = datetime.fromisoformat(written["ack_by"])
    assert timedelta(minutes=14) < due - datetime.fromisoformat(written["assigned_at"]) <= timedelta(minutes=15)
    assert m["audit"].await_args.args[2] == "followup_assigned"
    assert await r.zscore(f"followup:new:{B}", "c1") is not None
    m["invalidate"].assert_called()


@pytest.mark.asyncio
async def test_one_client_one_agent(world):
    state, m, _ = world
    state["rows"] = [_row("c1", visitor_email="same@example.com"), _row("c2", visitor_email="same@example.com")]
    await lot.run_lead_lottery()
    agents = {c.args[1] for c in m["assign"].await_args_list}
    assert [c.args[0] for c in m["assign"].await_args_list] == ["c1", "c2"] or \
        sorted(c.args[0] for c in m["assign"].await_args_list) == ["c1", "c2"]
    assert len(agents) == 1


@pytest.mark.asyncio
async def test_a_returning_client_goes_back_to_their_agent(world):
    state, m, _ = world
    state["rows"] = [_row("c1", visitor_email="back@example.com"),
                     _row("old", visitor_email="back@example.com", closed_by_agent_id=B,
                          conversation_status="closed", offer_sent_at=NOW.isoformat())]
    out = await lot.run_lead_lottery()
    m["assign"].assert_awaited_once_with("c1", B)
    assert out["sticky"] == 1


@pytest.mark.asyncio
async def test_nobody_at_the_desk_means_the_lead_waits(world):
    state, m, _ = world
    state["present"] = set()
    out = await lot.run_lead_lottery()
    assert out["waiting"] == 1 and out["assigned"] == 0
    m["assign"].assert_not_awaited()
    assert lot.LOTTERY_HEALTH["waiting_now"] == 1 and lot.LOTTERY_HEALTH["no_one_present"] == 1


@pytest.mark.asyncio
async def test_what_cannot_be_read_gives_nothing_out(world):
    state, m, _ = world
    m["owners"].side_effect = lambda ids: None
    out = await lot.run_lead_lottery()
    assert out["state"] == "unreadable"
    m["assign"].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_chat_with_an_open_task_stays_with_the_tasks_owner(world):
    state, m, _ = world
    state["owners"] = {"c1": C}
    await lot.run_lead_lottery()
    m["assign"].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lost_race_writes_nothing_more(world):
    state, m, r = world
    m["assign"].return_value = False
    out = await lot.run_lead_lottery()
    assert out["assigned"] == 0 and lot.LOTTERY_HEALTH["lost_race"] == 1
    m["patch_meta"].assert_not_awaited()
    assert await r.keys("followup:new:*") == []


@pytest.mark.asyncio
async def test_a_run_held_by_another_process_is_skipped(world):
    _, m, r = world
    await r.set("lock:job:lead_lottery", "theirs", px=50_000)
    assert await lot.run_lead_lottery() == {"state": "held_elsewhere"}
    m["universe"].assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unconfirmed_lead_goes_back_and_the_miss_is_counted(world):
    state, m, r = world
    due = (NOW - timedelta(minutes=1)).isoformat()
    f = {"state": "pending", "agent_id": A, "by": "lottery", "assigned_at": (NOW - timedelta(minutes=16)).isoformat(),
         "ack_by": due, "misses": []}
    state["rows"] = [_row(assigned_agent_id=A, followup=f)]
    m["conv"].return_value = {"id": "c1", "assigned_agent_id": A, "metadata": {"followup": f}}
    await r.zadd(f"followup:new:{A}", {"c1": 1})
    out = await lot.run_lead_lottery()
    assert out["missed"] == 1
    m["release"].assert_awaited_once_with("c1", A)
    written = m["patch_meta"].await_args.args[1]["followup"]
    assert written["state"] == "missed" and written["agent_id"] is None and written["misses"][0][0] == A
    assert m["audit"].await_args.args[2] == "followup_missed"
    assert await r.zscore(f"followup:new:{A}", "c1") is None


@pytest.mark.asyncio
async def test_a_lead_confirmed_meanwhile_is_not_taken_back(world):
    state, m, _ = world
    f = {"state": "pending", "agent_id": A, "ack_by": (NOW - timedelta(minutes=1)).isoformat(), "misses": []}
    state["rows"] = [_row(assigned_agent_id=A, followup=f)]
    m["conv"].return_value = {"id": "c1", "assigned_agent_id": A, "metadata": {"followup": {**f, "state": "acked"}}}
    out = await lot.run_lead_lottery()
    assert out["missed"] == 0
    m["release"].assert_not_awaited()


# ── confirmation, by hand, the badge ────────────────────────────


@pytest.mark.asyncio
async def test_opening_a_lead_confirms_it_for_its_agent_only(world):
    _, m, r = world
    await r.zadd(f"followup:new:{A}", {"c1": 1})
    pending = {"state": "pending", "agent_id": A, "by": "lottery", "misses": []}
    assert not await lot.confirm("c1", {"id": B}, pending, "opened")
    assert not await lot.confirm("c1", {"id": A}, {**pending, "state": "acked"}, "opened")
    m["patch_meta"].assert_not_awaited()
    assert await lot.confirm("c1", {"id": A}, pending, "opened")
    written = m["patch_meta"].await_args.args[1]["followup"]
    assert written["state"] == "acked" and written["acked_how"] == "opened"
    assert await r.zscore(f"followup:new:{A}", "c1") is None
    assert await lot.new_count(A) == 0


@pytest.mark.asyncio
async def test_a_manager_gives_a_closed_lead_without_a_timer(world):
    _, m, r = world
    m["conv"].return_value = {"id": "c1", "status": "closed", "assigned_agent_id": A, "metadata": {}}
    f = await lot.give_by_hand("c1", _agent(B), {"id": "mgr", "role": "admin"})
    assert f["state"] == "manual" and f["agent_id"] == B and "ack_by" not in f
    m["update"].assert_awaited_once_with("c1", {"assigned_agent_id": B})
    assert await r.zscore(f"followup:new:{B}", "c1") is not None


@pytest.mark.asyncio
async def test_an_open_chat_is_not_given_here(world):
    _, m, _ = world
    m["conv"].return_value = {"id": "c1", "status": "active", "metadata": {}}
    with pytest.raises(ValueError):
        await lot.give_by_hand("c1", _agent(B), {"id": "mgr"})
    with pytest.raises(ValueError):
        m["conv"].return_value = {"id": "c1", "status": "closed", "metadata": {}}
        await lot.give_by_hand("c1", _agent(B, chat_enabled=False), {"id": "mgr"})
    m["update"].assert_not_awaited()


@pytest.mark.asyncio
async def test_no_redis_no_badge_number():
    assert await lot.new_count(A) is None


# ── what agents see in Offers to send, with the lottery on ──────


def _offers(rows, me, **kw):
    from app.services.offers_view import build_offers, owner_of

    return build_offers(
        rows, now=NOW, names={A: "Ana", B: "Bob"},
        blind=lambda r: owner_of(r) is None,
        hidden=lambda r: owner_of(r) is not None and owner_of(r) != me, **kw,
    )


def test_an_agent_sees_their_own_whole_another_agents_by_route_and_owner():
    rows = [
        _row("mine", assigned_agent_id=A, visitor_email="mine@example.com",
             followup={"by": "lottery", "agent_id": A, "assigned_at": NOW.isoformat(), "state": "acked"}),
        _row("theirs", assigned_agent_id=B, visitor_email="theirs@example.com"),
        _row("nobodys", visitor_email="free@example.com"),
    ]
    waiting = {r["conversation_id"]: r for r in _offers(rows, A)["waiting"]}
    assert waiting["mine"]["email"] == "mine@example.com" and not waiting["mine"]["hidden"]
    assert waiting["mine"]["followup"] == {"by": "lottery", "state": "acked", "at": NOW.isoformat(), "agent_name": "Ana"}
    theirs = waiting["theirs"]
    assert theirs["hidden"] and theirs["email"] is None and theirs["phone"] is None
    assert theirs["route"] == "JFK → LHR" and theirs["agent_name"] == "Bob"
    assert theirs["client"] != "Ana Example"
    assert waiting["nobodys"]["blind"] and waiting["nobodys"]["route"] is None


def test_without_the_callbacks_nothing_changes():
    """Rule 5: the lottery off is today's list."""
    from app.services.offers_view import build_offers

    rows = [_row("theirs", assigned_agent_id=B, visitor_email="theirs@example.com")]
    row = build_offers(rows, now=NOW)["waiting"][0]
    assert row["email"] == "theirs@example.com" and not row["hidden"] and not row["blind"]
