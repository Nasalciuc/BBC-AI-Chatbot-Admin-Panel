"""Past-date guard (C1–C5) — frozen at 2026-07-20.

Covers the shared future-date rule on both extraction paths:
  - regex path:  _coerce_future_iso / _make_date (entity_extractor)
  - Claude path: _validate_future_date (lead_service) re-validates tool output
"""

from freezegun import freeze_time

from app.pipeline.entity_extractor import (
    _coerce_future_iso,
    _make_date,
    _resolve_year,
    extract_entities,
)
from app.services.lead_service import _validate_future_date


@freeze_time("2026-07-20")
class TestCoerceFutureIso:
    def test_resolve_year_compares_month_and_day(self):
        # (month, day) earlier than today → next year.
        assert _resolve_year(3, 15) == 2027
        assert _resolve_year(7, 19) == 2027   # same month, earlier day
        assert _resolve_year(7, 20) == 2026   # today
        assert _resolve_year(12, 10) == 2026  # later this year

    def test_inferred_past_month_bumps_to_next_year(self):
        assert _coerce_future_iso(3, 15, None) == "2027-03-15"

    def test_inferred_future_month_stays_this_year(self):
        assert _coerce_future_iso(12, 10, None) == "2026-12-10"

    def test_explicit_past_year_current_year_bumps(self):
        # 2026 given but March already passed → next occurrence.
        assert _coerce_future_iso(3, 1, 2026) == "2027-03-01"

    def test_explicit_future_same_year_kept(self):
        assert _coerce_future_iso(8, 15, 2026) == "2026-08-15"

    def test_explicit_stale_year_dropped(self):
        # 2+ years old / clearly stale → do not guess.
        assert _coerce_future_iso(3, 1, 2024) is None

    def test_invalid_calendar_date_none(self):
        assert _coerce_future_iso(2, 30, None) is None

    def test_make_date_delegates(self):
        assert _make_date(3, 15) == "2027-03-15"
        assert _make_date(2, 30) is None


@freeze_time("2026-07-20")
class TestScreenshotBugs:
    """The exact values from the bug screenshots must never resolve to the past."""

    def test_march_1_2026_becomes_2027(self):
        e = extract_entities("Departure March 1, 2026")
        assert e.departure_date == "2027-03-01"

    def test_march_13_to_20_both_next_year(self):
        e = extract_entities("March 13-20")
        assert e.departure_date == "2027-03-13"
        assert e.return_date == "2027-03-20"

    def test_march_1_2024_dropped(self):
        e = extract_entities("March 1, 2024")
        assert e.departure_date is None


@freeze_time("2026-07-20")
class TestValidateFutureDate:
    """Final gate for the Claude tool path (returns date strings directly)."""

    def test_future_date_kept(self):
        assert _validate_future_date("2026-08-15") == "2026-08-15"

    def test_today_kept(self):
        assert _validate_future_date("2026-07-20") == "2026-07-20"

    def test_past_date_corrected_forward(self):
        # The screenshot bug via the Claude path.
        assert _validate_future_date("2026-03-13") == "2027-03-13"

    def test_old_past_date_also_corrected_forward(self):
        # The final validator has no "explicitness" context, so it always
        # corrects a past date forward (never leaves a lead with a past date).
        # The stale-year DROP rule lives in the extraction layer (_coerce_future_iso).
        assert _validate_future_date("2024-03-01") == "2027-03-01"

    def test_none_and_garbage_safe(self):
        assert _validate_future_date(None) is None
        assert _validate_future_date("not-a-date") is None
        assert _validate_future_date("") is None

    def test_never_raises_on_bad_input(self):
        # Dropping a bad date must never raise (must not block lead creation).
        for bad in [None, "", "2026-13-40", "garbage", "2020-02-30"]:
            _validate_future_date(bad)  # no exception
