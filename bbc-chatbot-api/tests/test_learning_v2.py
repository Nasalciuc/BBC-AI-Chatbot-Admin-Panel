"""PR-8 — the self-learning loop: bandit, tag, control group, reward resolve."""

import os
import random
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.learning import bandit
from app.learning.bandit import VariantStats
from app.learning import outcomes


@pytest.fixture(autouse=True)
def _clean():
    flags._reset_for_tests()
    yield
    flags._reset_for_tests()


# ── bandit ──────────────────────────────────────────────────────────────

def test_choose_prefers_the_stronger_variant():
    rng = random.Random(7)
    vs = [VariantStats("a", answered=80, not_answered=20), VariantStats("b", answered=5, not_answered=95)]
    picks = [bandit.choose(vs, segment_shows=200, rng=rng) for _ in range(200)]
    assert picks.count("a") > picks.count("b") * 3


def test_choose_caps_at_four_active():
    vs = [VariantStats(str(i)) for i in range(9)]
    # No crash, returns one of the first four.
    assert bandit.choose(vs, rng=random.Random(1)) in {"0", "1", "2", "3"}


def test_thin_segment_falls_back_to_global():
    rng = random.Random(3)
    vs = [VariantStats("a", 0, 0), VariantStats("b", 0, 0)]
    g = {"a": {"answered": 90, "not_answered": 10}, "b": {"answered": 2, "not_answered": 98}}
    picks = [bandit.choose(vs, segment_shows=5, global_stats=g, rng=rng) for _ in range(200)]
    assert picks.count("a") > picks.count("b")


def test_prob_best_sums_to_one():
    vs = [VariantStats("a", 30, 10), VariantStats("b", 10, 30)]
    probs = bandit.prob_best(vs, samples=2000, rng=random.Random(1))
    assert abs(sum(probs.values()) - 1.0) < 1e-6
    assert probs["a"] > probs["b"]


def test_retire_only_after_min_shows_and_never_the_best():
    vs = [VariantStats("win", 180, 60), VariantStats("lose", 1, 260)]
    retire = bandit.to_retire(vs, rng=random.Random(1))
    assert "lose" in retire and "win" not in retire
    # Below RETIRE_MIN_SHOWS → never retired.
    vs2 = [VariantStats("win", 80, 20), VariantStats("lose", 0, 50)]
    assert bandit.to_retire(vs2, rng=random.Random(1)) == []


# ── control group ─────────────────────────────────────────────────────────

def test_control_group_is_about_10_percent():
    ids = [f"conv-{i}" for i in range(3000)]
    ctrl = [c for c in ids if outcomes.is_control(c)]
    assert 200 < len(ctrl) < 450


def test_no_conversation_id_is_not_control():
    assert outcomes.is_control(None) is False


# ── learning_tag ──────────────────────────────────────────────────────────

def test_learning_tag_complete_and_partial():
    from app.services.learning import learning_tag

    full = {"origin_code": "JFK", "destination_code": "LHR", "departure_date": "2026-06-01",
            "trip_type": "one_way", "trip_notes": {"trip_type_source": "client"}, "passengers": 2}
    assert learning_tag({"message_count": 4}, full) == "complete"
    thin = {"origin_code": "JFK", "destination_code": "LHR"}
    assert learning_tag({"message_count": 4}, thin).startswith("partial:")
    assert learning_tag({"message_count": 1}, thin) == "no_engagement"


def test_learning_tag_lost_after_question():
    from app.services.learning import learning_tag

    thin = {"origin_code": "JFK", "destination_code": "LHR"}
    tag = learning_tag({"message_count": 5}, thin, outcomes=[{"slot": "departure_date", "answered": False}])
    assert tag == "lost_after_question:departure_date"


# ── reward resolve ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_sets_answered_from_lead():
    open_rows = [{"id": "o1", "slot": "route"}, {"id": "o2", "slot": "passengers"}]
    lead = {"origin_code": "JFK", "destination_code": "LHR"}  # route filled, pax not
    updates = []

    class _Q:
        def __init__(self): self._f = {}
        def select(self, *a): return self
        def eq(self, *a): return self
        def is_(self, *a): return self
        def update(self, payload): updates.append(payload); return self
        def execute(self): return type("R", (), {"data": open_rows})()

    class _Client:
        def table(self, *a): return _Q()

    async def _run(fn, idempotent=True, *, label="other"):
        return fn()

    with patch("app.db.supabase.get_client", lambda: _Client()), \
         patch("app.db.supabase._run_sync", _run):
        n = await outcomes.resolve_outcomes("conv-x", lead)
    assert n == 2
    # route resolved True, passengers False
    assert {"answered": True} in updates and {"answered": False} in updates
