"""Anthropic per-token list prices, in one place, with the cache tiers.

These are Anthropic's public published rates (USD per token), the same kind of
number the old COSTS dict in claude.py held — not any BBC fare or markup data.
Costing a call from the four usage counters (input, cache-write, cache-read,
output) instead of two is what makes the ledger match the invoice: a cache
write costs 1.25x (5m) or 2x (1h) input, a read 0.1x (0.05x on Sonnet 5.5 and
Opus 5.5; Haiku 5.5 keeps 0.1x), and none of that was being counted.

Haiku 5.5 has two price tiers by prompt size: up to 100k tokens, and over
100k, where every line costs 5x. The prompt size counts ALL its input tokens,
cache reads and cache writes included (Anthropic's pricing page), so the tier
is chosen on input + cache_write + cache_read, not on input alone.

Checked against https://platform.claude.com/docs/en/about-claude/pricing
on 2026-11-04.
`cost_from_usage` reads whichever `cache_creation` breakdown the API returns.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_M = 1_000_000


def _rates(inp, cw5, cw1h, cr, out):
    return {
        "input": inp / _M,
        "cache_write_5m": cw5 / _M,
        "cache_write_1h": cw1h / _M,
        "cache_read": cr / _M,
        "output": out / _M,
    }


# model id -> rates. 4.x: cache write 1.25x/2x input, read 0.1x input.
PRICES: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": _rates(1.0, 1.25, 2.0, 0.10, 5.0),
    "claude-sonnet-4-20250514": _rates(3.0, 3.75, 6.0, 0.30, 15.0),
    "claude-sonnet-4-6": _rates(3.0, 3.75, 6.0, 0.30, 15.0),
    "claude-opus-4-8": _rates(5.0, 6.25, 10.0, 0.50, 25.0),
    # Sonnet / Opus 5.5: cache read is 0.05x input.
    "claude-sonnet-5-5": _rates(2.0, 2.5, 4.0, 0.10, 10.0),
    "claude-opus-5-5": _rates(4.0, 5.0, 8.0, 0.20, 20.0),
    # Haiku 5.5, prompts up to 100k tokens: cache read 0.1x input.
    "claude-haiku-5-5": _rates(0.10, 0.125, 0.20, 0.01, 0.50),
}

# Haiku 5.5, prompts over 100k tokens: 5x on every line.
_HAIKU_55_BIG = _rates(0.50, 0.625, 1.0, 0.05, 2.50)
_HAIKU_55_BIG_THRESHOLD = 100_000

_FAMILY = {
    "haiku": PRICES["claude-haiku-4-5-20251001"],
    "sonnet": PRICES["claude-sonnet-4-6"],
    "opus": PRICES["claude-opus-4-8"],
}

_warned: set[str] = set()


def rates_for(model: str, prompt_tokens: int = 0) -> tuple[dict[str, float], bool]:
    """(rates, is_exact). Unknown model → family rates (+warn once) → zeros (+warn).

    prompt_tokens: the whole prompt (input + cache writes + cache reads)."""
    if model == "claude-haiku-5-5" and prompt_tokens > _HAIKU_55_BIG_THRESHOLD:
        return _HAIKU_55_BIG, True
    exact = PRICES.get(model)
    if exact is not None:
        return exact, True
    for family, r in _FAMILY.items():
        if family in model:
            if model not in _warned:
                _warned.add(model)
                logger.warning(f"[prices] no exact entry for {model} — using {family} family rates")
            return r, False
    if model not in _warned:
        _warned.add(model)
        logger.warning(f"[prices] no rates for {model} — recording $0")
    return _rates(0, 0, 0, 0, 0), False


def _int(obj: Any, name: str) -> int:
    v = getattr(obj, name, None)
    if v is None and isinstance(obj, dict):
        v = obj.get(name)
    try:
        return int(v or 0)
    except (TypeError, ValueError):  # noqa: silent — a non-numeric usage field costs 0 for that field
        return 0


def _breakdown(usage: Any) -> tuple[int, int, Optional[int], Optional[int]]:
    """(total_cache_write, cache_read, write_5m, write_1h). The split is present
    only on responses that used more than one TTL; else write_5m/1h are None."""
    total_write = _int(usage, "cache_creation_input_tokens")
    read = _int(usage, "cache_read_input_tokens")
    cc = getattr(usage, "cache_creation", None)
    if cc is None and isinstance(usage, dict):
        cc = usage.get("cache_creation")
    w5 = w1h = None
    if cc is not None:
        w5 = _int(cc, "ephemeral_5m_input_tokens")
        w1h = _int(cc, "ephemeral_1h_input_tokens")
        if (w5 or w1h) and not total_write:
            total_write = w5 + w1h
    return total_write, read, w5, w1h


def cost_from_usage(model: str, usage: Any) -> float:
    """Dollar cost of one call from its usage object (all four token kinds)."""
    if usage is None:
        return 0.0
    input_tokens = _int(usage, "input_tokens")
    output_tokens = _int(usage, "output_tokens")
    total_write, read, w5, w1h = _breakdown(usage)
    rates, _ = rates_for(model, input_tokens + total_write + read)
    if w5 is not None or w1h is not None:
        write_cost = (w5 or 0) * rates["cache_write_5m"] + (w1h or 0) * rates["cache_write_1h"]
    else:
        # No split returned: the default 5m TTL.
        write_cost = total_write * rates["cache_write_5m"]
    return round(
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + write_cost
        + read * rates["cache_read"],
        6,
    )
