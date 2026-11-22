"""The follow-up lottery: who is a candidate, who wins, the confirmation, by hand, and what agents see."""

import random
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.infra import redis_client
from app.services import conversation_lock as cl
from app.services import lead_lottery as lot
from tests.fake_redis import FakeRedis, badge_add, compare_and_delete

# A fixed instant, 13:00 in New York: "today" for the counts never depends on when the suite runs (in the
# first New York hour, an hour ago was yesterday). The module's clock is set to it in `world`.
NOW = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)
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
    monkeypatch.setattr(lot, "datetime", _Clock)
    r = FakeRedis()
    r.register(cl.RELEASE_LUA, compare_and_delete)
    r.register(lot.BADGE_ADD_LUA, badge_add)
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
        "assign_to": patch("app.db.supabase.assign_closed_to", new=AsyncMock(return_value=True)),
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
async def test_a_redis_that_cannot_say_who_runs_skips_the_run(world):
    _, m, r = world
    r.down = True
    assert await lot.run_lead_lottery() == {"state": "held_elsewhere"}
    m["universe"].assert_not_awaited()
    assert cl.LOCK_HEALTH["job_skipped_unknown"] >= 1


@pytest.mark.asyncio
async def test_without_redis_configured_the_one_process_runs(world, monkeypatch):
    from config.settings import settings

    _, m, _ = world
    monkeypatch.setattr(settings, "redis_url", "")
    redis_client.set_client_for_tests(None)
    assert (await lot.run_lead_lottery())["state"] == "ok"


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
    m["release"].assert_awaited_once_with("c1", A, f["assigned_at"])
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
    m["conv"].return_value = {"id": "c1", "assigned_agent_id": A, "metadata": {"followup": pending}}
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
    m["assign_to"].assert_awaited_once_with("c1", B)
    assert await r.zscore(f"followup:new:{B}", "c1") is not None


@pytest.mark.asyncio
async def test_a_lead_given_by_hand_leaves_the_badge_when_its_agent_opens_it(world):
    """contracts/redis-keys.md: given by a manager, it leaves the badge once its agent opens it or marks its
    offer. Only a pending lead's confirmation took it off, so a hand-given lead stayed on the badge for good."""
    _, m, r = world
    m["conv"].return_value = {"id": "c1", "status": "closed", "assigned_agent_id": A, "metadata": {}}
    f = await lot.give_by_hand("c1", _agent(B), {"id": "mgr", "role": "admin"})
    assert await lot.new_count(B) == 1
    m["patch_meta"].reset_mock()
    assert not await lot.confirm("c1", {"id": A}, f, "opened"), "not theirs: nothing"
    assert await lot.new_count(B) == 1
    assert not await lot.confirm("c1", {"id": B}, f, "opened"), "theirs already: nothing to confirm"
    assert await lot.new_count(B) == 0
    m["patch_meta"].assert_not_awaited()
    assert all(c.args[2] != "followup_acked" for c in m["audit"].await_args_list)


@pytest.mark.asyncio
async def test_an_open_chat_is_not_given_here(world):
    _, m, _ = world
    m["conv"].return_value = {"id": "c1", "status": "active", "metadata": {}}
    with pytest.raises(ValueError):
        await lot.give_by_hand("c1", _agent(B), {"id": "mgr"})
    with pytest.raises(ValueError):
        m["conv"].return_value = {"id": "c1", "status": "closed", "metadata": {}}
        await lot.give_by_hand("c1", _agent(B, chat_enabled=False), {"id": "mgr"})
    m["assign_to"].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_chat_reopened_before_the_hand_give_is_not_given(world):
    """The write itself holds only while the chat is closed: a status read before it does not close the race."""
    _, m, r = world
    m["conv"].return_value = {"id": "c1", "status": "closed", "metadata": {}}
    m["assign_to"].return_value = False
    with pytest.raises(ValueError):
        await lot.give_by_hand("c1", _agent(B), {"id": "mgr"})
    m["patch_meta"].assert_not_awaited()
    assert await r.zscore(f"followup:new:{B}", "c1") is None


@pytest.mark.asyncio
async def test_a_confirmation_read_before_a_manager_moved_the_chat_does_nothing(world):
    _, m, _ = world
    pending = {"state": "pending", "agent_id": A, "by": "lottery", "misses": []}
    m["conv"].return_value = {"id": "c1", "assigned_agent_id": B,
                              "metadata": {"followup": {"state": "manual", "agent_id": B}}}
    assert not await lot.confirm("c1", {"id": A}, pending, "opened")
    m["patch_meta"].assert_not_awaited()


