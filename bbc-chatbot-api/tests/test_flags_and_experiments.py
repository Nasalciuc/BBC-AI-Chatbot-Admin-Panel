"""app/flags.py and app/experiments.py — the two things every later switch relies on."""

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from app import flags
from app.experiments import bucket, in_arm


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    flags._reset_for_tests()
    monkeypatch.delenv("SOME_SWITCH", raising=False)
    monkeypatch.delenv("SOME_PERCENT", raising=False)
    yield
    flags._reset_for_tests()


def _fake_run_sync(result=None, exc=None):
    async def _run(fn, idempotent=True, *, label="other"):
        if exc is not None:
            raise exc
        return result

    return _run


# ── flag(): database → environment → default ──────────────────────────────

def test_default_is_returned_when_nothing_is_set():
    assert flags.flag("SOME_SWITCH", False) is False
    assert flags.flag("SOME_PERCENT", 0) == 0


def test_environment_overrides_default(monkeypatch):
    monkeypatch.setenv("SOME_SWITCH", "true")
    monkeypatch.setenv("SOME_PERCENT", "25")
    assert flags.flag("SOME_SWITCH", False) is True
    assert flags.flag("some_percent", 0) == 25


def test_database_overrides_environment(monkeypatch):
    monkeypatch.setenv("SOME_SWITCH", "true")
    monkeypatch.setattr(
        "app.db.supabase._run_sync",
        _fake_run_sync(SimpleNamespace(data=[{"flags": {"some_switch": False, "SOME_PERCENT": 40}}])),
    )
    assert asyncio.run(flags.refresh()) is True
    assert flags.flag("SOME_SWITCH", False) is False
    assert flags.flag("SOME_PERCENT", 0) == 40
    assert flags.FLAGS_HEALTH["refresh_ok"] == 1


def test_unparseable_value_keeps_the_default(monkeypatch):
    monkeypatch.setenv("SOME_PERCENT", "lots")
    monkeypatch.setenv("SOME_SWITCH", "maybe")
    assert flags.flag("SOME_PERCENT", 7) == 7
    assert flags.flag("SOME_SWITCH", True) is True


def test_missing_column_is_counted_and_falls_back_to_env(monkeypatch, caplog):
    monkeypatch.setenv("SOME_SWITCH", "1")
    err = Exception('column app_settings.flags does not exist (42703)')
    monkeypatch.setattr("app.db.supabase._run_sync", _fake_run_sync(exc=err))
    assert asyncio.run(flags.refresh()) is False
    assert asyncio.run(flags.refresh()) is False
    assert flags.FLAGS_HEALTH["source_missing"] is True
    assert flags.FLAGS_HEALTH["refresh_errors"] == 2
    assert flags.flag("SOME_SWITCH", False) is True
    warnings = [r for r in caplog.records if "not readable" in r.getMessage()]
    assert len(warnings) == 1, "a missing column warns once, not every 30 s"


def test_outage_keeps_the_last_snapshot(monkeypatch):
    monkeypatch.setattr(
        "app.db.supabase._run_sync",
        _fake_run_sync(SimpleNamespace(data=[{"flags": {"SOME_SWITCH": True}}])),
    )
    assert asyncio.run(flags.refresh()) is True
    monkeypatch.setattr("app.db.supabase._run_sync", _fake_run_sync(exc=ConnectionError("reset")))
    assert asyncio.run(flags.refresh()) is False
    assert flags.flag("SOME_SWITCH", False) is True
    assert flags.FLAGS_HEALTH["source_missing"] is False


def test_empty_row_means_no_database_flags(monkeypatch):
    monkeypatch.setenv("SOME_SWITCH", "true")
    monkeypatch.setattr("app.db.supabase._run_sync", _fake_run_sync(SimpleNamespace(data=[])))
    assert asyncio.run(flags.refresh()) is True
    assert flags.flag("SOME_SWITCH", False) is True


def test_a_null_in_the_database_is_no_value(monkeypatch):
    # CodeRabbit #226: null kept the key, flag() returned the default and the
    # environment was skipped.
    monkeypatch.setenv("SOME_SWITCH", "true")
    monkeypatch.setattr("app.db.supabase._run_sync",
                        _fake_run_sync(SimpleNamespace(data=[{"flags": {"SOME_SWITCH": None}}])))
    assert asyncio.run(flags.refresh()) is True
    assert flags.flag("SOME_SWITCH", False) is True and "SOME_SWITCH" not in flags.snapshot()


def test_an_error_with_another_code_is_an_outage_not_a_missing_source(monkeypatch):
    # The code decides when there is one: a permission error naming
    # app_settings is a real fault, said every time, not "migration missing".
    class _ApiError(Exception):
        def __init__(self, code, message):
            super().__init__(message)
            self.code = code

    monkeypatch.setattr("app.db.supabase._run_sync",
                        _fake_run_sync(exc=_ApiError("42501", "permission denied for table app_settings (could not find access)")))
    assert asyncio.run(flags.refresh()) is False
    assert flags.FLAGS_HEALTH["source_missing"] is False
    monkeypatch.setattr("app.db.supabase._run_sync",
                        _fake_run_sync(exc=_ApiError("PGRST205", "Could not find the table 'public.app_settings'")))
    assert asyncio.run(flags.refresh()) is False
    assert flags.FLAGS_HEALTH["source_missing"] is True


# ── bucket(): stable, salted, never hash() ────────────────────────────────

def test_bucket_is_sha256_of_experiment_and_id():
    cid = "6f1c2c1e-0000-4000-8000-000000000001"
    expected = int(hashlib.sha256(f"pipeline_graph:{cid}".encode()).hexdigest(), 16) % 100
    assert bucket("pipeline_graph", cid) == expected
    assert bucket("pipeline_graph", cid) == bucket("pipeline_graph", cid)


def test_experiments_do_not_share_arms():
    ids = [f"conv-{i}" for i in range(2000)]
    a = {c for c in ids if in_arm("pipeline_graph", c, 10)}
    b = {c for c in ids if in_arm("model_family_55", c, 10)}
    assert 120 < len(a) < 280 and 120 < len(b) < 280
    assert len(a & b) < 60, "two 10% arms overlapping fully would confound both"


def test_no_id_is_control():
    assert bucket("x", None) is None
    assert bucket("x", "") is None
    assert in_arm("x", None, 100) is False


def test_percent_bounds():
    assert in_arm("x", "conv-1", 0) is False
    assert in_arm("x", "conv-1", 100) is True
    assert in_arm("x", "conv-1", 250) is True
    assert in_arm("x", "conv-1", -5) is False
