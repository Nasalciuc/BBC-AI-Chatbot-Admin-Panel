"""Wave 4 PR 4 — derived-tag filtering is accurate beyond page 1.

The derivation itself (derive_conversation_tag, 6 tags, read-time,
retroactive) shipped in #158/#159 and is pinned by test_supervisor_suite.
What was broken: the tag filter ran on the CURRENT PAGE only (limit 50),
so matching conversations beyond page 1 were invisible and counts were
page-local. A true SQL WHERE mirror is impossible in PostgREST (the rules
compare column to column: last_user_message_at > last_agent_message_at,
and `completed` needs the lead join) — so a tag-filtered request now scans
a superset (_TAG_SCAN_CAP newest rows + the cheap DB pre-filters), derives
on all of it, then pages in Python with the true count.

Parity: the DB pre-filter predicates are re-expressed here in Python and
checked against derive_conversation_tag on shared fixtures — exact
equivalence for no_engagement, superset for abandoned.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.db import supabase as sb
from app.db.supabase import _TAG_SCAN_CAP, derive_conversation_tag

NOW = datetime(2026, 8, 12, 12, 0, 0, tzinfo=timezone.utc)
QUIET_MIN = 30


def _iso(minutes_ago: int) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def _conv(**kw) -> dict:
    base = {
        "id": kw.pop("id", "c-1"),
        "status": "active",
        "mode": "ai",
        "metadata": {},
        "visitor_name": None,
        "visitor_phone": None,
        "visitor_email": None,
        "last_user_message_at": None,
        "last_agent_message_at": None,
        "last_reply_at": None,
        "assigned_agent_id": None,
    }
    base.update(kw)
    return base


# Shared fixtures — one per tag family plus edges.
FIXTURES: list[dict] = [
    _conv(id="ne-1", visitor_name="A", visitor_phone="+1", visitor_email="a@b.c"),
    _conv(id="fresh-1", last_user_message_at=_iso(5)),
    _conv(
        id="aband-1",
        metadata={"engaged_agent_id": "op-9"},
        last_user_message_at=_iso(QUIET_MIN + 20),
        last_agent_message_at=_iso(QUIET_MIN + 25),
        last_reply_at=_iso(QUIET_MIN + 10),
    ),
    _conv(
        id="mq-1",
        metadata={"engaged_agent_id": "op-9"},
        last_user_message_at=_iso(2),
        last_agent_message_at=_iso(9),
        last_reply_at=_iso(2),
    ),
    _conv(
        id="act-1",
        mode="human",
        assigned_agent_id="op-9",
        metadata={"engaged_agent_id": "op-9"},
        last_user_message_at=_iso(4),
        last_agent_message_at=_iso(3),
        last_reply_at=_iso(3),
    ),
    _conv(id="empty-1"),  # nothing at all — fresh
]


# ── Python re-expressions of the DB pre-filter predicates ────

def _db_no_engagement(c: dict) -> bool:
    """Mirror of the PostgREST no_engagement pre-filter (exact)."""
    return (
        c.get("last_user_message_at") is None
        and bool(c.get("visitor_name"))
        and bool(c.get("visitor_phone"))
        and bool(c.get("visitor_email"))
    )


def _db_abandoned_candidate(c: dict, cutoff_iso: str) -> bool:
    """Mirror of the quiet_before pre-filter (superset of abandoned)."""
    lu = c.get("last_user_message_at")
    return lu is not None and lu < cutoff_iso and c.get("last_reply_at") is not None


class TestParity:
    def test_no_engagement_predicate_exact_parity(self):
        for c in FIXTURES:
            derived = derive_conversation_tag(c, quiet_minutes=QUIET_MIN, now=NOW)
            assert (derived == "no_engagement") == _db_no_engagement(c), c["id"]

    def test_abandoned_prefilter_is_superset(self):
        cutoff = _iso(QUIET_MIN)
        for c in FIXTURES:
            derived = derive_conversation_tag(c, quiet_minutes=QUIET_MIN, now=NOW)
            if derived == "abandoned":
                assert _db_abandoned_candidate(c, cutoff), (
                    f"{c['id']}: DB pre-filter would exclude a row the "
                    "derivation tags abandoned — rows would go missing"
                )

    def test_exactly_one_tag_per_conversation(self):
        for c in FIXTURES:
            tag = derive_conversation_tag(c, quiet_minutes=QUIET_MIN, now=NOW)
            assert tag in {
                "no_engagement", "completed", "abandoned",
                "fresh", "main_queue", "active",
            }, c["id"]


class TestTagPagingAccuracy:
    """The filter sees the scanned superset, not just the caller's page."""

    @pytest.mark.asyncio
    async def test_count_and_page_come_from_full_scan(self):
        # 120 candidates: every 3rd derives "fresh" (40 total). Old behavior
        # with limit=10 returned at most the page's few and count<=10.
        rows = []
        for i in range(120):
            if i % 3 == 0:
                rows.append(_conv(id=f"f-{i}", last_user_message_at=_iso(5)))
            else:
                # engaged_agent_id top-level: the SELECT lifts it out of
                # metadata and get_conversations folds it back in.
                rows.append(
                    _conv(
                        id=f"a-{i}",
                        mode="human",
                        assigned_agent_id="op-9",
                        engaged_agent_id="op-9",
                        last_user_message_at=_iso(4),
                        last_agent_message_at=_iso(3),
                        last_reply_at=_iso(3),
                    )
                )

        async def fake_run_sync(fn, **kw):
            return SimpleNamespace(data=[dict(r) for r in rows], count=len(rows))

        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", fake_run_sync), \
             patch.object(sb, "_supervisor_columns_available", lambda: True), \
             patch.object(sb, "enrich_conversations_agent_info", _identity), \
             patch.object(sb, "_get_lead_completeness_inputs", _empty_map):
            page, total = await sb.get_conversations(tag="fresh", limit=10, offset=10)

        assert total == 40, f"true filtered count expected, got {total}"
        assert len(page) == 10
        assert all(r["tag"] == "fresh" for r in page)

    @pytest.mark.asyncio
    async def test_scan_overflow_logs_loudly(self, caplog):
        import logging

        rows = [_conv(id=f"f-{i}", last_user_message_at=_iso(5)) for i in range(5)]

        async def fake_run_sync(fn, **kw):
            return SimpleNamespace(data=[dict(r) for r in rows], count=_TAG_SCAN_CAP + 50)

        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", fake_run_sync), \
             patch.object(sb, "_supervisor_columns_available", lambda: True), \
             patch.object(sb, "enrich_conversations_agent_info", _identity), \
             patch.object(sb, "_get_lead_completeness_inputs", _empty_map):
            with caplog.at_level(logging.WARNING, logger="app.db.supabase"):
                await sb.get_conversations(tag="fresh", limit=10, offset=0)

        assert any("scan capped" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_untagged_requests_keep_db_paging(self):
        """No tag → the DB still pages (range = caller's page, count = DB)."""
        rows = [_conv(id=f"r-{i}") for i in range(7)]

        async def fake_run_sync(fn, **kw):
            return SimpleNamespace(data=[dict(r) for r in rows], count=1350)

        with patch.object(sb, "get_client", return_value=MagicMock()), \
             patch.object(sb, "_run_sync", fake_run_sync), \
             patch.object(sb, "_supervisor_columns_available", lambda: True), \
             patch.object(sb, "enrich_conversations_agent_info", _identity), \
             patch.object(sb, "_get_lead_completeness_inputs", _empty_map):
            page, total = await sb.get_conversations(limit=7, offset=0)

        assert total == 1350
        assert len(page) == 7


async def _identity(rows):
    return rows


async def _empty_map(ids):
    return {}