@pytest.mark.asyncio
async def test_an_assignment_whose_clock_was_not_saved_is_given_back(world):
    state, m, r = world
    m["patch_meta"].return_value = False
    out = await lot.run_lead_lottery()
    assert out["assigned"] == 0
    m["release"].assert_awaited_once()
    assert m["release"].await_args.args[:2] == ("c1", m["assign"].await_args.args[1])
    assert all(c.args[2] != "followup_assigned" for c in m["audit"].await_args_list)
    assert lot.LOTTERY_HEALTH["patch_failed"] == 1


@pytest.mark.asyncio
async def test_a_release_that_landed_without_its_miss_is_repaired(world):
    """An earlier run released the chat but could not write the miss: still "pending", nobody's. This run writes
    the miss without releasing anything, and the lead is a candidate again."""
    state, m, _ = world
    f = {"state": "pending", "agent_id": A, "by": "lottery", "assigned_at": (NOW - timedelta(minutes=16)).isoformat(),
         "ack_by": (NOW - timedelta(minutes=1)).isoformat(), "misses": []}
    state["rows"] = [_row(followup=f)]
    m["conv"].return_value = {"id": "c1", "assigned_agent_id": None, "metadata": {"followup": f}}
    out = await lot.run_lead_lottery()
    assert out["missed"] == 1 and lot.LOTTERY_HEALTH["repaired"] == 1
    m["release"].assert_not_awaited()
    written = m["patch_meta"].await_args_list[0].args[1]["followup"]
    assert written["state"] == "missed" and written["agent_id"] is None
    assert lot.is_candidate({**state["rows"][0], "followup": written})


@pytest.mark.asyncio
async def test_the_burst_counts_chats_within_a_run_as_across_runs(world, monkeypatch):
    """Burst 3. A client with two chats, then two clients with one: A, the least loaded, takes the first two
    clients (three chats) and is then full; the third client goes to B. Counted per client, A would have taken
    all three."""
    state, m, _ = world
    monkeypatch.setenv("FOLLOWUP_BURST", "3")
    busy_b = [_row(f"b{i}", followup={"by": "lottery", "agent_id": B, "assigned_at": (NOW - timedelta(hours=3)).isoformat()},
                   assigned_agent_id=B, visitor_email=f"b{i}@example.com") for i in range(5)]
    state["rows"] = [
        _row("c1", visitor_email="first@example.com", created_at=(NOW - timedelta(hours=3)).isoformat()),
        _row("c2", visitor_email="first@example.com", created_at=(NOW - timedelta(hours=3)).isoformat()),
        _row("c3", visitor_email="second@example.com", created_at=(NOW - timedelta(minutes=150)).isoformat()),
        _row("c4", visitor_email="third@example.com", created_at=(NOW - timedelta(minutes=140)).isoformat()),
        *busy_b,
    ]
    await lot.run_lead_lottery()
    given = {c.args[0]: c.args[1] for c in m["assign"].await_args_list}
    assert given == {"c1": A, "c2": A, "c3": A, "c4": B}


@pytest.mark.asyncio
async def test_no_redis_no_badge_number():
    assert await lot.new_count(A) is None
    assert await lot.newest(A) is None


@pytest.mark.asyncio
async def test_the_newest_lead_is_the_one_given_last(world):
    """What the panel's alert opens: the lead given last that the agent hasn't opened, and when."""
    _, _, r = world
    assert await lot.newest(A) is None
    await r.zadd(f"followup:new:{A}", {"c1": 100, "c2": 300, "c3": 200})
    assert await lot.newest(A) == ("c2", 300.0)
    await r.zrem(f"followup:new:{A}", "c2")
    assert await lot.newest(A) == ("c3", 200.0)
    assert await lot.newest(B) is None, "another agent's badge is theirs"


@pytest.mark.asyncio
async def test_two_leads_given_in_one_second_keep_their_order(world, monkeypatch):
    """CodeRabbit #273: whole seconds tied them, and Redis broke the tie by the greater id, not the later give."""
    first, second = "ffffffff-0000-4000-8000-000000000001", "aaaaaaaa-0000-4000-8000-000000000002"
    clock = iter([1_763_805_600.25, 1_763_805_600.75])
    monkeypatch.setattr(lot.time, "time", lambda: next(clock))
    await lot._badge(A, add=first)
    await lot._badge(A, add=second)
    assert await lot.newest(A) == (second, 1_763_805_600.75)


