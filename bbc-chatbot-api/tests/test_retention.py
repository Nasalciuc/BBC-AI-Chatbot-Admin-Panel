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


OLD = "2000-01-01T00:00:00+00:00"
RECENT = "2999-01-01T00:00:00+00:00"
_COL = {t: c for t, (c, _age) in retention._LOGS.items()}


class _Q:
    """A chainable fake of the PostgREST builder that APPLIES the filters
    (review fix: it used to record them and keep every row eligible)."""

    def __init__(self, log, table, rows):
        self.log, self.table, self.rows, self.op = log, table, rows, "select"
        self.preds = []

    def select(self, *a, **k):
        self.op = "count" if k.get("count") else "select"
        return self

    def delete(self):
        self.op = "delete"
        return self

    def lt(self, col, v):
        self.log.append((self.table, "lt", col))
        self.preds.append(lambda r, col=col, v=v: r.get(col) is not None and r[col] < v)
        return self

    def or_(self, expr):
        self.log.append((self.table, "or", expr))
        parts = []
        for clause in expr.split(","):
            col, op, v = clause.split(".", 2)
            assert op == "lt"
            parts.append((col, v))
        self.preds.append(lambda r, parts=parts: any(r.get(c) is not None and r[c] < v for c, v in parts))
        return self

    def in_(self, col, ids):
        self.ids = ids
        self.preds.append(lambda r, ids=set(ids): r["id"] in ids)
        return self

    def limit(self, n):
        self.n = n
        return self

    def _match(self):
        return [r for r in self.rows if all(p(r) for p in self.preds)]

    def execute(self):
        class R:
            pass
        r = R()
        hits = self._match()
        if self.op == "count":
            r.data, r.count = [], len(hits)
        elif self.op == "select":
            r.data = [{"id": h["id"]} for h in hits[: self.n]]
        else:
            self.log.append((self.table, "delete", len(hits)))
            gone = {h["id"] for h in hits}
            self.rows[:] = [x for x in self.rows if x["id"] not in gone]
            r.data = [{"id": i} for i in gone]
        return r


def _rows(table):
    """1200 rows old enough to go, and rows that must stay."""
    if table in _COL:
        col = _COL[table]
        return ([{"id": f"o{i}", col: OLD} for i in range(1200)]
                + [{"id": f"k{i}", col: RECENT} for i in range(7)])
    if table == "invite_tokens":
        return ([{"id": f"o{i}", "expires_at": OLD, "used_at": None} for i in range(1199)]
                # used long ago, still "valid" by its expiry: goes
                + [{"id": "used-early", "expires_at": RECENT, "used_at": OLD}]
                # used just now, or never used and not expired: stays
                + [{"id": "used-now", "expires_at": RECENT, "used_at": RECENT},
                   {"id": "open", "expires_at": RECENT, "used_at": None}])
    if table == "blocklist":
        return ([{"id": f"o{i}", "expires_at": OLD} for i in range(1200)]
                + [{"id": "permanent", "expires_at": None}, {"id": "future", "expires_at": RECENT}])
    raise AssertionError(table)


@pytest.fixture
def fake(monkeypatch):
    log, rows = [], {t: _rows(t) for t in retention.TABLES}

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
    assert all(v == {"would_delete": 1200} for v in out.values()), out
    assert not any(op == "delete" for _, op, *_ in log)
    # The right clock column per table.
    assert ("agent_activity_log", "lt", "happened_at") in log
    assert ("agent_ready_log", "lt", "changed_at") in log
    assert ("blocklist", "lt", "expires_at") in log


@pytest.mark.asyncio
async def test_on_deletes_in_batches_and_keeps_what_is_not_old(fake, monkeypatch):
    log, rows = fake
    monkeypatch.setenv("RETENTION_ENABLED", "true")
    out = await retention.run_once()
    assert all(v == {"deleted": 1200} for v in out.values()), out
    sizes = [n for t, op, n in (x for x in log if x[1] == "delete") if t == "pipeline_runs"]
    assert sizes == [100] * 12
    assert {r["id"] for r in rows["pipeline_runs"]} == {f"k{i}" for i in range(7)}
    assert {r["id"] for r in rows["invite_tokens"]} == {"used-now", "open"}
    assert {r["id"] for r in rows["blocklist"]} == {"permanent", "future"}


@pytest.mark.asyncio
async def test_a_row_refreshed_after_it_was_selected_is_not_deleted(fake, monkeypatch):
    """The blocklist upsert keeps the id and moves the expiry: the delete
    rechecks the age, so the refreshed entry stays."""
    log, rows = fake
    monkeypatch.setenv("RETENTION_ENABLED", "true")
    real_ids = retention._ids

    async def _ids_then_refresh(table, add_filter, limit):
        ids = await real_ids(table, add_filter, limit)
        if table == "blocklist" and "o0" in ids:
            for r in rows["blocklist"]:
                if r["id"] == "o0":
                    r["expires_at"] = RECENT      # an operator refreshed it
        return ids

    monkeypatch.setattr(retention, "_ids", _ids_then_refresh)
    out = await retention.run_once()
    assert "o0" in {r["id"] for r in rows["blocklist"]}
    assert out["blocklist"] == {"deleted": 1199}


def test_conversations_and_messages_are_never_in_scope():
    assert "conversations" not in retention.TABLES
    assert "messages" not in retention.TABLES
