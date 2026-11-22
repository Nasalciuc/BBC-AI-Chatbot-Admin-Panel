"""The morning QA sample: who is in it, why, once a day, the e-mail, the verdicts. Every name is invented."""

import random
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import qa_sample as qa

# 15:00 UTC on 21 Nov = 10:00 in New York: yesterday is 20 Nov.
NOW = datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc)
DAY = "2026-11-20"
A, B = "agent-a", "agent-b"


def _row(cid, **kw):
    base = {
        "conversation_id": cid, "id": f"lead-{cid}", "chat_number": 1000 + len(cid), "tunnel": "sales",
        "created_at": "2026-11-20T08:00:00+00:00", "assigned_agent_id": A, "visitor_email": f"{cid}@example.com",
        "visitor_phone": None, "created_in_crm": True, "crm": {"source": "push"}, "offer_sent_at": None,
        "closed_by_agent_id": None, "conversation_closed_at": None, "last_user_message_at": None, "is_decoy": False,
    }
    base.update(kw)
    return base


def test_the_day_is_yesterday_in_new_york_from_seven():
    assert qa.day_to_sample(datetime(2026, 11, 21, 11, 59, tzinfo=timezone.utc)) is None   # 06:59 ET
    assert qa.day_to_sample(datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)) == DAY      # 07:00 ET
    assert qa.day_to_sample(NOW) == DAY


def test_the_population_is_yesterdays_chats_with_an_agent_and_a_contact():
    rows = [
        _row("in"),
        _row("closer", assigned_agent_id=None, closed_by_agent_id=B),
        _row("no-agent", assigned_agent_id=None),
        _row("no-contact", visitor_email=None),
        _row("support", tunnel="support"),
        _row("decoy", is_decoy=True),
        _row("day-before", created_at="2026-11-19T16:00:00+00:00"),
        _row("in", created_at="2026-11-20T17:00:00+00:00"),   # the same chat twice: once
    ]
    assert [r["conversation_id"] for r in qa.population(rows, DAY)] == ["in", "closer"]


@pytest.mark.parametrize("change, reason", [
    ({"crm": {"source": "manual", "shape": "odd"}}, "odd_crm_id"),
    ({"created_in_crm": False}, "no_crm_after_24h"),
    ({"offer_sent_at": "2026-11-20T18:00:00+00:00", "created_in_crm": False}, "offer_without_crm"),
    ({"closed_by_agent_id": A, "last_user_message_at": "2026-11-20T18:00:00+00:00",
      "conversation_closed_at": "2026-11-20T18:00:40+00:00"}, "closed_fast"),
])
def test_what_looks_wrong_says_why(change, reason):
    assert reason in qa.suspicious_reasons(_row("c", **change), NOW, foreign=False, low_rate=False)


def test_a_chat_that_looks_fine_has_no_reason():
    """Rule 5: the chats that do NOT look wrong."""
    row = _row("c", closed_by_agent_id=A, last_user_message_at="2026-11-20T18:00:00+00:00",
               conversation_closed_at="2026-11-20T18:05:00+00:00")
    assert qa.suspicious_reasons(row, NOW, foreign=False, low_rate=False) == []
    young = _row("c", created_in_crm=False, created_at=(NOW - timedelta(hours=3)).isoformat())
    assert "no_crm_after_24h" not in qa.suspicious_reasons(young, NOW, foreign=False, low_rate=False)


def test_an_agent_far_under_the_teams_crm_rate_is_flagged():
    rows = []
    for i in range(6):
        rows.append(_row(f"a{i}", assigned_agent_id=A, created_at=(NOW - timedelta(days=1)).isoformat()))
        rows.append(_row(f"b{i}", assigned_agent_id=B, created_in_crm=(i == 0),
                         created_at=(NOW - timedelta(days=1)).isoformat()))
        rows.append(_row(f"c{i}", assigned_agent_id="agent-c", created_at=(NOW - timedelta(days=1)).isoformat()))
    assert qa.low_rate_agents(rows, NOW) == {B}