@pytest.mark.asyncio
async def test_a_hand_give_moves_the_badges_under_the_lock_an_open_takes_it_off_in(world, monkeypatch):
    """CodeRabbit #273: the give added the badge after the lock, so an open that read "manual" in between took
    nothing off, and the badge it then added stayed. Both sides now touch the badge under the same lock."""
    _, m, _ = world
    m["conv"].return_value = {"id": "c1", "status": "closed", "assigned_agent_id": A, "metadata": {}}
    events: list[str] = []
    real_lock, real_badge = lot.conversation_lock, lot._badge

    class _Seen:
        def __init__(self, inner):
            self.inner = inner

        async def __aenter__(self):
            events.append("lock")
            return await self.inner.__aenter__()

        async def __aexit__(self, *exc):
            events.append("unlock")
            return await self.inner.__aexit__(*exc)

    async def badge(agent, *, add=None, remove=None):
        events.append(f"badge {'+' if add else '-'}{agent}")
        await real_badge(agent, add=add, remove=remove)

    monkeypatch.setattr(lot, "conversation_lock", lambda *a, **k: _Seen(real_lock(*a, **k)))
    monkeypatch.setattr(lot, "_badge", badge)
    f = await lot.give_by_hand("c1", _agent(B), {"id": "mgr", "role": "admin"})
    assert events == ["lock", f"badge -{A}", f"badge +{B}", "unlock"]
    events.clear()
    assert not await lot.confirm("c1", {"id": B}, f, "opened")
    assert events == ["lock", f"badge -{B}", "unlock"]
    assert await lot.new_count(B) == 0


@pytest.mark.asyncio
async def test_a_later_lead_scores_after_the_newest_whatever_the_clock_says(world, monkeypatch):
    """CodeRabbit #273: the score was this process's time.time(); a clock a step back (or another process behind)
    gave a later lead a score at or below the newest, and the panel, which announces only a lead given after the
    last it saw, kept it quiet. Now a new lead is always scored after the badge's newest."""
    _, _, r = world
    clock = iter([1000.5, 999.0, 1000.5, 2000.25])
    monkeypatch.setattr(lot.time, "time", lambda: next(clock))
    for cid in ("c1", "c2", "c3", "c4"):
        await lot._badge(B, add=cid)
    scores = [await r.zscore(f"followup:new:{B}", c) for c in ("c1", "c2", "c3", "c4")]
    assert scores == [1000.5, 1000.501, 1000.502, 2000.25], scores
    assert (await lot.newest(B))[0] == "c4"
    # Rule 5: a clock that moves on gives the time itself, as before.
    assert scores[3] == 2000.25


@pytest.mark.asyncio
async def test_a_hand_give_after_a_confirmation_keeps_its_badge(world):
    """CodeRabbit #273: the confirmation took the lead off the badge after its lock and its audit; a manager's
    give in that gap put it back under the lock, and the late removal took it off again."""
    _, m, r = world
    pending = {"state": "pending", "agent_id": A, "by": "lottery", "misses": []}
    m["conv"].return_value = {"id": "c1", "status": "closed", "assigned_agent_id": A, "metadata": {"followup": pending}}
    await r.zadd(f"followup:new:{A}", {"c1": 1})

    async def audit(actor, email, action, *rest):
        if action == "followup_acked":
            # The manager gives the same chat to the same agent while the confirmation writes its audit.
            await lot.give_by_hand("c1", _agent(A), {"id": "mgr", "role": "admin"})

    m["audit"].side_effect = audit
    assert await lot.confirm("c1", {"id": A}, pending, "opened")
    assert await r.zscore(f"followup:new:{A}", "c1") is not None, "the give that came after keeps its badge"


