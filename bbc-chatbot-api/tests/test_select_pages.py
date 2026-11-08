"""_select_pages never passes a partial read off as the whole one.

Every paged read is ordered oldest first, so the rows past the page cap are
the newest: the ones the current period is made of. A dashboard built from
that read would undercount today without saying so. A read still full at the
cap raises, and the endpoint answers with its usual error (502).
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.db import supabase as db  # noqa: E402


class _Table:
    """A query builder over `total` rows: .range(lo, hi).execute()."""

    def __init__(self, total: int):
        self.total = total
        self.lo = self.hi = 0

    def range(self, lo: int, hi: int) -> "_Table":
        self.lo, self.hi = lo, hi
        return self

    def execute(self):
        n = max(0, min(self.hi, self.total - 1) - self.lo + 1)
        return type("Res", (), {"data": [{"i": self.lo + k} for k in range(n)]})()


def _read(total: int, max_pages: int) -> list[dict]:
    return asyncio.run(db._select_pages(lambda: _Table(total), label="test", max_pages=max_pages))


def test_a_short_page_ends_the_read():
    rows = _read(2 * db._PAGE + 500, max_pages=5)
    assert len(rows) == 2 * db._PAGE + 500
    assert rows[-1]["i"] == 2 * db._PAGE + 499


def test_a_read_that_ends_exactly_at_the_cap_is_whole():
    assert len(_read(3 * db._PAGE, max_pages=3)) == 3 * db._PAGE


def test_a_read_with_more_rows_than_the_cap_raises():
    with pytest.raises(RuntimeError, match="partial"):
        _read(3 * db._PAGE + 1, max_pages=3)