def test_every_suspicious_chat_and_fifteen_percent_of_the_rest():
    chats = [_row(f"ok{i}") for i in range(20)] + [_row("odd", crm={"shape": "odd"})]
    picked = qa.pick(chats, now=NOW, foreign_in={"ok3"}, low_rate=set(), new_agents=set(), percent=15,
                     rng=random.Random(1))
    by_id = {p["conversation_id"]: p["reasons"] for p in picked}
    assert by_id["odd"] == ["odd_crm_id"] and by_id["ok3"] == ["foreign_contact"]
    assert sum(1 for r in by_id.values() if r == ["random"]) == 3   # ceil(19 × 15%)
    assert len(picked) == 5


def test_a_new_agents_chats_are_sampled_half_the_time():
    chats = [_row(f"n{i}", assigned_agent_id=B) for i in range(200)]
    picked = qa.pick(chats, now=NOW, foreign_in=set(), low_rate=set(), new_agents={B}, percent=0,
                     rng=random.Random(7))
    share = sum(1 for p in picked if p["reasons"] == ["new_agent"]) / 200
    assert 0.4 < share < 0.6


@pytest.fixture
def run_world(monkeypatch):
    qa._done_days.clear()
    qa._emailed_days.clear()
    qa._email_tries.clear()
    rows = [_row("ok1"), _row("odd", crm={"shape": "odd"})]
    m = {
        "universe": patch("app.db.supabase.get_leads_universe", new=AsyncMock(return_value=rows)),
        "audit_rows": patch("app.db.supabase.get_audit_rows", new=AsyncMock(return_value=[])),
        "messages": patch("app.db.supabase.get_agent_messages", new=AsyncMock(return_value=[
            {"conversation_id": "ok1", "content": "Write to me at private.agent@gmail.com"}])),
        "operators": patch("app.db.supabase.get_operator_rows", new=AsyncMock(return_value=[
            {"id": A, "created_at": "2026-01-01T00:00:00+00:00"}])),
        "audit": patch("app.db.supabase.log_audit", new=AsyncMock()),
        "strict": patch("app.db.supabase.insert_audit_rows_strict", new=AsyncMock()),
        "emails": patch("app.db.supabase.get_active_emails_for_role", new=AsyncMock(return_value=["qa@example.com"])),
        "names": patch("app.db.supabase.get_user_names", new=AsyncMock(return_value={A: "Ana Agent"})),
        "send": patch("app.services.email.send_qa_sample_email", new=AsyncMock(return_value=True)),
    }
    started = {k: p.start() for k, p in m.items()}
    yield started
    for p in m.values():
        p.stop()
    qa._done_days.clear()
    qa._emailed_days.clear()
    qa._email_tries.clear()


@pytest.mark.asyncio
async def test_the_run_picks_writes_and_mails_once(run_world):
    out = await qa.run_qa_sample(NOW)
    assert out == {"state": "sampled", "day": DAY, "chats": 2, "picked": 2, "suspicious": 2}
    run_world["strict"].assert_awaited_once()
    records = run_world["strict"].await_args.args[0]
    picks = {r["target_id"]: r["details"]["reasons"] for r in records if r["action"] == "qa_sample_picked"}
    assert picks == {"ok1": ["foreign_contact"], "odd": ["odd_crm_id"]}
    assert records[-1]["action"] == "qa_sample_done", "the picks and the day's done in one insert"
    assert run_world["audit"].await_args.args[2] == "qa_sample_emailed"
    to, day, lines = run_world["send"].await_args.args
    assert to == ["qa@example.com"] and day == DAY
    assert all("example.com" not in str(line) for line in lines), "no client contact in the e-mail"
    assert {line["agent"] for line in lines} == {"Ana Agent"}
    assert await qa.run_qa_sample(NOW) == {"state": "done", "day": DAY}
    assert run_world["universe"].await_count == 1


@pytest.mark.asyncio
async def test_a_day_already_sampled_in_another_process_is_not_sampled_again(run_world):
    run_world["audit_rows"].return_value = [{"action": "qa_sample_done", "details": {"day": DAY}},
                                            {"action": "qa_sample_emailed", "details": {"day": DAY}}]
    assert await qa.run_qa_sample(NOW) == {"state": "done", "day": DAY}
    run_world["strict"].assert_not_awaited()
    run_world["send"].assert_not_awaited()


