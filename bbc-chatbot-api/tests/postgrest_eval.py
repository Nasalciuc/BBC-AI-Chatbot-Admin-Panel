"""A small reader of the PostgREST filters our code builds, so tests can hold a filter to rows.

It follows PostgREST v12 as its source reads (ApiRequest/QueryParams.hs, Query/SqlFragment.hs), for the
part of the grammar we use:
  - a logic tree: or(...), and(...), not.or(...), not.and(...), split on the commas outside parentheses
    and outside double quotes;
  - a leaf: field.[not.]op.value, where a double-quoted value loses its quotes and a backslash inside
    them makes the next character plain (pQuotedValue); an unquoted value runs to the next , or );
  - like/ilike: every * becomes %, then Postgres LIKE (% any run, _ one character, \\ escapes);
    match/imatch: a Postgres regex, searched, not anchored;
  - SQL's three values: a comparison with NULL is unknown (None), NOT unknown is unknown, and a row is
    kept only when the whole filter is true.
A top-level parameter (column=op.value) takes its value as it is, quotes included (pSingleVal).
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

Truth = Optional[bool]


def split_top(s: str) -> list[str]:
    parts, depth, cur, in_quotes, escaped = [], 0, "", False, False
    for ch in s:
        if in_quotes:
            cur += ch
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quotes = False
            continue
        if ch == '"':
            in_quotes = True
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            assert depth >= 0, f"unbalanced ) in {s!r}"
        cur += ch
    assert depth == 0 and not in_quotes, f"unbalanced filter {s!r}"
    return parts + [cur]


def unquote(raw: str) -> str:
    """pLogicSingleVal: a quoted value, else the raw text (which may hold no , or ) of its own)."""
    if not raw.startswith('"'):
        assert "," not in raw and ")" not in raw and "(" not in raw or raw.startswith("("), \
            f"an unquoted value with a reserved character would be read differently: {raw!r}"
        return raw
    out, i = "", 1
    while i < len(raw):
        ch = raw[i]
        if ch == "\\":
            assert i + 1 < len(raw), f"dangling backslash in {raw!r}"
            out += raw[i + 1]
            i += 2
            continue
        if ch == '"':
            assert i == len(raw) - 1, f"text after the closing quote in {raw!r}"
            return out
        out += ch
        i += 1
    raise AssertionError(f"unterminated quote in {raw!r}")


def cell(row: dict, field: str) -> Any:
    """A column, or a jsonb ->> path, as Postgres would hand it to the comparison."""
    if "->>" in field:
        base, key = field.split("->>", 1)
        v = (row.get(base) or {}).get(key)
        if v is None:
            return None
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (dict, list)):
            return json.dumps(v)
        return str(v)
    return row.get(field)


def _text(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def like(pattern: str, text: str, *, case: bool) -> bool:
    pattern = pattern.replace("*", "%")  # PostgREST's star, before Postgres sees the pattern
    rx, i = "", 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            rx += re.escape(pattern[i + 1])
            i += 2
            continue
        rx += ".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
        i += 1
    return re.fullmatch(rx, text, re.S if case else re.S | re.I) is not None


def compare(v: Any, op: str, val: str) -> Truth:
    if op == "is":
        want = {"null": None, "true": True, "false": False}[val]
        return v is None if want is None else v is want or _text(v) == _text(want)
    if v is None:
        return None
    if op == "eq":
        return _text(v) == val
    if op == "neq":
        return _text(v) != val
    if op == "in":
        assert val.startswith("(") and val.endswith(")"), val
        return _text(v) in [unquote(x) for x in split_top(val[1:-1])]
    if op in ("like", "ilike"):
        return like(val, _text(v), case=op == "like")
    if op in ("match", "imatch"):
        return re.search(val, _text(v), 0 if op == "match" else re.I) is not None
    if op in ("gt", "gte", "lt", "lte"):
        a, b = _text(v), val
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    raise AssertionError(f"the evaluator doesn't know {op!r}")


def _and(values) -> Truth:
    values = list(values)
    if any(x is False for x in values):
        return False
    return None if any(x is None for x in values) else True


def _or(values) -> Truth:
    values = list(values)
    if any(x is True for x in values):
        return True
    return None if any(x is None for x in values) else False


def _not(x: Truth) -> Truth:
    return None if x is None else not x


def truth(expr: str, row: dict) -> Truth:
    """One logic tree or leaf, in SQL's three values."""
    negated = expr.startswith("not.")
    body = expr[4:] if negated else expr
    for op, join in (("or(", _or), ("and(", _and)):
        if body.startswith(op) and body.endswith(")"):
            value = join(truth(p, row) for p in split_top(body[len(op):-1]))
            return _not(value) if negated else value
    field, rest = expr.split(".", 1)
    leaf_negated = rest.startswith("not.")
    op, raw = (rest[4:] if leaf_negated else rest).split(".", 1)
    value = compare(cell(row, field), op, raw if op == "in" else unquote(raw))
    return _not(value) if leaf_negated else value


def holds(expr: str, row: dict) -> bool:
    return truth(expr, row) is True


# ── a whole recorded query: what the tests' recorder saw, against one row ─────


def _top(row: dict, name: str, args: tuple, kwargs: dict) -> Truth:
    if name == "or_":
        ref = kwargs.get("reference_table")
        assert ref is None, "reference_table filters are read with the embed"
        return truth(f"or({args[0]})", row)
    col, val = args[0], args[1] if len(args) > 1 else None
    v = cell(row, col)
    if name == "eq":
        return compare(v, "eq", _text(val))
    if name == "neq":
        return compare(v, "neq", _text(val))
    if name == "is_":
        return compare(v, "is", _text(val))
    if name in ("gte", "gt", "lt", "lte"):
        return compare(v, name, _text(val))
    if name in ("ilike", "like"):
        return compare(v, name, val)  # top level: the value as it is
    if name == "in_":
        return None if v is None else _text(v) in [_text(x) for x in val]
    raise AssertionError(f"the evaluator doesn't know the call {name!r}")


FILTER_CALLS = {"or_", "eq", "neq", "is_", "gte", "gt", "lt", "lte", "ilike", "like", "in_"}


def query_keeps(calls: list, row: dict, *, embed: str = "leads") -> bool:
    """Every top-level filter holds, and for an inner embed at least one embedded row passes all of
    its own filters (embed.column=..., embed.or=...)."""
    own, on_embed = [], []
    for name, args, kwargs in calls:
        if name not in FILTER_CALLS:
            continue
        if kwargs.get("reference_table") == embed or (args and isinstance(args[0], str)
                                                       and args[0].startswith(embed + ".")):
            on_embed.append((name, args, kwargs))
        else:
            own.append((name, args, kwargs))
    if not all(_top(row, n, a, k) is True for n, a, k in own):
        return False
    if not on_embed:
        return True
    for sub in row.get(embed) or []:
        ok = True
        for name, args, kwargs in on_embed:
            if name == "or_":
                ok = truth(f"or({args[0]})", sub) is True
            else:
                ok = _top(sub, name, (args[0][len(embed) + 1:],) + tuple(args[1:]), kwargs) is True
            if not ok:
                break
        if ok:
            return True
    return False
