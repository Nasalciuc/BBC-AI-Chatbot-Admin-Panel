"""Leads — what reached the CRM and what didn't.

What is pinned here:
  * the level is what the chat collected and what the client confirmed
    (confirmed_at), not the score;
  * "Not in the CRM" is every lead with a phone the CRM doesn't have, with
    the reason: refused, still open, closed by an agent, ready never sent;
  * tabs count the whole tab, filters narrow the rows, the counts agree;
  * a supervisor sees only their team, masked; a failed read is a 502;
  * the classic list (no `view`) is unchanged;
  * every panel write drops the shared read.
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
from app.services.leads_view import (  # noqa: E402
    build_leads_view,
    chat_with,
    crm_state,
    lead_level,
    matches,
)

NAMES = {"ana": "Ana Ceban", "victor": "Victor Rusu"}


def row(lid: str, **over) -> dict:
    base = {
        "id": lid, "conversation_id": f"c-{lid}", "chat_number": 4800, "created_at": "2026-11-06T10:00:00+00:00",
        "origin_code": "JFK", "destination_code": "LHR", "route_display": "New York → London",
        "departure_date": "2026-11-16", "return_date": None, "passengers": 2, "score": 60, "tier": "silver",
        "created_in_crm": False, "crm_lead_id": None, "crm_push_gate_reason": None, "reviewed_by_qa": False,
        "visitor_name": "Michael Turner", "visitor_email": "m@example.com", "visitor_phone": "+1 555 0110",
        "conversation_status": "closed", "assigned_agent_id": None, "last_agent_message_at": None,
        "closed_by_agent_id": None, "confirmed_at": None, "team_id": "team-1",
    }
    base.update(over)
    return base


def test_level_is_what_was_collected_and_confirmed():
    assert lead_level(row("a", origin_code=None, destination_code=None)) == "contact"
    assert lead_level(row("a", confirmed_at="2026-11-06T10:05:00+00:00")) == "ready"
    assert lead_level(row("a")) == "almost"
    # A gold score without the client's YES is not "Ready to quote".
    assert lead_level(row("a", tier="gold", score=80)) == "almost"
    assert lead_level(row("a", departure_date=None)) == "early"


def test_chat_with_and_crm_state():
    assert chat_with(row("a")) == "ai"
    assert chat_with(row("a", last_agent_message_at="2026-11-06T10:03:00+00:00")) == "person"
    assert chat_with(row("a", closed_by_agent_id="ana")) == "person"
    assert crm_state(row("a", created_in_crm=True)) == "in_crm"
    assert crm_state(row("a", visitor_phone=" ")) == "no_phone"
    assert crm_state(row("a", crm_push_gate_reason="same_route")) == "refused"
    assert crm_state(row("a", conversation_status="active")) == "open"
    assert crm_state(row("a", closed_by_agent_id="ana")) == "closed_by_agent"
    assert crm_state(row("a", confirmed_at="2026-11-06T10:05:00+00:00")) == "ready_never_sent"
    assert crm_state(row("a")) == "never_sent"


def test_search_by_name_route_chat_number_or_phone():
    r = row("a", chat_number=4810)
    assert matches(r, "michael") and matches(r, "lhr") and matches(r, "London")
    assert matches(r, "#4810") and matches(r, "4810")
    assert matches(r, "0110")
    assert not matches(r, "4811") and not matches(r, "paris")


def _rows() -> list[dict]:
    return [
        row("open", conversation_status="active", created_at="2026-11-06T11:00:00+00:00"),
        row("closed", closed_by_agent_id="ana", created_at="2026-11-06T09:00:00+00:00"),
        row("ready-unsent", confirmed_at="2026-11-06T08:05:00+00:00", created_at="2026-11-06T08:00:00+00:00"),
        row("refused", crm_push_gate_reason="email_typo_domain", created_at="2026-11-06T07:00:00+00:00"),
        row("in-crm", created_in_crm=True, crm_lead_id="208131", created_at="2026-11-05T07:00:00+00:00"),
        row("ready-review", created_in_crm=True, confirmed_at="2026-11-04T08:00:00+00:00",
            created_at="2026-11-04T07:00:00+00:00"),
        row("reviewed", created_in_crm=True, confirmed_at="2026-11-03T08:00:00+00:00", reviewed_by_qa=True,
            created_at="2026-11-03T07:00:00+00:00"),
        row("no-phone", visitor_phone=None, created_at="2026-11-02T07:00:00+00:00"),
    ]


def test_tabs_count_the_whole_tab_and_say_why():
    v = build_leads_view(_rows(), view="not_in_crm", names=NAMES)
    assert v["counts"] == {"not_in_crm": 4, "to_review": 2, "all": 8}
    assert [r["id"] for r in v["rows"]] == ["open", "closed", "ready-unsent", "refused"]   # newest first
    assert v["summary"] == {"open": 1, "closed_by_agent": 1, "ready_never_sent": 1, "never_sent": 0,
                            "refused": {"email_typo_domain": 1}}
    assert v["rows"][1]["closed_by_name"] == "Ana Ceban" and v["rows"][1]["agent_name"] == "Ana Ceban"
    review = build_leads_view(_rows(), view="to_review", names=NAMES)
    assert [r["id"] for r in review["rows"]] == ["ready-review", "ready-unsent"]       # oldest first
    assert review["order"] == "oldest" and "summary" not in review


def test_filters_narrow_rows_not_tab_counts():
    v = build_leads_view(_rows(), view="all", names=NAMES, level="ready", order="closest")
    assert v["count"] == 3 and v["counts"]["all"] == 8
    v = build_leads_view(_rows(), view="all", names=NAMES, with_="person")
    assert [r["id"] for r in v["rows"]] == ["closed"]
    v = build_leads_view(_rows(), view="all", names=NAMES, limit=2, offset=2)
    assert v["count"] == 8 and [r["id"] for r in v["rows"]] == ["ready-unsent", "refused"]


# ── The endpoint ─────────────────────────────────────────────────────


def _client(role: str) -> TestClient:
    from app.api.leads import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": "sup-1", "role": role, "tunnel_scope": "sales"}
    return TestClient(app)


def test_supervisor_sees_only_their_team_masked():
    rows = _rows() + [row("other-team", team_id="team-2", conversation_status="active")]
    with patch("app.db.supabase.get_leads_universe", new=AsyncMock(return_value=rows)), \
         patch("app.db.supabase.get_user_names", new=AsyncMock(return_value=NAMES)), \
         patch("app.db.supabase.get_team_ids_for_supervisor", new=AsyncMock(return_value=["team-1"])):
        r = _client("supervisor").get("/api/leads?view=not_in_crm")
    body = r.json()
    assert r.status_code == 200 and body["counts"]["not_in_crm"] == 4
    assert "other-team" not in [x["id"] for x in body["data"]]
    assert all("Michael Turner" != x["visitor_name"] for x in body["data"])


def test_a_supervisor_search_reads_only_what_they_see():
    r = row("a", chat_number=4810)
    # A supervisor sees •••er, m•••@example.com and •••0110, plus the route and chat number.
    for hidden in ("michael", "turner", "m@example", "5550110", "555 0"):
        assert matches(r, hidden) and not matches(r, hidden, masked=True), hidden
    for seen in ("er", "example.com", "0110", "lhr", "#4810"):
        assert matches(r, seen, masked=True), seen


def test_a_supervisor_cant_find_a_lead_by_its_hidden_contact():
    with patch("app.db.supabase.get_leads_universe", new=AsyncMock(return_value=_rows())), \
         patch("app.db.supabase.get_user_names", new=AsyncMock(return_value=NAMES)), \
         patch("app.db.supabase.get_team_ids_for_supervisor", new=AsyncMock(return_value=["team-1"])):
        sup = _client("supervisor").get("/api/leads?view=all&search=michael").json()
        sup_phone = _client("supervisor").get("/api/leads?view=all&search=5550").json()
        owner = _client("owner").get("/api/leads?view=all&search=michael").json()
    # The count would otherwise answer "is there a lead named Michael / with 5550 in the phone?".
    assert sup["count"] == 0 and sup_phone["count"] == 0
    assert owner["count"] == 8


def test_supervisor_without_a_team_sees_nothing():
    with patch("app.db.supabase.get_leads_universe", new=AsyncMock(return_value=_rows())), \
         patch("app.db.supabase.get_user_names", new=AsyncMock(return_value=NAMES)), \
         patch("app.db.supabase.get_team_ids_for_supervisor", new=AsyncMock(return_value=[])):
        body = _client("supervisor").get("/api/leads?view=all").json()
    assert body["count"] == 0 and body["counts"] == {"not_in_crm": 0, "to_review": 0, "all": 0}


def test_a_failed_read_is_a_502():
    with patch("app.db.supabase.get_leads_universe", new=AsyncMock(side_effect=RuntimeError("boom"))):
        assert _client("owner").get("/api/leads?view=all").status_code == 502


@pytest.mark.parametrize("role", ["sales", "support", "dev", "project_manager"])
def test_roles_without_leads_get_403(role):
    assert _client(role).get("/api/leads?view=all").status_code == 403


def test_the_classic_list_is_unchanged():
    get_leads = AsyncMock(return_value=([], 0))
    with patch("app.db.supabase.get_leads", new=get_leads), \
         patch("app.db.supabase.get_leads_review_counts", new=AsyncMock(return_value=(0, 0))), \
         patch("app.db.supabase.get_leads_universe", new=AsyncMock()) as universe:
        r = _client("owner").get("/api/leads?crm_pending=true&limit=50")
    assert r.status_code == 200 and universe.await_count == 0
    assert get_leads.await_args.kwargs["crm_pending"] is True and get_leads.await_args.kwargs["limit"] == 50


def test_every_panel_write_drops_the_shared_read():
    from app.db import supabase as db

    db._LEADS_UNIVERSE["at"] = 10**12
    with patch("app.db.supabase.update_lead", new=AsyncMock(return_value={"id": "L1"})):
        r = _client("qa").patch("/api/leads/L1/review", json={"reviewed": True})
    assert r.status_code == 200 and db._LEADS_UNIVERSE["at"] == 0.0