@pytest.mark.asyncio
async def test_half_a_day_is_never_marked_sampled(run_world):
    """The picks and the done go in one insert: a failed save leaves the day whole for the next tick."""
    run_world["strict"].side_effect = RuntimeError("insert failed")
    assert (await qa.run_qa_sample(NOW))["state"] == "save_failed"
    run_world["send"].assert_not_awaited()
    run_world["strict"].side_effect = None
    assert (await qa.run_qa_sample(NOW))["state"] == "sampled"


@pytest.mark.asyncio
async def test_a_failed_email_is_sent_again_from_the_saved_sample(run_world):
    run_world["send"].return_value = False
    assert (await qa.run_qa_sample(NOW))["state"] == "sampled"
    assert all(c.args[2] != "qa_sample_emailed" for c in run_world["audit"].await_args_list)
    saved = [{"action": "qa_sample_done", "details": {"day": DAY}},
             {"action": "qa_sample_picked", "target_id": "ok1", "details": {"day": DAY, "reasons": ["random"],
                                                                           "agent_id": A, "chat_number": 101}}]
    run_world["audit_rows"].return_value = saved
    run_world["send"].return_value = True
    out = await qa.run_qa_sample(NOW)
    assert out == {"state": "done", "day": DAY, "emailed": True}
    assert run_world["universe"].await_count == 1, "not sampled again: e-mailed from what was saved"
    assert run_world["audit"].await_args.args[2] == "qa_sample_emailed"


@pytest.mark.asyncio
async def test_a_team_that_cannot_be_read_samples_nothing_yet(run_world):
    run_world["operators"].return_value = None
    assert (await qa.run_qa_sample(NOW))["state"] == "unreadable"
    run_world["strict"].assert_not_awaited()


@pytest.mark.asyncio
async def test_the_clients_number_to_check_is_their_own(run_world, monkeypatch):
    rows = [_row("ok1", phone_to_check="0722 000 111")]
    run_world["universe"].return_value = rows
    run_world["messages"].return_value = [{"conversation_id": "ok1", "content": "I will call you on 0722 000 111"}]
    monkeypatch.setenv("QA_SAMPLE_PERCENT", "0")
    out = await qa.run_qa_sample(NOW)
    assert out["picked"] == 0, "repeating the client's own number is no foreign contact"


@pytest.mark.asyncio
async def test_before_seven_and_when_off_nothing_runs(run_world, monkeypatch):
    assert await qa.run_qa_sample(datetime(2026, 11, 21, 11, 0, tzinfo=timezone.utc)) == {"state": "not_yet"}
    monkeypatch.setenv("QA_SAMPLE", "false")
    assert await qa.run_qa_sample(NOW) == {"state": "off"}
    run_world["universe"].assert_not_awaited()


@pytest.mark.asyncio
async def test_nobody_with_the_qa_role_is_counted_not_mailed(run_world):
    run_world["emails"].return_value = []
    before = qa.QA_HEALTH["no_recipients"]
    await qa.run_qa_sample(NOW)
    run_world["send"].assert_not_awaited()
    assert qa.QA_HEALTH["no_recipients"] == before + 1


@pytest.mark.asyncio
async def test_the_page_reads_the_picks_and_the_latest_verdict():
    rows = [
        {"action": "qa_sample_picked", "target_id": "c1", "details": {"day": DAY, "reasons": ["random"],
                                                                     "agent_id": A, "chat_number": 101}},
        {"action": "qa_sample_picked", "target_id": "c2", "details": {"day": DAY, "reasons": ["odd_crm_id"],
                                                                     "agent_id": B, "chat_number": 102}},
        {"action": "qa_verdict", "target_id": "c1", "user_id": "qa1", "created_at": "t1",
         "details": {"day": DAY, "verdict": "problem", "note": "first"}},
        {"action": "qa_verdict", "target_id": "c1", "user_id": "qa1", "created_at": "t2",
         "details": {"day": DAY, "verdict": "ok", "note": "on second look"}},
        {"action": "qa_sample_picked", "target_id": "c3", "details": {"day": "2026-11-19", "reasons": ["random"]}},
    ]
    with patch("app.db.supabase.get_audit_rows", new=AsyncMock(return_value=rows)), \
            patch("app.db.supabase.get_user_names", new=AsyncMock(return_value={A: "Ana", B: "Bob", "qa1": "Quinn"})):
        out = await qa.sample_of(DAY)
    items = {i["conversation_id"]: i for i in out["items"]}
    assert set(items) == {"c1", "c2"}
    assert items["c1"]["verdict"] == "ok" and items["c1"]["note"] == "on second look" and items["c1"]["verdict_by"] == "Quinn"
    assert items["c2"]["reasons"][0]["code"] == "odd_crm_id" and items["c2"]["agent_name"] == "Bob"
    assert out["counts"] == {"ok": 1, "problem": 0, "discuss": 0, "open": 1}


