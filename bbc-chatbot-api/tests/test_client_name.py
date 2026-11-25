"""The client's name (the owner, 25 Nov 2026): in the week before, 155 of 188 chats an agent had carried no name,
and 72 of them reached the CRM without one. The agent who has the chat writes or corrects it in the panel
(POST /api/conversations/{id}/name), and the chat reads it from two more shapes of the client's own words.
Every name, address and number here is invented."""

import logging
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

from app.api import conversations as conv_api
from app.pipeline.contact_capture import (
    name_beside_contact, name_from_answer, name_from_intro, name_in_reply, typed_name,
)
from app.security.auth import get_current_user

AGENT = "aaaaaaaa-0000-4000-8000-0000000000a1"
OTHER = "aaaaaaaa-0000-4000-8000-0000000000a2"
ASKED_NAME = "Happy to help. Could I have your name and e-mail?"
ASKED_DATES = "Which dates work for you?"


# ── What a client's message names ───────────────────────────────


@pytest.mark.parametrize("message, name", [
    ("Hi, my name is Ana Popescu, two of us to Rome", "Ana Popescu"),
    ("my name is ana popescu please call me", "Ana Popescu"),
    ("My name is John A. Smith and I need 2 tickets", "John A. Smith"),
    ("My name’s Ana but my husband travels", "Ana"),
    ("Hello! My name is Paul. I want business class to Paris", "Paul"),
    ("My first name is Ana", "Ana"),
    ("me llamo maría josé y viajo a Madrid", "María José"),
    ("Je m’appelle Lucie Martin.", "Lucie Martin"),
    ("meu nome é Ana e moro em Lisboa", "Ana"),
    # Four words at most, and never a particle at the end.
    ("My name is María José García de la Fuente", "María José García"),
])
def test_my_name_is_names_the_client(message, name):
    assert name_from_intro(message) == name


@pytest.mark.parametrize("message", [
    "my name is on the booking",
    "my name is spelled with a K",
    "my name is not important",
    "my wife's name is Maria",
    "I'm looking for flights",          # the extractor's old mistake: "I'm …" names nobody
    "This is urgent",
    "my name is 007",
    "Paul Moreau",
    "",
])
def test_anything_else_names_nobody(message):
    assert name_from_intro(message) is None


@pytest.mark.parametrize("message, name", [
    ("Ana Popescu, ana@example.com", "Ana Popescu"),
    ("ana@example.com - Ana", "Ana"),
    ("Ana, +40 721 000 000", "Ana"),
    ("Paul Moreau and paul@example.com", "Paul Moreau"),
    ("Email: paul@example.com, name: Paul Moreau", "Paul Moreau"),
    ("Full name: Ana Popescu, +40 721 000 000", "Ana Popescu"),
])
def test_the_name_beside_the_contact(message, name):
    assert name_beside_contact(message) == name


@pytest.mark.parametrize("message", [
    "Ana, Boston, ana@example.com",     # two pieces of text: a name and a city read alike
    "Sure, it's paul.moreau@example.com",
    "you can call me at +1 (305) 555-0101 after 6pm",
    "paul.moreau@example.com or +44 7700 900123",
    "Write to ana@example.com",
    "Here: ana@example.com",
    "ana@example.com to Rome",
    "JFK to LHR on 2026-12-20",         # a date is no phone
    "Ana 2026-12-20",
    "Paul Moreau",                      # no contact: name_from_answer's case
])
def test_beside_no_contact_or_beside_more_than_a_name_nobody(message):
    assert name_beside_contact(message) is None


def test_beside_the_contact_only_when_the_name_was_asked():
    assert name_in_reply("Ana Popescu, ana@example.com", ASKED_NAME) == "Ana Popescu"
    assert name_in_reply("Ana Popescu", ASKED_NAME) == "Ana Popescu"
    # Rule 5: the same messages, the name not asked.
    assert name_in_reply("Ana Popescu, ana@example.com", ASKED_DATES) is None
    assert name_in_reply("Ana Popescu", ASKED_DATES) is None
    # "my name is …" needs no question.
    assert name_in_reply("my name is Ana Popescu", ASKED_DATES) == "Ana Popescu"


def test_an_initial_is_no_article():
    assert name_from_answer("John A. Smith") == "John A. Smith"
    assert name_from_answer("a smith") is None


@pytest.mark.parametrize("typed, name", [
    ("ana  popescu", "Ana Popescu"),
    ("Hope", "Hope"),
    ("Mary-Ann O’Neil", "Mary-Ann O'Neil"),
    ("J. R. Smith", "J. R. Smith"),
    ("María José García de la Fuente", "María José García de la Fuente"),
    ("maría josé garcía de la fuente", "María José García de la Fuente"),
    ("  Ana Popescu! ", "Ana Popescu"),
    ("Smith Jr.", "Smith Jr."),
])
def test_a_typed_name_is_tidied(typed, name):
    assert typed_name(typed) == name


@pytest.mark.parametrize("typed", [
    "", "  ", "A", "ana@example.com", "Ana 2", "+40 721 000 000", "www.example.com", "ana.popescu",
    "https://example.com", "a b c d e f g", "x" * 61,
])
def test_a_typed_non_name_is_refused(typed):
    assert typed_name(typed) is None


# ── The panel's endpoint ────────────────────────────────────────


