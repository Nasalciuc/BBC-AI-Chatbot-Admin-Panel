"""Thompson sampling over question variants (PR-8).

For each slot (and traffic segment) several phrasings of the question compete.
Each gets a Beta(1+answered, 1+not_answered) posterior; to pick one we draw a
sample from every active variant's posterior and take the max — so a variant is
shown in proportion to the probability it is actually the best, which both
exploits the current winner and keeps exploring the rest.

- Fewer than MIN_SEGMENT shows on a (slot, segment) → fall back to the global
  (slot) stats, so a thin segment borrows the whole population's evidence.
- At most MAX_ACTIVE variants compete per slot.
- A variant whose probability of being best stays under RETIRE_P after
  RETIRE_MIN_SHOWS is retired (the caller persists that + alerts ops).

Pure and deterministic under a seeded RNG, so it is fully testable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional, Sequence

MIN_SEGMENT = 30
MAX_ACTIVE = 4
RETIRE_P = 0.02
RETIRE_MIN_SHOWS = 200


@dataclass
class VariantStats:
    variant_id: str
    answered: int = 0
    not_answered: int = 0

    @property
    def shows(self) -> int:
        return self.answered + self.not_answered


def _beta_sample(rng: random.Random, a: int, b: int) -> float:
    return rng.betavariate(1 + a, 1 + b)


def choose(
    variants: Sequence[VariantStats],
    *,
    segment_shows: int = 0,
    global_stats: Optional[dict] = None,
    rng: Optional[random.Random] = None,
) -> Optional[str]:
    """Pick a variant id by Thompson sampling. None if there are none."""
    active = list(variants)[:MAX_ACTIVE]
    if not active:
        return None
    rng = rng or random.Random()
    use_global = segment_shows < MIN_SEGMENT and global_stats
    best_id, best_draw = None, -1.0
    for v in active:
        a, b = v.answered, v.not_answered
        if use_global and v.variant_id in global_stats:
            g = global_stats[v.variant_id]
            a, b = g.get("answered", 0), g.get("not_answered", 0)
        draw = _beta_sample(rng, a, b)
        if draw > best_draw:
            best_draw, best_id = draw, v.variant_id
    return best_id


def prob_best(
    variants: Sequence[VariantStats],
    *,
    samples: int = 4000,
    rng: Optional[random.Random] = None,
) -> dict[str, float]:
    """Monte-Carlo P(variant is the best) for each variant."""
    active = list(variants)
    if not active:
        return {}
    rng = rng or random.Random()
    wins = {v.variant_id: 0 for v in active}
    for _ in range(samples):
        best_id, best_draw = None, -1.0
        for v in active:
            d = _beta_sample(rng, v.answered, v.not_answered)
            if d > best_draw:
                best_draw, best_id = d, v.variant_id
        wins[best_id] += 1
    return {k: n / samples for k, n in wins.items()}


def to_retire(variants: Sequence[VariantStats], *, rng: Optional[random.Random] = None) -> list[str]:
    """Variant ids that should be auto-retired (enough shows, almost never best)."""
    active = list(variants)
    if len(active) < 2:
        return []
    probs = prob_best(active, rng=rng)
    out = []
    for v in active:
        if v.shows >= RETIRE_MIN_SHOWS and probs.get(v.variant_id, 0.0) < RETIRE_P:
            out.append(v.variant_id)
    # Never retire the current best, even if the whole field is thin.
    if probs:
        keep = max(probs, key=probs.get)
        out = [vid for vid in out if vid != keep]
    return out