def _client(role):
    from app.api import qa as qa_api
    from app.security.auth import get_current_user

    app = FastAPI()
    app.include_router(qa_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "role": role, "email": "u1@example.com"}
    return TestClient(app)


@pytest.mark.parametrize("role", ["sales", "support", "supervisor", "project_manager"])
def test_agents_never_reach_the_sample(role):
    assert _client(role).get("/api/qa/sample").status_code == 403
    assert _client(role).get("/api/qa/stats").status_code == 403


def test_a_verdict_only_on_a_chat_of_that_days_sample():
    sample = {"day": DAY, "items": [{"conversation_id": "aaaaaaaa-0000-4000-8000-000000000001"}], "counts": {}}
    audit = AsyncMock()
    with patch("app.services.qa_sample.sample_of", new=AsyncMock(return_value=sample)), \
            patch("app.db.supabase.insert_audit_rows_strict", new=audit):
        c = _client("qa")
        ok = c.patch("/api/qa/sample/aaaaaaaa-0000-4000-8000-000000000001", json={"day": DAY, "verdict": "problem",
                                                                                "note": "price quoted early"})
        assert ok.status_code == 200 and ok.json()["success"] is True
        no = c.patch("/api/qa/sample/aaaaaaaa-0000-4000-8000-000000000002", json={"day": DAY, "verdict": "ok"})
        assert no.status_code == 400
        assert c.patch("/api/qa/sample/aaaaaaaa-0000-4000-8000-000000000001",
                       json={"day": DAY, "verdict": "maybe"}).status_code == 422
    assert audit.await_count == 1 and audit.await_args.args[0][0]["action"] == "qa_verdict"


def test_a_verdict_that_is_not_saved_is_not_a_success():
    sample = {"day": DAY, "items": [{"conversation_id": "aaaaaaaa-0000-4000-8000-000000000001"}], "counts": {}}
    with patch("app.services.qa_sample.sample_of", new=AsyncMock(return_value=sample)), \
            patch("app.db.supabase.insert_audit_rows_strict", new=AsyncMock(side_effect=RuntimeError("down"))):
        r = _client("qa").patch("/api/qa/sample/aaaaaaaa-0000-4000-8000-000000000001",
                                json={"day": DAY, "verdict": "ok"})
    assert r.status_code == 503


def test_a_sample_that_cannot_be_read_is_a_502():
    with patch("app.services.qa_sample.sample_of", new=AsyncMock(side_effect=RuntimeError("down"))):
        assert _client("qa").get(f"/api/qa/sample?day={DAY}").status_code == 502



@pytest.mark.asyncio
async def test_the_stats_count_the_sampled_days_asked_for_not_one_more():
    """days=1 at NOW is DAY alone: the day before was written yesterday morning, inside the read window, but is
    another sampled day."""
    rows = [
        {"action": "qa_sample_picked", "target_id": "c1", "details": {"day": DAY, "agent_id": A}},
        {"action": "qa_sample_picked", "target_id": "c0", "details": {"day": "2026-11-19", "agent_id": A}},
        {"action": "qa_verdict", "target_id": "c1", "details": {"day": DAY, "verdict": "problem"}},
        {"action": "qa_verdict", "target_id": "c0", "details": {"day": "2026-11-19", "verdict": "ok"}},
    ]
    with patch("app.db.supabase.get_audit_rows", new=AsyncMock(return_value=rows)), \
            patch("app.db.supabase.get_user_names", new=AsyncMock(return_value={A: "Ana"})):
        one = await qa.stats(1, NOW)
        two = await qa.stats(2, NOW)
    assert one == [{"agent_id": A, "agent_name": "Ana", "picked": 1, "ok": 0, "problem": 1, "discuss": 0, "open": 0}]
    assert two[0]["picked"] == 2 and two[0]["ok"] == 1
