#!/usr/bin/env python3
"""Offline router evaluation (PR-6.5). Owner-run with `railway run`; writes to
out/ (gitignored). Compares today's models (a) against Sonnet 5.5 (b) and
Sonnet 5.5 + Haiku collect (c) on real conversation prefixes, masked in memory.

Deterministic checks per reply: ends with exactly one question; asks the slot
the policy wants; invents no day/airport/pax; promises no price; <=3 sentences;
no non-brand phone/link; non-empty, non-refusal. The gate: (b) >= (a) on every
check within 1pp; (c) >= (b) on "asks the wanted slot" and "no invented detail".

This is a scaffold: it needs a production DB (SELECT) and real API keys, so it
never runs in CI. Run: railway run python scripts/eval_router.py --limit 300
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.privacy.mask import mask_pii

OUT = Path(__file__).resolve().parent.parent / "out"
_QUESTION = re.compile(r"\?")
_PRICE_PROMISE = re.compile(r"\$\s?\d|\bguarantee|\bwe can book\b", re.I)


def deterministic_checks(reply: str) -> dict:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", reply.strip()) if s]
    return {
        "one_question": reply.count("?") == 1,
        "not_empty": bool(reply.strip()),
        "max_3_sentences": len(sentences) <= 3,
        "no_price_promise": not _PRICE_PROMISE.search(reply),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    print(
        "eval_router is a scaffold: wire it to a read-only production SELECT of "
        "closed conversations, mask each prefix with mask_pii, run configs (a)/(b)/(c) "
        "through app.ai.router + app.ai.llm, and write out/router_eval.json with the "
        "per-check pass rates, judge score, cost/turn and p50/p95 latency."
    )
    if args.dry_run:
        (OUT / "router_eval.json").write_text(json.dumps({"status": "dry_run"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