@pytest.mark.asyncio
async def test_two_managers_giving_one_chat_move_the_badge_from_the_owner_under_the_lock(world):
    """CodeRabbit #273: the owner was read before the lock; two gives at once both read A, and the second, to C,
    took A off (already off) and left B's badge for a lead that was C's."""
    state, m, r = world
    state["agents"].append(_agent("cccccccc-0000-4000-8000-000000000003"))
    C = "cccccccc-0000-4000-8000-000000000003"
    owner = {"now": A, "second": False, "stale_given": False}

    def read(cid):
        # The second manager's first read happened before the first manager's give landed: it still says A.
        if owner["second"] and not owner["stale_given"]:
            owner["stale_given"] = True
            return {"id": cid, "status": "closed", "assigned_agent_id": A, "metadata": {}}
        return {"id": cid, "status": "closed", "assigned_agent_id": owner["now"], "metadata": {}}

    async def assigned(cid, agent_id):
        owner["now"] = agent_id
        return True

    m["conv"].side_effect = read
    m["assign_to"].side_effect = assigned
    await r.zadd(f"followup:new:{A}", {"c1": 1})
    await lot.give_by_hand("c1", _agent(B), {"id": "m1", "role": "admin"})
    owner["second"] = True
    await lot.give_by_hand("c1", _agent(C), {"id": "m2", "role": "admin"})
    assert await lot.new_count(A) == 0 and await lot.new_count(B) == 0, "B's badge goes with the lead"
    assert await lot.new_count(C) == 1
    # The second give names the owner it moved the lead from: B, read under the lock.
    assert m["audit"].await_args.args[5]["previous"] == B


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


# ── CodeRabbit #273 round 2: a give that cannot be undone, decoys, open tasks ──


@pytest.mark.asyncio
async def test_a_give_neither_clocked_nor_given_back_is_kept_and_undone_next_run(world):
    """The swap landed, the 15-minute clock was not saved, and giving the chat back failed too: nothing else
    would ever find it (assigned: no candidate; no pending clock: never taken back). It is kept in Redis, and
    the next run gives it back."""
    state, m, r = world
    m["patch_meta"].return_value = False
    m["release"].return_value = False
    out = await lot.run_lead_lottery()
    agent = m["assign"].await_args.args[1]
    assert out["assigned"] == 0 and lot.LOTTERY_HEALTH["release_failed"] == 1
    assert await r.smembers(lot.UNWIND_KEY) == {f"c1 {agent} {NOW.isoformat()}"}, "the give, named by its time"

    # The next run: the database answers again; the chat is still that agent's, with no clock.
    m["patch_meta"].return_value = True
    m["release"].reset_mock(return_value=True)
    m["release"].return_value = True
    m["conv"].return_value = {"id": "c1", "assigned_agent_id": agent, "metadata": {"followup": None}}
    state["rows"] = []
    await lot.run_lead_lottery()
    # Only while no follow-up names a time, as read: a clock saved in between keeps the chat with its agent.
    m["release"].assert_awaited_once_with("c1", agent, None, no_followup=True)
    assert lot.LOTTERY_HEALTH["unwound"] == 1 and await r.smembers(lot.UNWIND_KEY) == set()


