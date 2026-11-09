"""Repeated lines, the safety valve, and the day's count (app/log_setup.py).

9 Nov: one refused lead was the same ERROR every 5 minutes for days; transport
storms wrote hundreds of identical lines a second, and Railway — whose cap is
500 lines a second — dropped 75,211 lines in a week, in the minutes of the
incidents. The first line of a kind is written; the same line again is held
back, counted, and said once ("+N similar, M distinct ids") with backoff.
"""

import io
import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import log_setup
from app.log_setup import (
    GROUP_FIRST_WINDOW_S,
    GROUP_MAX_WINDOW_S,
    GROUP_RESET_AFTER_S,
    EventRegistry,
    GroupingFilter,
    JsonFormatter,
    RateCapFilter,
)

ID_A = "ca7f016a-2ccd-411f-9357-d1b5a65d63c1"
ID_B = "93a5c0a1-1b47-4eb5-a9c9-d86abf342765"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _rec(msg="[cron][%s] CRM fail: gate:email_typo_domain", args=(ID_A,), level=logging.ERROR,
         name="app.api.cron", line=441):
    return logging.LogRecord(name, level, "/app/app/api/cron.py", line, msg, args, None, func="run_abandoned_crm")


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def grouping(clock, monkeypatch):
    monkeypatch.delenv("LOG_GROUPING", raising=False)
    return GroupingFilter(EventRegistry(), clock=clock)


def test_the_first_line_passes_and_the_same_line_again_is_held_back(grouping):
    assert grouping.filter(_rec()) is True
    assert [grouping.filter(_rec()) for _ in range(50)] == [False] * 50
    assert grouping.held_total == 50


def test_after_the_window_the_next_one_says_how_many(grouping, clock):
    grouping.filter(_rec())
    for _ in range(3):
        grouping.filter(_rec())
    grouping.filter(_rec(args=(ID_B,)))
    clock.now += GROUP_FIRST_WINDOW_S
    rec = _rec()
    assert grouping.filter(rec) is True
    assert rec.repeat_count == 4 and rec.distinct_ids == 2
    assert rec.getMessage().endswith("[+4 similar in the last 5 min, 2 distinct ids]")


def test_the_summary_comes_out_on_flush_even_when_the_storm_stopped(grouping, clock, caplog):
    grouping.filter(_rec())
    for _ in range(7):
        grouping.filter(_rec())
    assert grouping.flush() == 0, "the window is still open"
    clock.now += GROUP_FIRST_WINDOW_S
    with caplog.at_level(logging.INFO, logger="app.api.cron"):
        assert grouping.flush() == 1
    (summary,) = [r for r in caplog.records if "similar" in r.getMessage()]
    assert summary.levelno == logging.ERROR, "the summary keeps the level of what it counts"
    assert summary.repeat_count == 7 and summary.distinct_ids == 1
    assert summary.event == "app.api.cron.run_abandoned_crm"
    assert grouping.filter(summary) is True, "a summary is never held back itself"


def test_force_flush_at_shutdown_writes_every_pending_count(grouping, caplog):
    grouping.filter(_rec())
    grouping.filter(_rec())
    with caplog.at_level(logging.INFO, logger="app.api.cron"):
        assert grouping.flush(force=True) == 1


def test_while_it_keeps_coming_the_summaries_back_off_to_once_an_hour(grouping, clock):
    grouping.filter(_rec())
    seen = []
    last = clock.now
    for _ in range(int(4 * 3600 / 60)):  # one line a minute for four hours
        clock.now += 60
        if grouping.filter(_rec()):
            seen.append(round(clock.now - last))
            last = clock.now
    assert seen[:5] == [300, 600, 1200, 2400, 3600]
    assert all(gap == GROUP_MAX_WINDOW_S for gap in seen[4:])


def test_after_an_hour_of_silence_the_next_one_is_new_again(grouping, clock):
    grouping.filter(_rec())
    grouping.filter(_rec())
    clock.now += GROUP_RESET_AFTER_S + 1
    rec = _rec()
    assert grouping.filter(rec) is True
    assert not hasattr(rec, "repeat_count")


def test_a_different_kind_of_line_is_not_held_back(grouping):
    grouping.filter(_rec())
    assert grouping.filter(_rec(msg="get_conversation_counts error: Server disconnected", args=())) is True
    assert grouping.filter(_rec(line=999)) is True, "another call site is another kind"


