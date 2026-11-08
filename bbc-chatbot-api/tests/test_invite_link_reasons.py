"""Why an invite link failed — app/db/supabase.py::diagnose_invite_token.

A re-invite retires the older links by setting used_at on them, so used_at
alone can't tell "you already set your password" from "a newer link
replaced this one". The account's password decides.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


class _Query:
    def __init__(self, rows):
        self._rows = rows
        self.filters: list[tuple] = []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def gt(self, col, val):
        self.filters.append(("gt", col, val))
        return self

    def is_(self, col, val):
        self.filters.append(("is", col, val))
        return self

    @property
    def not_(self):
        self.filters.append(("not",))
        return self

    def limit(self, n):
        return self

    def execute(self):
        res = MagicMock()
        res.data = self._rows
        return res


class _Client:
    """`tables[name]` is the rows every read of that table returns, or —
    with `reads[name]` — one list of rows per read, in order."""

    def __init__(self, tables: dict[str, list], reads: dict[str, list[list]] | None = None):
        self._tables = tables
        self._reads = {k: list(v) for k, v in (reads or {}).items()}
        self.queries: dict[str, list[_Query]] = {}

    def table(self, name):
        rows = self._reads[name].pop(0) if self._reads.get(name) else self._tables.get(name, [])
        q = _Query(rows)
        self.queries.setdefault(name, []).append(q)
        return q


def _token(**over):
    row = {
        "used_at": None,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "user_id": "u1",
        "created_at": "2026-11-01T10:00:00+00:00",
    }
    row.update(over)
    return row


async def _diagnose(client):
    from app.db import supabase as sb

    with patch.object(sb, "get_client", return_value=client):
        return await sb.diagnose_invite_token("t" * 24)


@pytest.mark.asyncio
async def test_a_used_link_on_an_account_with_a_password_is_used():
    client = _Client({"invite_tokens": [_token(used_at="2026-11-02T09:00:00+00:00")],
                      "users": [{"id": "u1"}]})
    assert await _diagnose(client) == "used"
    users_q = client.queries["users"][0]
    assert ("eq", "id", "u1") in users_q.filters
    assert ("is", "password_hash", "null") in users_q.filters and ("not",) in users_q.filters


@pytest.mark.asyncio
async def test_a_link_retired_by_a_re_invite_is_superseded_not_used():
    # used_at set by invalidate_active_invite_tokens; the account still has no password.
    client = _Client({"invite_tokens": [_token(used_at="2026-11-02T09:00:00+00:00")],
                      "users": []})
    assert await _diagnose(client) == "superseded"


@pytest.mark.asyncio
async def test_a_used_link_with_no_user_keeps_the_old_answer():
    client = _Client({"invite_tokens": [_token(used_at="2026-11-02T09:00:00+00:00", user_id=None)]})
    assert await _diagnose(client) == "used"
    assert "users" not in client.queries


@pytest.mark.asyncio
async def test_the_other_reasons_are_unchanged():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert await _diagnose(_Client({"invite_tokens": []})) == "unknown"
    # Expired: the second read (a newer token for the same user) finds none…
    expired = _Client({}, reads={"invite_tokens": [[_token(expires_at=past)], []]})
    assert await _diagnose(expired) == "expired"
    # …or finds one: replaced by a re-invite.
    newer = _Client({}, reads={"invite_tokens": [[_token(expires_at=past)], [{"id": "t2"}]]})
    assert await _diagnose(newer) == "superseded"
    assert "users" not in newer.queries
    # Not used and not expired, yet not consumable: the old "unknown".
    assert await _diagnose(_Client({"invite_tokens": [_token()]})) == "unknown"