def _kept_conv(state: str, at: datetime, owner: str = A) -> dict:
    return {"id": "c1", "assigned_agent_id": owner,
            "metadata": {"followup": {"state": state, "agent_id": A, "assigned_at": at.isoformat()}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("conv", [
    _kept_conv("pending", NOW),                          # the give's own clock was saved after all (a lost answer)
    _kept_conv("acked", NOW),                            # ... and the agent confirmed it
    _kept_conv("manual", NOW + timedelta(minutes=5)),    # a manager gave it to the same agent by hand since
    _kept_conv("pending", NOW + timedelta(hours=1)),     # a later run gave it again, with its clock
    {"id": "c1", "assigned_agent_id": B, "metadata": {}},
    {"id": "c1", "assigned_agent_id": None, "metadata": {}},
])
async def test_a_kept_give_with_nothing_left_to_undo_is_dropped_untouched(world, conv):
    """A follow-up saved at or after the failed give is newer than it, a manager moved the chat, or it is
    nobody's: nothing is given back (CodeRabbit #273: a confirmed or hand-given chat stays with its agent)."""
    state, m, r = world
    await r.sadd(lot.UNWIND_KEY, f"c1 {A} {NOW.isoformat()}")
    m["conv"].return_value = conv
    state["rows"] = []
    await lot.run_lead_lottery()
    m["release"].assert_not_awaited()
    assert await r.smembers(lot.UNWIND_KEY) == set() and lot.LOTTERY_HEALTH["unwound"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("conv, released", [(None, True), ({"id": "c1", "assigned_agent_id": A, "metadata": {}}, False)])
async def test_a_kept_give_stays_kept_while_it_cannot_be_read_or_undone(world, conv, released):
    state, m, r = world
    await r.sadd(lot.UNWIND_KEY, f"c1 {A} {NOW.isoformat()}")
    m["conv"].return_value = conv
    m["release"].return_value = released
    state["rows"] = []
    await lot.run_lead_lottery()
    assert await r.smembers(lot.UNWIND_KEY) == {f"c1 {A} {NOW.isoformat()}"}, "the next run tries again"


@pytest.mark.asyncio
async def test_a_follow_up_older_than_the_failed_give_is_its_leftover_and_the_give_is_undone(world):
    """The agent confirmed this client's chat two days ago; today's give to them failed before its clock. The
    old record is what the failed give left: the give is undone, and only while that record is still there."""
    state, m, r = world
    old = NOW - timedelta(days=2)
    await r.sadd(lot.UNWIND_KEY, f"c1 {A} {NOW.isoformat()}")
    m["conv"].return_value = _kept_conv("acked", old)
    state["rows"] = []
    await lot.run_lead_lottery()
    m["release"].assert_awaited_once_with("c1", A, old.isoformat(), no_followup=False)
    assert lot.LOTTERY_HEALTH["unwound"] == 1 and await r.smembers(lot.UNWIND_KEY) == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [f"c1 {A}", f"c1 {A} not-a-time", "c1"])
async def test_a_kept_entry_that_cannot_be_read_is_dropped_and_said(world, entry, caplog):
    state, m, r = world
    await r.sadd(lot.UNWIND_KEY, entry)
    state["rows"] = []
    with caplog.at_level("ERROR"):
        await lot.run_lead_lottery()
    m["release"].assert_not_awaited()
    assert await r.smembers(lot.UNWIND_KEY) == set()
    assert any("could not be read" in rec.getMessage() for rec in caplog.records)


def test_the_universe_reads_the_decoy_marker_with_decoys_switched_off(monkeypatch):
    """A decoy stored while DECOYS_ENABLED was on is still never given (nor sampled) after it is turned off."""
    import asyncio
    from unittest.mock import MagicMock

    from app.db import supabase as db

    selects = []

    class _Q:
        def select(self, cols, *a, **k):
            selects.append(cols)
            return self

        def order(self, *a, **k):
            return self

        def range(self, *a, **k):
            return self

        def execute(self):
            res = MagicMock()
            res.data = []
            return res

    client = MagicMock()
    client.table.return_value = _Q()
    monkeypatch.setenv("DECOYS_ENABLED", "false")
    with patch.object(db, "_queued_at_column_available", return_value=True):
        asyncio.run(db._universe_convs(client))
    assert selects and "is_decoy" in selects[0]


def test_open_tasks_are_read_after_the_last_id_so_a_task_done_between_pages_hides_nobody(monkeypatch):
    """Five open tasks, pages of two; the first task is done while the first page is read. At an offset the
    third task would fall into the seam and its chat would read as nobody's."""
    import asyncio
    from unittest.mock import MagicMock

    from app.db import supabase as db

    store = [{"id": i, "conversation_id": f"c{i}", "assignee_id": A} for i in range(1, 6)]

    class _Tasks:
        def __init__(self):
            self.after = self.lo = self.hi = self.n = None

        def select(self, *a, **k):
            return self

        def in_(self, *a, **k):
            return self

        def is_(self, *a, **k):
            return self

        @property
        def not_(self):
            return self

        def order(self, *a, **k):
            return self

        def gt(self, key, value):
            self.after = value
            return self

        def limit(self, n):
            self.n = n
            return self

        def range(self, lo, hi):
            self.lo, self.hi = lo, hi
            return self

        def execute(self):
            rows = sorted(store, key=lambda r: r["id"])
            if self.after is not None:
                rows = [r for r in rows if r["id"] > self.after]
            if self.lo is not None:
                rows = rows[self.lo:self.hi + 1]
            if self.n is not None:
                rows = rows[:self.n]
            res = MagicMock()
            res.data = [dict(r) for r in rows]
            if store and store[0]["id"] == 1:
                store.pop(0)  # task 1 is done right after the first page
            return res

    client = MagicMock()
    client.table.side_effect = lambda name: _Tasks()
    monkeypatch.setattr(db, "_PAGE", 2)
    with patch.object(db, "get_client", return_value=client):
        owners = asyncio.run(db.get_open_task_owners([f"c{i}" for i in range(1, 6)]))
    assert {"c2", "c3", "c4", "c5"} <= set(owners), owners
