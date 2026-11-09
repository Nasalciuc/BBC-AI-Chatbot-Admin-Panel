"""The widget form's trap (PR-12).

The pre-chat form carries a hidden `website` field a person never sees and
the time from the form appearing to its submit. A bot fills every field and
submits at once; autofill can also be faster than 1.5 s, so the trap never
throws a visitor away:

  FORM_TRAP_MODE=count (merge default, 7 days) → counted in /health only;
  FORM_TRAP_MODE=mark  → the conversation is created with
                         metadata.suspect="form_trap": it shows a badge in the
                         panel and does NOT go to the CRM on its own — a human
                         can still push it;
  FORM_TRAP_MODE=off   → ignored.
"""

from __future__ import annotations

from typing import Optional

from app.flags import flag

MIN_FILL_MS = 1500

FORM_TRAP_HEALTH = {"seen": 0, "honeypot": 0, "too_fast": 0, "marked": 0, "crm_held": 0}


def evaluate(form_check: Optional[dict]) -> Optional[str]:
    """The trap that fired ("honeypot" | "too_fast"), or None."""
    if not isinstance(form_check, dict):
        return None
    FORM_TRAP_HEALTH["seen"] += 1
    if str(form_check.get("website") or "").strip():
        FORM_TRAP_HEALTH["honeypot"] += 1
        return "honeypot"
    try:
        elapsed = int(form_check.get("elapsed_ms"))
    except (TypeError, ValueError):  # noqa: silent — no timing from an old widget is "not measured", not a trap
        return None
    if 0 <= elapsed < MIN_FILL_MS:
        FORM_TRAP_HEALTH["too_fast"] += 1
        return "too_fast"
    return None


def apply(form_check: Optional[dict], meta: dict) -> Optional[str]:
    """Evaluate and, in mark mode, stamp meta. Returns the trap that fired."""
    mode = str(flag("FORM_TRAP_MODE", "count")).strip().lower()
    if mode == "off":
        return None
    hit = evaluate(form_check)
    if hit and mode == "mark":
        meta["suspect"] = "form_trap"
        meta["suspect_reason"] = hit
        FORM_TRAP_HEALTH["marked"] += 1
    return hit


def holds_crm(conv_metadata: Optional[dict]) -> bool:
    """True when an automatic CRM push must wait for a human."""
    held = (conv_metadata or {}).get("suspect") == "form_trap"
    if held:
        FORM_TRAP_HEALTH["crm_held"] += 1
    return held


def _reset_for_tests() -> None:
    FORM_TRAP_HEALTH.update(seen=0, honeypot=0, too_fast=0, marked=0, crm_held=0)
