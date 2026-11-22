"""GET /api/leads/check/existing (part 6, the security pass): management only, and a query that finds the lead it
says it finds. Before: any panel role could ask a client's name and contact by e-mail or phone; the chat was not
an inner embed, so the answer was the newest lead of all; "e-mail or phone" was a top-level or() on embedded
columns, which PostgREST v12 refuses (and the values went in unquoted). Every name here is invented."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import leads as leads_api  # noqa: E402
from app.db import supabase as db  # noqa: E402
from app.security.auth import get_current_user  # noqa: E402
from tests.postgrest_eval import query_keeps  # noqa: E402

FOUND = {"id": "l1", "conversation_id": "c1", "visitor_name": "Dana Example", "visitor_email": "dana@example.com",
         "visitor_phone": "+1 555 0100", "assigned_agent_id": None, "tier": "gold", "status": "new", "tunnel": "sales"}


def _ask(role, query="email=dana@example.com", found=FOUND, error=None):
    app = FastAPI()
    app.include_router(leads_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "role": role}
    look = AsyncMock(return_value=found, side_effect=error)
    with patch.object(db, "get_existing_lead_by_contact", new=look):
        r = TestClient(app).get(f"/api/leads/check/existing?{query}")
    return r, look


@pytest.mark.parametrize("role", ["sales", "support", "supervisor", "qa", "project_manager", ""])
def test_only_management_asks_who_a_contact_is(role):
    r, look = _ask(role)
    assert r.status_code == 403
    look.assert_not_awaited()


@pytest.mark.parametrize("role", ["owner", "admin", "dev"])
def test_management_gets_the_lead(role):
    r, _ = _ask(role)
    assert r.status_code == 200 and r.json()["exists"] is True and r.json()["visitor_name"] == "Dana Example"


def test_none_found_and_could_not_look_are_two_answers():
    r, _ = _ask("owner", found=None)
    assert r.json() == {"success": True, "data": None, "count": 0, "exists": False}
    r, _ = _ask("owner", error=RuntimeError("connection to db-internal.example.test:5432 refused"))
    out = r.json()
    assert out["success"] is False and out["exists"] is False and out["data"] is None and out["count"] == 0
    assert "db-internal" not in out["error"], "no database text reaches the caller"


def test_every_answer_carries_the_admin_envelope():
    """{success, data, count, error?} on each answer (the repository's admin contract), the flat keys kept."""
    r, _ = _ask("owner")
    out = r.json()
    assert out["success"] is True and out["count"] == 1 and out["data"]["visitor_name"] == "Dana Example"
    assert out["exists"] is True and out["visitor_name"] == "Dana Example", "what the CRM side reads stays"


# ── the query, held to rows the way PostgREST reads it ───────────


class _Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a, k))
            return self
        return rec

    def execute(self):
        res = MagicMock()
        res.data = []
        return res


def _calls(**kw):
    q = _Recorder()
    client = MagicMock()
    client.table.return_value = q
    with patch.object(db, "get_client", return_value=client):
        assert asyncio.run(db.get_existing_lead_by_contact(**kw)) is None
    return q.calls


def _lead(email=None, phone=None):
    return {"id": "l1", "conversations": [{"id": "c1", "visitor_email": email, "visitor_phone": phone}]}


def test_the_chat_is_an_inner_embed():
    select = next(args[0] for name, args, _ in _calls(email="dana@example.com") if name == "select")
    assert "conversations!inner(" in select


@pytest.mark.parametrize("kw, lead, kept", [
    ({"email": "Dana@Example.com"}, _lead(email="dana@example.com"), True),
    ({"email": "dana@example.com"}, _lead(email="lee@example.com"), False),
    ({"phone": "+1 555 0100"}, _lead(phone="+1 555 0100"), True),
    ({"phone": "+1 555 0100"}, _lead(phone="+1 555 0199"), False),
    ({"email": "dana@example.com", "phone": "+1 555 0100"}, _lead(phone="+1 555 0100"), True),
    ({"email": "dana@example.com", "phone": "+1 555 0100"}, _lead(email="dana@example.com"), True),
    ({"email": "dana@example.com", "phone": "+1 555 0100"}, _lead(email="lee@example.com", phone="+1 555 0199"), False),
])
def test_it_finds_the_lead_of_that_contact_only(kw, lead, kept):
    assert query_keeps(_calls(**kw), lead, embed="conversations") is kept


def test_a_comma_typed_in_the_contact_adds_no_condition():
    calls = _calls(email="x@example.com,visitor_email.like.*", phone="1,visitor_phone.not.is.null")
    for victim in (_lead(email="dana@example.com"), _lead(phone="+1 555 0100")):
        assert query_keeps(calls, victim, embed="conversations") is False
    ors = [(a, k) for name, a, k in calls if name == "or_"]
    assert ors and all(k.get("reference_table") == "conversations" for _, k in ors), "one condition on the embed"


def test_a_failed_read_raises_instead_of_answering_none():
    client = MagicMock()
    client.table.side_effect = RuntimeError("db down")
    with patch.object(db, "get_client", return_value=client), pytest.raises(RuntimeError):
        asyncio.run(db.get_existing_lead_by_contact(email="dana@example.com"))
