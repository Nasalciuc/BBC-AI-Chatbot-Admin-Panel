"""ci/check_code_columns.py (PR-14) — every column the code names exists.

Reads the literal `.table("t").select("a, b, c")` calls in app/ and checks
each (table, column) against information_schema of the database the
migrations were just replayed into. A column the code reads that no
migration creates is how a rebuilt database broke (pipeline_runs.tunnel,
users.chats_served_today, …).

Embedded resources (`conversations!inner(...)`), `*`, and expressions are
skipped; this is a floor, not a proof.

Usage: PGHOST=… PGPORT=… PGUSER=… PGDATABASE=… python ci/check_code_columns.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CALL = re.compile(r'table\(\s*"([a-z_]+)"\s*\)\s*\.select\(\s*"([^"]+)"', re.S)
IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
# An embedded resource: conversations!inner(a, b) or users(name) — its
# columns belong to the other table, so the whole group is dropped.
EMBED = re.compile(r"[a-z_]+(?:![a-z_]+)?\([^)]*\)")


def code_columns() -> dict[str, set[str]]:
    want: dict[str, set[str]] = {}
    for p in (ROOT / "app").rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        for table, cols in CALL.findall(src):
            for raw in EMBED.sub("", cols).split(","):
                c = raw.strip()
                if not c or c == "*" or "(" in c or "!" in c or ":" in c or "." in c:
                    continue
                if IDENT.match(c):
                    want.setdefault(table, set()).add(c)
    return want


def live_columns() -> dict[str, set[str]]:
    q = (
        "select table_name || '.' || column_name from information_schema.columns "
        "where table_schema = 'public'"
    )
    out = subprocess.run(["psql", "-tA", "-c", q], check=True, capture_output=True, text=True).stdout
    have: dict[str, set[str]] = {}
    for line in out.splitlines():
        if "." in line:
            t, c = line.split(".", 1)
            have.setdefault(t, set()).add(c)
    return have


def main() -> int:
    want, have = code_columns(), live_columns()
    missing = sorted(
        f"{t}.{c}" for t, cols in want.items() for c in cols if t in have and c not in have[t]
    )
    missing_tables = sorted(t for t in want if t not in have)
    for m in missing_tables:
        print(f"missing table: {m}")
    for m in missing:
        print(f"missing column: {m}")
    checked = sum(len(v) for v in want.values())
    print(f"checked {checked} columns in {len(want)} tables")
    return 1 if (missing or missing_tables) else 0


if __name__ == "__main__":
    sys.exit(main())
