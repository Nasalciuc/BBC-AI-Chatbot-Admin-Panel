"""The model router (PR-6) — the III.3 policy table as code, not prompt.

decide(task, …) returns a Route: which model, which effort, the max_tokens,
the cache TTL and a reason. The table is the single place that answers "which
brain for this turn", so the cost can be reasoned about and tested row by row.

Family: the 5.5 models are chosen only when the conversation is in the
MODEL_FAMILY_55_PERCENT arm AND the turn runs on the graph engine (the caller
passes family_55=…). On legacy, or outside the arm, the 4.x models stay.
Opus is offered only behind ALLOW_OPUS.

Budget: at 80% of the daily cap the sales tiers drop to Haiku; at 100% the
turn is a template with the brand phone (model=None). The cap itself never
lowers — a small cap is an attacker's lever to push everyone onto the template.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config.settings import settings
from app.flags import flag

# Tasks the router knows. Utility tasks (classify/extract/summary/iata) are
# routed by PR-2.7's UTILITY_MODEL_55, not here; here is the reply tiers.
SALES_REPLY = "sales_reply"
SALES_DECISIVE = "sales_decisive"
COLLECT_REPLY = "collect_reply"
CLOSING = "closing"
SUPPORT_REPLY = "support_reply"


@dataclass(frozen=True)
class Route:
    model: Optional[str]      # None = answer with a template (budget exhausted)
    effort: str               # "low" | "medium" | "high"
    max_tokens: int
    cache_ttl: str            # "5m" | "1h"
    reason: str
    family: str = "4x"        # "4x" | "55"


def _family(family_55: bool) -> str:
    return "55" if family_55 else "4x"


def _model_for(tier: str, family_55: bool) -> str:
    """Resolve a tier name to a concrete model id for the chosen family."""
    if family_55:
        return {
            "haiku": settings.claude_haiku_55_model,
            "sonnet": settings.claude_sonnet_55_model,
            "opus": settings.claude_opus_55_model,
        }[tier]
    return {
        "haiku": settings.claude_haiku_model,
        "sonnet": settings.claude_sonnet_model,
        "opus": settings.claude_opus_model,
    }[tier]


def _cache_ttl() -> str:
    ttl = str(flag("CACHE_TTL", "5m")).strip().lower()
    return "1h" if ttl in ("1h", "60m", "3600") else "5m"


def decide(
    task: str,
    *,
    decisive: bool = False,
    family_55: bool = False,
    budget_fraction: float = 0.0,
) -> Route:
    """Pick the route for a reply turn.

    decisive: the caller (generator._select_sales_model) already decided this
    is a premium turn (price, talk-to-agent, booking change, gold, First,
    experience-seeker). budget_fraction: today's spend / daily_budget.
    """
    ttl = _cache_ttl()

    # 100% of budget → template, whatever the task.
    if budget_fraction >= 1.0:
        return Route(None, "low", 0, ttl, "budget_exhausted", _family(family_55))

    over_80 = budget_fraction >= 0.8
    allow_opus = bool(flag("ALLOW_OPUS", False))

    if task == SUPPORT_REPLY:
        return Route(_model_for("haiku", family_55), "low", 1024, ttl, "support", _family(family_55))

    if task == CLOSING:
        # Closing stays Opus 4.8 on the 4.x family (unchanged); Sonnet 5.5 low
        # on the new family.
        tier = "sonnet" if family_55 else "opus"
        return Route(_model_for(tier, family_55), "low", 1024, ttl, "closing", _family(family_55))

    if task == COLLECT_REPLY:
        # A/B: Haiku for the data-gathering turns when COLLECT_MODEL=haiku.
        if str(flag("COLLECT_MODEL", "sonnet")).strip().lower() == "haiku":
            return Route(_model_for("haiku", family_55), "low", 1024, ttl, "collect_haiku", _family(family_55))
        return Route(_model_for("sonnet", family_55), "low", 1024, ttl, "collect_sonnet", _family(family_55))

    # sales_reply / sales_decisive
    if over_80:
        return Route(_model_for("haiku", family_55), "low", 1024, ttl, "budget_80_haiku", _family(family_55))

    if decisive or task == SALES_DECISIVE:
        if allow_opus and family_55:
            return Route(_model_for("opus", family_55), "medium", 1536, ttl, "decisive_opus", _family(family_55))
        if not family_55:
            # 4.x decisive keeps today's Opus 4.8.
            return Route(_model_for("opus", family_55), "medium", 1536, ttl, "decisive_opus4x", _family(family_55))
        return Route(_model_for("sonnet", family_55), "medium", 1536, ttl, "decisive_sonnet", _family(family_55))

    return Route(_model_for("sonnet", family_55), "low", 1024, ttl, "sales_default", _family(family_55))
