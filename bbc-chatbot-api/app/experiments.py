"""Experiment arms that do not move — rule R11 of the plan.

A conversation's arm is `sha256("<experiment>:<conversation_id>") % 100`.
- Salted by the experiment's own name, so two experiments never pick the same
  conversations (a graph-engine arm and a model-family arm would otherwise be
  the same 10%, and neither result could be read).
- sha256, never Python's `hash()`: that one is salted per process, so every
  restart would reshuffle the groups.
- No conversation id yet (the pipeline creates it) → the control arm, until
  one exists.
"""

from __future__ import annotations

import hashlib
from typing import Optional


def bucket(experiment: str, conversation_id: Optional[str]) -> Optional[int]:
    """0–99 for a conversation, None when there is no id yet."""
    if not conversation_id:
        return None
    digest = hashlib.sha256(f"{experiment}:{conversation_id}".encode("utf-8")).hexdigest()
    return int(digest, 16) % 100


def in_arm(experiment: str, conversation_id: Optional[str], percent: int) -> bool:
    """True when the conversation is inside the first `percent` buckets."""
    b = bucket(experiment, conversation_id)
    if b is None:
        return False
    return b < max(0, min(100, int(percent)))
