"""Tests for the daily learning loop — cards, merge, budget, auth, injection."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import learning
from config.settings import settings


# ── Fake Supabase ─────────────────────────────────────────────


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.inserts = []
        self.updates = []
        self.deletes = 0


class _FakeQuery:
    """Records the filters used so tests can assert on the query shape."""

    def __init__(self, table, log):
        self._table = table
        self._log = log
        self._op = None
        self._payload = None

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, field, value):
        self._log.append(("eq", field, value))
        return self

    def gte(self, field, value):
        self._log.append(("gte", field, value))
        return self

    def lt(self, field, value):
        self._log.append(("lt", field, value))
        return self

    def in_(self, field, values):
        self._log.append(("in", field, tuple(values)))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def range(self, start, end):
        self._log.append(("range", start, end))
        self._range = (start, end)
        return self

    def execute(self):
        if self._op == "insert":
            self._table.inserts.append(self._payload)
            return _Result([{**self._payload, "id": "new-id"}])
        if self._op == "update":
            self._table.updates.append(self._payload)
            return _Result([{**(self._table.rows[0] if self._table.rows else {}), **self._payload}])
        if self._op == "delete":
            self._table.deletes += 1
            return _Result([])
        rows = list(self._table.rows)
        if getattr(self, "_range", None):
            start, end = self._range
            rows = rows[start:end + 1]
        return _Result(rows)


class _FakeClient:
    def __init__(self, **tables):
        self.tables = {name: _FakeTable(rows) for name, rows in tables.items()}
        self.log = []

    def table(self, name):
        return _FakeQuery(self.tables.setdefault(name, _FakeTable()), self.log)


@pytest.fixture
def fake_db():
    """Patch the supabase client + thread hop out of the learning service."""
    client = _FakeClient()

    async def _run_sync(fn):
        return fn()

    with (
        patch.object(learning.db, "get_client", lambda: client),
        patch.object(learning.db, "_run_sync", _run_sync),
    ):
        yield client


# ── 1. Cards ──────────────────────────────────────────────────


def _conv(**over):
    base = {
        "id": "c-1",
        "chat_number": 1042,
        "tunnel": "sales",
        "status": "closed",
        "mode": "ai",
        "created_at": "2026-07-01T10:00:00+00:00",
        "closed_at": "2026-07-01T10:30:00+00:00",
        "updated_at": "2026-07-01T10:30:00+00:00",
        "metadata": {"utm_source": "kayak"},
        "visitor_name": "Jonathan Meyer",
        "visitor_phone": "+1 202 555 0100",
        "visitor_email": "jm@example.com",
        "last_user_message_at": "2026-07-01T10:05:00+00:00",
        "last_agent_message_at": None,
        "last_reply_at": "2026-07-01T10:06:00+00:00",
    }
    base.update(over)
    return base


def _msgs(*pairs):
    return [{"role": role, "content": text, "created_at": "2026-07-01T10:00:00+00:00"}
            for role, text in pairs]


class TestCards:
    def test_pii_is_masked(self):
        card = learning.build_conversation_card(
            _conv(),
            _msgs(("user", "Hi, I'm Jonathan Meyer, jm@example.com, call +1 202 555 0100")),
        )
        blob = json.dumps(card)
        assert "jm@example.com" not in blob
        assert "202 555 0100" not in blob
        assert "Jonathan Meyer" not in blob
        assert "[email]" in blob and "[phone]" in blob and "[name]" in blob

    def test_messages_are_truncated(self):
        card = learning.build_conversation_card(_conv(), _msgs(("user", "x" * 900)))
        assert all(len(line["text"]) <= learning.MESSAGE_TRUNCATE for line in card["excerpt"])

    def test_abandoned_card_shows_the_drop_off(self):
        conv = _conv(
            status="active",
            assigned_agent_id="a-1",
            last_agent_message_at="2026-07-01T10:06:00+00:00",
            last_user_message_at=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
            last_reply_at=(datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(),
            visitor_email=None,
        )
        messages = _msgs(*[("user" if i % 2 == 0 else "ai", f"msg {i}") for i in range(12)])
        card = learning.build_conversation_card(conv, messages)
        assert card["tag"] == "abandoned"
        assert [line["text"] for line in card["excerpt"]] == [f"msg {i}" for i in range(6, 12)]

    def test_no_engagement_card_shows_the_opening(self):
        conv = _conv(last_user_message_at=None, last_reply_at=None)
        card = learning.build_conversation_card(
            conv, _msgs(("ai", "greeting"), ("ai", "second"), ("ai", "third"), ("ai", "fourth"))
        )
        assert card["tag"] == "no_engagement"
        assert [line["text"] for line in card["excerpt"]] == ["greeting", "second", "third"]

    def test_segment_combines_tunnel_and_source(self):
        card = learning.build_conversation_card(_conv(), _msgs(("user", "hi")))
        assert card["segment"] == "sales/kayak"
        assert learning.build_conversation_card(
            _conv(metadata={}), _msgs(("user", "hi"))
        )["segment"] == "sales/direct"

    def test_tag_distribution_counts_by_tag_and_segment(self):
        cards = [
            {"tag": "completed", "segment": "sales/kayak"},
            {"tag": "abandoned", "segment": "sales/kayak"},
            {"tag": "abandoned", "segment": "support/direct"},
        ]
        dist = learning.tag_distribution(cards)
        assert dist["total"] == 3
        assert dist["tags"] == {"completed": 1, "abandoned": 2}
        assert dist["segments"]["sales/kayak"] == {"completed": 1, "abandoned": 1}


# ── 2. Merge ──────────────────────────────────────────────────


@pytest.mark.asyncio
class TestMerge:
    async def test_new_lesson_inserts_as_proposed(self, fake_db):
        new, reinforced = await learning.merge_lessons(
            [{"action": "new", "kind": "killer_pattern", "title": "asks phone too early",
              "content": "Say the private-fare insight before asking for a phone.",
              "evidence_count": 12, "denominator": 40, "dominant_segment": "sales/kayak",
              "cards": ["1042"]}],
            existing=[],
            cards_by_id={"1042": {"chat": "1042", "tag": "abandoned"}},
            run_day=date(2026, 7, 29),
        )
        row = fake_db.tables["chatbot_lessons"].inserts[0]
        assert (new, reinforced) == (1, 0)
        assert row["status"] == "proposed"
        assert row["evidence_count"] == 12 and row["denominator"] == 40
        assert row["first_seen_run"] == row["last_seen_run"] == "2026-07-29"
        assert row["sample_cards"] == [{"chat": "1042", "tag": "abandoned"}]

    async def test_reinforce_adds_evidence_and_keeps_status(self, fake_db):
        existing = [{"id": "l-1", "kind": "killer_pattern", "title": "t",
                     "status": "approved", "evidence_count": 10}]
        new, reinforced = await learning.merge_lessons(
            [{"action": "reinforces", "lesson_id": "l-1", "kind": "killer_pattern",
              "evidence_count": 5}],
            existing=existing, cards_by_id={}, run_day=date(2026, 7, 29),
        )
        update = fake_db.tables["chatbot_lessons"].updates[0]
        assert (new, reinforced) == (0, 1)
        assert update["evidence_count"] == 15
        assert update["last_seen_run"] == "2026-07-29"
        assert "status" not in update

    async def test_contradiction_links_the_lesson_it_argues_with(self, fake_db):
        await learning.merge_lessons(
            [{"action": "contradicts", "lesson_id": "l-1", "kind": "winning_pattern",
              "title": "phone first works on returning clients",
              "content": "For returning clients, ask for the phone in the first reply."}],
            existing=[{"id": "l-1", "kind": "killer_pattern", "title": "t", "status": "approved"}],
            cards_by_id={}, run_day=date(2026, 7, 29),
        )
        row = fake_db.tables["chatbot_lessons"].inserts[0]
        assert row["contradicts_lesson_id"] == "l-1"
        assert row["status"] == "proposed"

    async def test_pii_and_junk_entries_are_dropped(self, fake_db):
        await learning.merge_lessons(
            [
                {"action": "new", "kind": "not_a_kind", "title": "x", "content": "y"},
                {"action": "new", "kind": "few_shot", "title": "", "content": "y"},
                {"action": "new", "kind": "few_shot", "title": "ok",
                 "content": "Client wrote from jm@example.com — mirror the occasion."},
            ],
            existing=[], cards_by_id={}, run_day=date(2026, 7, 29),
        )
        inserts = fake_db.tables["chatbot_lessons"].inserts
        assert len(inserts) == 1
        assert "jm@example.com" not in inserts[0]["content"]


# ── 3. The run: budget, idempotency, windows ──────────────────


def _run_patches(cards, llm, **extra):
    base = {
        "collect_cards": AsyncMock(return_value=cards),
        "get_lessons": AsyncMock(return_value=[]),
        "_save_run": AsyncMock(),
        "_recent_runs": AsyncMock(return_value=[]),
        "_prune_runs": AsyncMock(),
        "_run_exists_today": AsyncMock(return_value=False),
        "call_sonnet_learning": llm,
    }
    base.update(extra)
    return [patch.object(learning, name, value) for name, value in base.items()]


@pytest.mark.asyncio
class TestRun:
    async def test_budget_cap_stops_the_run_and_saves_partial(self):
        cards = [{"chat": i, "tag": "abandoned", "segment": "sales/direct"} for i in range(90)]
        llm = lambda *_a, **_k: ('{"killer_patterns":[],"winning_patterns":[]}', 4.0)
        save = AsyncMock()
        patches = _run_patches(cards, llm, _save_run=save)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            with patch.object(settings, "learning_run_budget", 5.0):
                result = await learning.run_learning()

        assert result["status"] == "partial_budget"
        assert result["cost"] == 8.0  # two chunks at $4, then the cap bites
        assert save.await_args.args[0]["status"] == "partial_budget"

    async def test_invalid_json_marks_partial_and_keeps_going(self):
        cards = [{"chat": 1, "tag": "abandoned", "segment": "sales/direct"}]
        llm = lambda *_a, **_k: ("not json at all", 0.01)
        patches = _run_patches(cards, llm)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await learning.run_learning()

        assert result["status"] == "partial_json"
        assert result["new_lessons"] == 0

    async def test_json_retry_recovers(self):
        calls = {"n": 0}

        def llm(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                return "```oops```", 0.01
            return '{"killer_patterns":[],"winning_patterns":[]}', 0.01

        cards = [{"chat": 1, "tag": "abandoned", "segment": "sales/direct"}]
        patches = _run_patches(cards, llm)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await learning.run_learning()

        assert calls["n"] >= 2
        assert result["status"] == "ok"

    async def test_second_run_same_day_is_skipped(self):
        with patch.object(learning, "_run_exists_today", AsyncMock(return_value=True)):
            result = await learning.run_learning()
        assert result["skipped"] == "already_ran"

    async def test_bootstrap_ignores_the_daily_guard(self):
        patches = _run_patches([], lambda *_a, **_k: ("{}", 0.0),
                               _run_exists_today=AsyncMock(return_value=True))
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await learning.run_learning(bootstrap=True)
        assert result.get("skipped") is None
        assert result["bootstrap"] is True

    async def test_failure_is_recorded_not_raised(self):
        save = AsyncMock()
        with (
            patch.object(learning, "collect_cards", AsyncMock(side_effect=RuntimeError("boom"))),
            patch.object(learning, "_run_exists_today", AsyncMock(return_value=False)),
            patch.object(learning, "_save_run", save),
        ):
            result = await learning.run_learning()
        assert result["status"] == "error"
        assert save.await_args.args[0]["status"] == "error"


@pytest.mark.asyncio
class TestWindowSelection:
    async def test_bootstrap_reads_every_closed_conversation(self, fake_db):
        await learning._fetch_conversation_page(bootstrap=True, offset=0)
        assert ("eq", "status", "closed") in fake_db.log
        assert not [entry for entry in fake_db.log if entry[0] == "gte"]

    async def test_daily_reads_the_last_24h(self, fake_db):
        await learning._fetch_conversation_page(bootstrap=False, offset=0)
        gte = [entry for entry in fake_db.log if entry[0] == "gte"]
        assert gte and gte[0][1] == "updated_at"
        cutoff = datetime.fromisoformat(gte[0][2])
        assert timedelta(hours=23) < datetime.now(timezone.utc) - cutoff < timedelta(hours=25)
        assert ("eq", "status", "closed") not in fake_db.log

    async def test_conversations_without_messages_are_skipped(self, fake_db):
        fake_db.tables["conversations"] = _FakeTable([_conv(id="c-1"), _conv(id="c-2")])
        with (
            patch.object(learning, "_fetch_messages",
                         AsyncMock(return_value={"c-1": _msgs(("user", "hello"))})),
            patch.object(learning, "_fetch_leads", AsyncMock(return_value={})),
        ):
            cards = await learning.collect_cards(bootstrap=True)
        assert len(cards) == 1


# ── 4. Rolling window + regression watchdog ───────────────────


class TestRegression:
    def _runs(self, current, prior):
        today = date(2026, 7, 29)
        rows = []
        for offset, dist in ((1, current), (8, prior)):
            rows.append({
                "run_date": (today - timedelta(days=offset)).isoformat(),
                "tag_distribution": {"tags": dist},
            })
        return rows, today

    def test_rolling_summary_splits_the_two_weeks(self):
        rows, today = self._runs({"abandoned": 40, "completed": 60}, {"abandoned": 20, "completed": 80})
        summary = learning.rolling_summary(rows, today)
        assert summary["abandoned_share_7d"] == pytest.approx(0.4)
        assert summary["abandoned_share_prior_7d"] == pytest.approx(0.2)

    def test_regression_fires_above_the_threshold(self):
        rows, today = self._runs({"abandoned": 40, "completed": 60}, {"abandoned": 20, "completed": 80})
        assert learning.regression_detected(learning.rolling_summary(rows, today)) is True

    def test_no_alert_on_a_small_move(self):
        rows, today = self._runs({"abandoned": 22, "completed": 78}, {"abandoned": 20, "completed": 80})
        assert learning.regression_detected(learning.rolling_summary(rows, today)) is False

    def test_no_history_no_alert(self):
        assert learning.regression_detected({"abandoned_share_7d": 0.5}) is False


# ── 5. Cron auth ──────────────────────────────────────────────


def _cron_client():
    from app.api.cron import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestCronAuth:
    def test_no_token_is_rejected(self):
        with patch.object(settings, "cron_secret", "s3cret"):
            assert _cron_client().post("/api/cron/daily-learning").status_code == 401

    def test_wrong_token_is_rejected(self):
        with patch.object(settings, "cron_secret", "s3cret"):
            resp = _cron_client().post(
                "/api/cron/daily-learning", headers={"Authorization": "Bearer nope"}
            )
        assert resp.status_code == 401

    def test_unconfigured_secret_is_503(self):
        with patch.object(settings, "cron_secret", ""):
            assert _cron_client().post("/api/cron/daily-learning").status_code == 503

    def test_correct_token_runs_the_loop(self):
        run = AsyncMock(return_value={"status": "ok"})
        with (
            patch.object(settings, "cron_secret", "s3cret"),
            patch.object(learning, "run_learning", run),
        ):
            resp = _cron_client().post(
                "/api/cron/daily-learning", headers={"Authorization": "Bearer s3cret"}
            )
        assert resp.status_code == 200 and resp.json() == {"status": "ok"}
        assert run.await_args.kwargs == {"bootstrap": False}

    def test_bootstrap_flag_is_forwarded(self):
        run = AsyncMock(return_value={"status": "ok"})
        with (
            patch.object(settings, "cron_secret", "s3cret"),
            patch.object(learning, "run_learning", run),
        ):
            _cron_client().post(
                "/api/cron/daily-learning?bootstrap=true",
                headers={"Authorization": "Bearer s3cret"},
            )
        assert run.await_args.kwargs == {"bootstrap": True}


# ── 6. Prompt injection ───────────────────────────────────────


def _lesson(n, content="Lead with the private-fare insight before asking for contact."):
    return {"kind": "killer_pattern", "title": f"t{n}", "content": f"{content} ({n})",
            "evidence_count": 50 - n, "denominator": 100}


class TestInjection:
    def setup_method(self):
        learning.invalidate_lesson_cache()

    def test_absent_when_nothing_is_approved(self):
        assert learning.render_lessons_section([]) is None

    def test_renders_evidence_as_a_rate(self):
        section = learning.render_lessons_section([_lesson(1)])
        assert "LEARNED FROM REAL CONVERSATIONS" in section
        assert "(evidence: 49/100 chats)" in section

    def test_capped_at_eight_lessons(self):
        section = learning.render_lessons_section([_lesson(i) for i in range(20)])
        assert len([ln for ln in section.splitlines() if ln.startswith("- ")]) == 8

    def test_capped_by_size(self):
        section = learning.render_lessons_section([_lesson(i, "x" * 600) for i in range(8)])
        assert len(section) <= learning.MAX_LESSON_SECTION_CHARS

    def test_query_asks_for_approved_and_fresh_only(self, fake_db):
        learning._fetch_approved_lessons()
        assert ("eq", "status", "approved") in fake_db.log
        gte = [entry for entry in fake_db.log if entry[0] == "gte"]
        assert gte and gte[0][1] == "last_seen_run"
        cutoff = date.fromisoformat(gte[0][2])
        assert (date.today() - cutoff).days == settings.lesson_staleness_days

    def test_cache_avoids_a_query_per_turn(self):
        with patch.object(learning, "_fetch_approved_lessons", return_value=[_lesson(1)]) as fetch:
            learning.approved_lessons()
            learning.approved_lessons()
        assert fetch.call_count == 1

    def test_empty_and_failed_results_are_cached_too(self):
        with patch.object(learning, "_fetch_approved_lessons", return_value=[]) as fetch:
            learning.approved_lessons()
            learning.approved_lessons()
        assert fetch.call_count == 1

        learning.invalidate_lesson_cache()
        with patch.object(learning, "_fetch_approved_lessons", side_effect=RuntimeError("down")) as fetch:
            learning.approved_lessons()
            learning.approved_lessons()
        assert fetch.call_count == 1

    def test_db_failure_never_breaks_the_prompt(self):
        with patch.object(learning, "_fetch_approved_lessons", side_effect=RuntimeError("down")):
            assert learning.approved_lessons() == []

    def test_lessons_land_in_the_static_prompt(self):
        from app.ai.prompts import build_conversational_prompt
        from app.models.chat import VisitorInfo

        with patch.object(learning, "approved_lessons", return_value=[_lesson(1)]):
            static, dynamic = build_conversational_prompt(tunnel="sales", visitor=VisitorInfo())
        assert "LEARNED FROM REAL CONVERSATIONS" in static
        assert "LEARNED FROM REAL CONVERSATIONS" not in dynamic

    def test_prompt_is_unchanged_without_approved_lessons(self):
        from app.ai.prompts import build_conversational_prompt
        from app.models.chat import VisitorInfo

        with patch.object(learning, "approved_lessons", return_value=[]):
            static, _ = build_conversational_prompt(tunnel="sales", visitor=VisitorInfo())
        assert "LEARNED FROM REAL CONVERSATIONS" not in static


# ── 7. Approval endpoints ─────────────────────────────────────


def _lessons_client(role: str):
    from app.api.lessons import router
    from app.security.auth import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u-1", "email": f"{role}@bbc.com", "role": role,
    }
    return TestClient(app)


class TestApprovalEndpoints:
    @pytest.mark.parametrize("role", ["sales", "support", "qa", "supervisor"])
    def test_non_admins_cannot_review(self, role):
        assert _lessons_client(role).get("/api/admin/lessons").status_code == 403

    def test_contradictions_come_first(self):
        rows = [
            {"id": "a", "evidence_count": 90, "contradicts_lesson_id": None},
            {"id": "b", "evidence_count": 3, "contradicts_lesson_id": "a"},
        ]
        with patch("app.api.lessons.get_lessons", AsyncMock(return_value=rows)):
            body = _lessons_client("owner").get("/api/admin/lessons").json()
        assert [lesson["id"] for lesson in body["lessons"]] == ["b", "a"]

    def test_approve_updates_and_clears_the_cache(self):
        with (
            patch("app.api.lessons.set_lesson_status",
                  AsyncMock(return_value={"id": "l-1", "status": "approved"})),
            patch("app.api.lessons.invalidate_lesson_cache") as bust,
        ):
            resp = _lessons_client("admin").patch(
                "/api/admin/lessons/l-1", json={"status": "approved"}
            )
        assert resp.status_code == 200 and resp.json()["status"] == "approved"
        assert bust.called

    def test_unknown_lesson_is_404(self):
        with patch("app.api.lessons.set_lesson_status", AsyncMock(return_value=None)):
            resp = _lessons_client("owner").patch(
                "/api/admin/lessons/nope", json={"status": "retired"}
            )
        assert resp.status_code == 404

    def test_invalid_status_is_rejected(self):
        resp = _lessons_client("owner").patch(
            "/api/admin/lessons/l-1", json={"status": "published"}
        )
        assert resp.status_code == 400
