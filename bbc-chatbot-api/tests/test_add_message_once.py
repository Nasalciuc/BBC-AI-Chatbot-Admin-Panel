"""A message stored under an id its caller fixed (app.db.supabase.add_message, message_id): an insert made again
finds the row an earlier one stored and stores nothing (Messenger's events, CodeRabbit #288). Every id and text here
is invented."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from postgrest.exceptions import APIError  # noqa: E402

from app.db import supabase as db  # noqa: E402

CONV = "0f9e8d7c-6b5a-4c3d-8e2f-1a0b9c8d7e6f"
MID = "6b1f0c3e-2d4a-5e6f-8a9b-0c1d2e3f4a5b"
ROW = {"id": MID, "conversation_id": CONV, "role": "user", "content": "[photo]", "created_at": "2026-11-26T01:00:00Z"}


def _client(insert=None, read=None):
    """A client whose insert answers `insert` (a row, or an exception raised) and whose read by id answers `read`."""
    c = MagicMock()
    ins = c.table.return_value.insert
    if isinstance(insert, Exception):
        ins.return_value.execute.side_effect = insert
    else:
        ins.return_value.execute.return_value = SimpleNamespace(data=[insert] if insert else [])
    sel = c.table.return_value.select.return_value.eq.return_value.limit.return_value.execute
    if isinstance(read, Exception):
        sel.side_effect = read
    else:
        sel.return_value = SimpleNamespace(data=[read] if read else [])
    return c


async def _add(client, **kw):
    touch = AsyncMock()
    with patch.object(db, "get_client", return_value=client), patch.object(db, "_touch_conversation_activity", new=touch):
        row = await db.add_message(CONV, "user", "[photo]", **kw)
    return row, touch, client.table.return_value.insert.call_args


@pytest.mark.asyncio
async def test_a_message_goes_in_under_the_id_its_caller_fixed():
    row, touch, call = await _add(_client(insert=ROW), message_id=MID)
    assert call.args[0]["id"] == MID and row == ROW and not isinstance(row, db.StoredBefore)
    touch.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_insert_made_again_finds_the_row_stored_before_and_stores_nothing():
    dup = APIError({"code": "23505", "message": 'duplicate key value violates unique constraint "messages_pkey"'})
    row, touch, _ = await _add(_client(insert=dup, read=ROW), message_id=MID)
    assert isinstance(row, db.StoredBefore) and row == ROW
    touch.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_row_stored_before_that_cannot_be_read_is_not_known():
    """Rule 7: never "stored" without the row read back, and never None for "not there" either (it raises inside,
    and the caller gets None: try again later)."""
    dup = APIError({"code": "23505", "message": "duplicate key"})
    row, _, _ = await _add(_client(insert=dup, read=RuntimeError("connection reset")), message_id=MID)
    assert row is None
    row, _, _ = await _add(_client(insert=dup, read=None), message_id=MID)
    assert row is None


@pytest.mark.asyncio
async def test_only_the_databases_code_on_an_insert_with_an_id_reads_as_stored_before():
    """Rule 5: another error with an id, or a 23505 on an insert without one, is a failure (None), never a row."""
    other = APIError({"code": "23503", "message": "violates foreign key constraint, key 23505"})
    row, _, _ = await _add(_client(insert=other, read=ROW), message_id=MID)
    assert row is None
    row, _, call = await _add(_client(insert=APIError({"code": "23505", "message": "duplicate key"}), read=ROW))
    assert row is None and "id" not in call.args[0]