def _as(role: str, user_id: str = AGENT):
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id, "role": role, "email": f"{role}@example.test", "name": role,
        "tunnel_scope": "sales" if role in ("sales", "support") else "all",
    }


@pytest.fixture(autouse=True)
def _clear():
    before = dict(conv_api.CLIENT_NAME_HEALTH)
    yield
    app.dependency_overrides.clear()
    conv_api.CLIENT_NAME_HEALTH.update(before)


def _row(**over) -> dict:
    row = {"id": "c-1", "status": "active", "mode": "human", "assigned_agent_id": AGENT, "engaged_agent_id": None,
           "tunnel": "sales", "team_id": None, "queued_at": None}
    row.update(over)
    return row


async def _post(role: str, name: str, *, row=None, read_error=None, saved=True, user_id=AGENT):
    read = AsyncMock(return_value=row if row is not None else _row(), side_effect=read_error)
    write = AsyncMock(return_value=saved)
    audit = AsyncMock()
    with patch("app.db.supabase.get_blind_inputs", new=read), \
         patch("app.services.lead_service.update_lead_from_entities", new=write), \
         patch("app.db.supabase.log_audit", new=audit):
        _as(role, user_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/conversations/c-1/name", json={"name": name})
    return r, read, write, audit


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [
    _row(),                                                                  # assigned to them
    _row(assigned_agent_id=None, mode="ai", engaged_agent_id=AGENT),         # theirs, back with the assistant
    _row(status="closed"),                                                   # closed, still theirs
])
async def test_the_agent_on_the_chat_saves_the_name(row, caplog):
    before = conv_api.CLIENT_NAME_HEALTH["saved"]
    with caplog.at_level(logging.INFO):
        r, _, write, audit = await _post("sales", "ana popescu", row=row)
    assert r.status_code == 200
    assert r.json()["data"] == {"visitor_name": "Ana Popescu"}
    # Through the lead's writer: the chat's visitor_name, under its lock, and the score counts it.
    write.assert_awaited_once_with("c-1", {"name": "Ana Popescu"})
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["details"] == {"fields": ["visitor_name"], "role": "sales"}
    assert conv_api.CLIENT_NAME_HEALTH["saved"] == before + 1
    # Backend rule 7: never a name in the logs.
    assert "Popescu" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "admin", "dev"])
async def test_a_manager_saves_it_on_any_chat(role):
    r, _, write, _ = await _post(role, "Ana Popescu", row=_row(assigned_agent_id=OTHER), user_id="m-1")
    assert r.status_code == 200
    write.assert_awaited_once_with("c-1", {"name": "Ana Popescu"})


@pytest.mark.asyncio
@pytest.mark.parametrize("role, row", [
    ("sales", _row(assigned_agent_id=OTHER)),                                # a colleague's chat
    ("support", _row(assigned_agent_id=None, mode="waiting_for_agent", status="needs_agent")),  # waiting, blind
    ("supervisor", _row()),
    ("qa", _row()),
    ("project_manager", _row()),
])
async def test_anyone_else_is_refused_and_nothing_is_written(role, row):
    before = conv_api.CLIENT_NAME_HEALTH["not_allowed"]
    r, _, write, audit = await _post(role, "Ana Popescu", row=row,
                                     user_id=AGENT if role in ("sales", "support") else "x-1")
    assert r.status_code == 403
    assert r.json()["detail"] == conv_api._NAME_NOT_YOURS_DETAIL
    write.assert_not_awaited()
    audit.assert_not_awaited()
    assert conv_api.CLIENT_NAME_HEALTH["not_allowed"] == before + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["ana@example.com", "+40 721 000 000", "A", "www.example.com", "  "])
async def test_what_is_not_a_name_is_refused_before_anything_is_read(name):
    before = conv_api.CLIENT_NAME_HEALTH["not_a_name"]
    r, read, write, _ = await _post("sales", name)
    assert r.status_code == 422
    assert r.json()["detail"] == conv_api._NOT_A_NAME_DETAIL
    read.assert_not_awaited()
    write.assert_not_awaited()
    assert conv_api.CLIENT_NAME_HEALTH["not_a_name"] == before + 1


@pytest.mark.asyncio
async def test_a_chat_that_doesnt_exist_is_a_404():
    read = AsyncMock(return_value=None)
    write = AsyncMock()
    with patch("app.db.supabase.get_blind_inputs", new=read), \
         patch("app.services.lead_service.update_lead_from_entities", new=write):
        _as("owner", "o-1")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/conversations/c-1/name", json={"name": "Ana Popescu"})
    assert r.status_code == 404
    write.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_chat_that_cant_be_read_or_a_write_that_fails_is_a_503_and_counted():
    """Rule 7: "couldn't look" is never "no chat"; rule 2: a failure on the lead's path is counted."""
    before = conv_api.CLIENT_NAME_HEALTH["failed"]
    r, _, write, _ = await _post("sales", "Ana Popescu", read_error=RuntimeError("down"))
    assert r.status_code == 503
    write.assert_not_awaited()
    r, _, write, audit = await _post("sales", "Ana Popescu", saved=False)
    assert r.status_code == 503
    write.assert_awaited_once()
    audit.assert_not_awaited()
    assert conv_api.CLIENT_NAME_HEALTH["failed"] == before + 2


def test_health_shows_the_client_name_counts():
    import inspect

    from app.api import health

    assert inspect.getsource(health.health_detail).count('"client_name": dict(CLIENT_NAME_HEALTH)') == 2
