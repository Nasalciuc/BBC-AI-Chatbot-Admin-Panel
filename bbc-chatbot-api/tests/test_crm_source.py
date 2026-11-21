"""How each CRM id got there, its shape, the same id on another client, and the CRM lookup adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.db import supabase as db


class _Res:
    def __init__(self, data):
        self.data = data


async def _mark(source="push", set_by=None, shape=None, noted=True):
    async def _sync(fn, *a, **k):
        return _Res([{"id": "L1", "conversation_id": "c1", "created_in_crm": True}])

    note = AsyncMock(return_value=noted)
    with patch.object(db, "get_client", return_value=MagicMock()), patch.object(db, "_run_sync", _sync), \
            patch.object(db, "patch_conversation_presence", new=note):
        row = await db.mark_lead_created_in_crm("L1", crm_lead_id="R-1", source=source, set_by=set_by, shape=shape)
    return row, note


@pytest.mark.parametrize("source", ["push", "cron", "manual"])
@pytest.mark.asyncio
async def test_the_chat_says_how_its_lead_reached_the_crm(source):
    row, note = await _mark(source=source, set_by="agent-1" if source == "manual" else None)
    assert row["created_in_crm"] is True
    cid, patch_ = note.await_args.args
    assert cid == "c1" and patch_["crm"]["source"] == source and patch_["crm"]["set_at"]


@pytest.mark.asyncio
async def test_a_note_that_is_not_saved_never_fails_the_mark():
    before = db.CRM_SOURCE_HEALTH["not_noted"]
    row, _ = await _mark(noted=False)
    assert row and db.CRM_SOURCE_HEALTH["not_noted"] == before + 1


@pytest.mark.asyncio
async def test_the_switch_turns_the_note_off(monkeypatch):
    monkeypatch.setenv("CRM_SOURCE_RECORD", "false")
    _, note = await _mark()
    note.assert_not_awaited()


def test_the_crms_own_ids_are_24_hex_characters():
    from app.api.leads import crm_id_shape

    assert crm_id_shape("64f1a2b3c4d5e6f708192a3b") == "ok"
    assert crm_id_shape("64F1A2B3C4D5E6F708192A3B") == "ok"
    for odd in ("MAN-1", "12345", "64f1a2b3c4d5e6f708192a3", "64f1a2b3c4d5e6f708192a3bz", "x" * 24):
        assert crm_id_shape(odd) == "odd"


_LEAD = {"id": "L1", "conversation_id": "c1"}
_MINE = {"id": "c1", "visitor_email": "dana@example.com", "chat_number": 100, "metadata": {}}


def _paste(crm_id, *, confirm_odd=False, others=None, convs=None, user=None, dup_error=None):
    from app.api import leads as leads_api

    convs = convs or {"c1": _MINE}
    mark = AsyncMock(return_value={"id": "L1", "created_in_crm": True, "crm_lead_id": crm_id})
    audit = AsyncMock()
    patches = [
        patch.object(db, "get_lead_full", new=AsyncMock(return_value=dict(_LEAD))),
        patch.object(db, "get_conversation_simple", new=AsyncMock(side_effect=lambda cid: convs.get(cid))),
        patch.object(db, "get_leads_by_crm_id", new=AsyncMock(side_effect=dup_error, return_value=others or [])),
        patch.object(db, "mark_lead_created_in_crm", new=mark),
        patch.object(db, "log_audit", new=audit),
        patch.object(db, "invalidate_leads_universe"),
    ]
    for p in patches:
        p.start()

    async def run():
        try:
            return await leads_api.set_manual_crm_id("L1", crm_id=crm_id, confirm_odd=confirm_odd,
                                                     user=user or {"role": "admin", "id": "adm"})
        finally:
            for p in patches:
                p.stop()
    return run, mark, audit


@pytest.mark.asyncio
async def test_an_odd_id_asks_once_and_is_kept_marked_when_confirmed():
    run, mark, _ = _paste("MAN-1")
    with pytest.raises(HTTPException) as e:
        await run()
    assert e.value.status_code == 422
    mark.assert_not_awaited()
    run, mark, audit = _paste("MAN-1", confirm_odd=True)
    out = await run()
    assert out["shape"] == "odd" and mark.await_args.kwargs["shape"] == "odd"
    assert audit.await_args.args[2] == "crm_id_set" and audit.await_args.args[5]["shape"] == "odd"


@pytest.mark.asyncio
async def test_a_crm_shaped_id_goes_straight_in():
    run, mark, _ = _paste("64f1a2b3c4d5e6f708192a3b")
    out = await run()
    assert out["shape"] == "ok"
    assert mark.await_args.kwargs == {"crm_lead_id": "64f1a2b3c4d5e6f708192a3b", "source": "manual",
                                      "set_by": "adm", "shape": "ok"}


@pytest.mark.asyncio
async def test_the_same_id_on_another_client_is_a_409_naming_the_chat():
    other = {"id": "c9", "visitor_email": "someone@example.com", "chat_number": 4242, "metadata": {}}
    run, mark, _ = _paste("64f1a2b3c4d5e6f708192a3b", others=[{"id": "L9", "conversation_id": "c9"}],
                          convs={"c1": _MINE, "c9": other})
    with pytest.raises(HTTPException) as e:
        await run()
    assert e.value.status_code == 409 and "#4242" in e.value.detail
    mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_same_client_in_another_chat_may_share_the_id():
    """Rule 5: one client, several chats, one CRM lead: not a duplicate."""
    again = {"id": "c9", "visitor_email": "Dana@example.com", "chat_number": 4242, "metadata": {}}
    run, mark, _ = _paste("64f1a2b3c4d5e6f708192a3b", others=[{"id": "L9", "conversation_id": "c9"}],
                          convs={"c1": _MINE, "c9": again})
    assert (await run())["success"]
    mark.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_duplicate_check_that_cannot_run_does_not_pass():
    run, mark, _ = _paste("64f1a2b3c4d5e6f708192a3b", dup_error=RuntimeError("db down"))
    with pytest.raises(HTTPException) as e:
        await run()
    assert e.value.status_code == 503
    mark.assert_not_awaited()


# ── the CRM lookup adapter ──────────────────────────────────────


@pytest.mark.asyncio
async def test_the_lookup_is_off_until_the_crm_has_the_route():
    from app.services import crm

    assert await crm.lookup("64f1a2b3c4d5e6f708192a3b") is None


@pytest.mark.parametrize("status, expected", [(200, True), (404, False), (500, None)])
@pytest.mark.asyncio
async def test_the_lookup_answers_from_the_crm(monkeypatch, status, expected):
    from app.services import crm
    from config.settings import settings

    monkeypatch.setenv("CRM_LOOKUP_PATH", "/requests/{id}")
    monkeypatch.setattr(settings, "crm_api_url", "https://crm.example.test")
    client = MagicMock()
    client.get = AsyncMock(return_value=MagicMock(status_code=status))
    with patch.object(crm, "_get_crm_client", return_value=client):
        assert await crm.lookup("64f1a2b3c4d5e6f708192a3b") is expected
    assert client.get.await_args.args[0].endswith("/requests/64f1a2b3c4d5e6f708192a3b")
