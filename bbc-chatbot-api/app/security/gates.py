"""Role gates with a log-first rollout (PR-4).

`enforce_or_log(allowed, user, action, detail)` returns True when the action
may proceed. When the caller's role is not in `allowed`:
  - ROLE_GATES_ENFORCE on  → raise 403;
  - ROLE_GATES_ENFORCE off → log what it WOULD refuse and allow, so a day of
    watching cannot lock anyone out (the merge default).

The counters feed /health/detail so the owner can see the would-refuse rate
before turning enforcement on.
"""

from __future__ import annotations

import logging
from typing import Iterable

from fastapi import HTTPException

from app.flags import flag

logger = logging.getLogger(__name__)

GATE_HEALTH = {"checked": 0, "would_refuse": 0, "refused": 0}

MANAGEMENT = frozenset({"owner", "admin", "dev"})
OVERSIGHT = MANAGEMENT | {"supervisor", "qa", "project_manager"}


def enforce_or_log(allowed: Iterable[str], user: dict, action: str, detail: str = "") -> bool:
    GATE_HEALTH["checked"] += 1
    role = (user or {}).get("role", "sales")
    if role in set(allowed):
        return True
    GATE_HEALTH["would_refuse"] += 1
    enforce = bool(flag("ROLE_GATES_ENFORCE", False))
    logger.warning(
        f"[role-gate] {'refuse' if enforce else 'would-refuse'} {action} "
        f"role={role} {detail}".rstrip()
    )
    if enforce:
        GATE_HEALTH["refused"] += 1
        raise HTTPException(status_code=403, detail="Forbidden")
    return False


def _reset_for_tests() -> None:
    GATE_HEALTH.update(checked=0, would_refuse=0, refused=0)
