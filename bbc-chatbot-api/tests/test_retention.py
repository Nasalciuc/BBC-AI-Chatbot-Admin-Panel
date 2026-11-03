"""PR-14 — retention counts by default and deletes in bounded batches when on."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.services import retention


class _Q:
    """A chainable fake of the PostgREST builder that records the calls."""

    def __init__(self, log, table, rows):
        self.log, self.table, self.rows, self.op = log, table, rows, "select"

    def select(self, *a, **k):
        self.op = "count" if k.get("count") else "select"
        return self

    def delete(self):
        self.op = "delete"
        return self

    def lt(self, col, v):
        self.log.append((self.table, "lt", col))
        return self

    def in_(self, col, ids):
        self.ids = ids
        return self

    def limit(self, n):
        self.n = n
        return self

    def execute(self):
        class R:
            pass
        r = R()
        if self.op == "count":
            r.data, r.count = [], 1234
        elif self.op == "select":
            take = self.rows[: self.n]
            r.data = [{"id": i} for i in take]
        else:
            self.log.append((self.table, "delete", len(self.ids)))
            del self.rows[: len(self.ids)]
            r.data = []
        return r


@pytest.fixture
def fake(monkeypatch):
    log, rows = [], {t: list(range(1200)) for t in retention.TABLES}

    class C:
        def table(self, t):
            return _Q(log, t, rows[t])

    async def _run_sync(fn, **kw):
        return fn()

    monkeypatch.setattr(retention, "get_client", lambda: C())
    monkeypatch.setattr(retention, "_run_sync", _run_sync)
    flags._reset_for_tests()
    return log, rows


@pytest.mark.asyncio
async def test_off_only_counts(fake):
    log, rows = fake
    out = await retention.run_once()
    assert all(v == {"would_delete": 1234} for v in out.values())
    assert not any(op == "delete" for _, op, *_ in log)
    # The right clock column per table.
    assert ("agent_activity_log", "lt", "happened_at") in log
    assert ("agent_ready_log", "lt", "changed_at") in log
    assert ("blocklist", "lt", "expires_at") in log


@pytest.mark.asyncio
async def test_on_deletes_in_batches(fake, monkeypatch):
    log, rows = fake
    monkeypatch.setenv("RETENTION_ENABLED", "true")
    out = await retention.run_once()
    assert out["pipeline_runs"] == {"deleted": 1200}
    sizes = [n for t, op, n in (x for x in log if x[1] == "delete") if t == "pipeline_runs"]
    assert sizes == [100] * 12


def test_conversations_and_messages_are_never_in_scope():
    assert "conversations" not in retention.TABLES
    assert "messages" not in retention.TABLES