def test_security_lines_and_critical_ones_are_never_grouped(grouping):
    for name in ("app.security.ban_guard", "app.security.decoys"):
        assert all(grouping.filter(_rec(name=name)) for _ in range(10))
    assert all(grouping.filter(_rec(level=logging.CRITICAL)) for _ in range(10))


def test_log_grouping_false_turns_it_off(grouping, monkeypatch):
    monkeypatch.setenv("LOG_GROUPING", "false")
    assert all(grouping.filter(_rec()) for _ in range(10))


def test_the_day_counts_every_warning_and_error_held_back_or_not(grouping):
    for _ in range(30):
        grouping.filter(_rec())
    grouping.filter(_rec(level=logging.WARNING, msg="[db-retry] dead connection (label=%s)", args=("list",), line=209,
                         name="app.db.supabase"))
    grouping.filter(_rec(level=logging.INFO, msg="ordinary", args=()))
    snap = grouping.registry.snapshot()
    assert snap["errors"] == 30 and snap["warnings"] == 1
    assert snap["top"][0]["count"] == 30 and snap["top"][0]["level"] == "ERROR"
    assert ID_A[:8] in snap["top"][0]["sample"]


def test_a_new_day_starts_from_zero():
    day = {"v": "2026-11-09"}
    reg = EventRegistry(today=lambda: day["v"])
    reg.note(_rec(), "x")
    day["v"] = "2026-11-10"
    reg.note(_rec(), "x")
    snap = reg.snapshot()
    assert snap["day"] == "2026-11-10" and snap["errors"] == 1


# ── the safety valve ─────────────────────────────────────────────────────


def test_the_valve_holds_back_info_and_warnings_over_the_cap(clock, caplog):
    cap = RateCapFilter(per_second=1, burst=3, clock=clock)
    info = [cap.filter(_rec(level=logging.INFO)) for _ in range(5)]
    assert info == [True, True, True, False, False]
    assert cap.filter(_rec(level=logging.ERROR)) is True, "an ERROR always passes"
    assert cap.filter(_rec(level=logging.WARNING, name="app.security.ban_guard")) is True
    clock.now += 1
    assert cap.filter(_rec(level=logging.INFO)) is True, "it refills"
    with caplog.at_level(logging.WARNING, logger="app.log_setup"):
        assert cap.flush() == 2
    assert any("safety valve held back 2 lines" in r.getMessage() for r in caplog.records)


# ── end to end: what reaches the stream ──────────────────────────────────


def test_a_storm_of_a_thousand_identical_lines_writes_two(clock, monkeypatch):
    monkeypatch.delenv("LOG_GROUPING", raising=False)
    grouping = GroupingFilter(EventRegistry(), clock=clock)
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(grouping)
    handler.addFilter(RateCapFilter(clock=clock))
    log = logging.getLogger("app.db.supabase")
    log.addHandler(handler)
    try:
        for i in range(1000):
            rec = _rec(level=logging.WARNING, name="app.db.supabase", line=209,
                       msg="[db-retry] dead connection (RemoteProtocolError: Server disconnected) — retrying once (label=%s)",
                       args=("list",))
            handler.handle(rec)
        clock.now += GROUP_FIRST_WINDOW_S
        grouping.flush()
    finally:
        log.removeHandler(handler)
    lines = [json.loads(l) for l in buf.getvalue().splitlines()]
    assert len(lines) == 2
    assert lines[1]["repeat_count"] == 999 and lines[1]["level"] == "warn"


def test_the_production_handler_runs_id_then_grouping_then_valve():
    handler = log_setup._build_handler("json")
    kinds = [type(f).__name__ for f in handler.filters]
    assert kinds == ["RequestIdFilter", "GroupingFilter", "RateCapFilter"]


def test_health_shows_the_days_count():
    snap = log_setup.log_health_snapshot()
    assert set(snap) >= {"format", "grouping", "held_back_total", "capped_total", "today"}
    assert set(snap["today"]) >= {"day", "warnings", "errors", "top"}


def test_a_malformed_log_call_never_raises_out_of_the_filter(grouping):
    """A filter that raised would crash the caller's logger.info(); the record
    goes on to emit(), which reports a bad format string as it always did."""
    bad = _rec(msg="%s and %s", args=("only one",))
    assert grouping.filter(bad) is True
