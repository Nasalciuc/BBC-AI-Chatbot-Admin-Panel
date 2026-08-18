"""Three rules that hold the line, checked by a machine instead of by goodwill.

Each rule was written by an incident, not by taste:

1. **Silent exception handlers.** 68 handlers in `app/` swallow an exception
   without logging it and without re-raising. Only 12 of them carry a comment
   explaining why — the other 56 were written to make the code run, not to
   handle anything. The dominant shape is not `pass` (17) but
   `return <sentinel>` (40): the caller cannot tell "not applicable" from "it
   broke". `_find_sticky_operator() -> None` means either "this client has no
   operator stuck to them" or "the query failed", and routing takes the same
   decision either way.

2. **Conditionally assigned locals.** 17 Aug 2026: five `UnboundLocalError`
   in `_pipeline` put 16 clients on emergency-template replies for ten hours,
   while `/health` reported zero.

3. **Early returns that answer without saving.** A branch that replies to the
   client before entity extraction, without first persisting what the client
   said, throws away the route, the dates, the passengers and the cabin — the
   whole reason the conversation exists.

None of these rules can be enforced retroactively without stopping all other
work, so each one is FROZEN against a baseline. The baseline may only shrink.
New code is held to the rule; old code is held to "do not get worse".

Run just these:  python -m pytest tests/test_code_discipline.py -q
"""

import ast
import re
from collections import Counter
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = API_ROOT / "app"
BASELINE_DIR = Path(__file__).resolve().parent / "baselines"

SILENT_BASELINE = BASELINE_DIR / "silent_except.txt"
LOCALS_BASELINE = BASELINE_DIR / "conditional_locals.txt"
RETURNS_BASELINE = BASELINE_DIR / "early_returns.txt"

# ══════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════

# Loud enough to survive production. `debug` is deliberately absent — see
# DEBUG_IS_SILENCE below.
LOUD_LOG_METHODS = frozenset({"warning", "error", "exception", "critical", "info"})

# In these packages the log level in production is INFO, so a `logger.debug`
# in an exception handler is indistinguishable from saying nothing at all.
# Everywhere else (scripts, one-off tooling) debug is a real log.
DEBUG_IS_SILENCE = ("app/services", "app/pipeline", "app/api")

LOGGER_NAMES = frozenset({"logger", "log", "logging", "_logger", "LOGGER"})

# The one escape hatch. It must say WHY, in words a reviewer can disagree
# with — ten characters is roughly "cache warm" and anything shorter is a
# shrug, not a reason.
WAIVER_RE = re.compile(r"#\s*noqa:\s*silent\s*(?:[-—–:]+\s*)?(?P<reason>.*)$")
MIN_WAIVER_REASON = 10

SENTINEL_RETURNS = ("None", "False", "{}", "[]", '""', "''", "0")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return path.relative_to(API_ROOT).as_posix()


def _app_files() -> list[Path]:
    return sorted(
        p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts
    )


def _load_baseline(path: Path) -> Counter:
    if not path.exists():
        return Counter()
    lines = [
        ln.strip()
        for ln in _read(path).splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    return Counter(lines)


def _qualname_map(tree: ast.AST) -> dict[int, str]:
    """Every node id -> the dotted name of the function that encloses it.

    Module level reads as `<module>` so a finding outside any function still
    gets a stable, line-number-free address.
    """
    out: dict[int, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}.{child.name}" if prefix != "<module>" else child.name
                out[id(child)] = name
                walk(child, name)
            else:
                out[id(child)] = prefix
                walk(child, prefix)

    out[id(tree)] = "<module>"
    walk(tree, "<module>")
    return out


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _waiver_on_lines(source_lines: list[str], linenos: list[int]) -> str | None:
    """The `# noqa: silent — reason` for a finding, if it carries one.

    Returns the reason (possibly too short — the caller judges), or None.
    """
    for lineno in linenos:
        if 1 <= lineno <= len(source_lines):
            m = WAIVER_RE.search(source_lines[lineno - 1])
            if m:
                return m.group("reason").strip()
    return None


# ══════════════════════════════════════════════════════════════════
# Rule 1 — silent exception handlers
# ══════════════════════════════════════════════════════════════════


def _is_logger_call(node: ast.AST) -> tuple[str, ast.Call] | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    owner = node.func.value
    if isinstance(owner, ast.Name) and owner.id in LOGGER_NAMES:
        return node.func.attr, node
    if isinstance(owner, ast.Attribute) and owner.attr in LOGGER_NAMES:
        return node.func.attr, node
    return None


def _log_carries_the_exception(call: ast.Call, method: str, exc_name: str | None) -> bool:
    """A log that does not carry the error is a log that says 'something'.

    `logger.warning("failed")` tells whoever is paged exactly nothing: not
    which record, not which call, not what the database actually said.
    """
    if method == "exception":
        return True  # always attaches the traceback
    for kw in call.keywords:
        if kw.arg == "exc_info" and not (
            isinstance(kw.value, ast.Constant) and kw.value.value in (False, None)
        ):
            return True
    if exc_name is None:
        # Nothing was bound, so there is nothing the message could carry.
        return False
    for arg in call.args:
        if exc_name in _names_in(arg):
            return True
    for kw in call.keywords:
        if exc_name in _names_in(kw.value):
            return True
    return False


def _raise_preserves_the_cause(node: ast.Raise, exc_name: str | None) -> bool:
    if node.exc is None:
        return True  # bare `raise` — the original, untouched
    if node.cause is not None:
        return True  # `raise Other(...) from e`
    if exc_name is not None and isinstance(node.exc, ast.Name) and node.exc.id == exc_name:
        return True  # `raise e`
    if exc_name is None:
        # Nothing bound to chain from; Python still sets __context__.
        return True
    return False


def _body_shape(handler: ast.ExceptHandler) -> str:
    """A short, stable name for what the handler's body actually is."""
    body = [s for s in handler.body if not isinstance(s, ast.Expr) or not isinstance(getattr(s, "value", None), ast.Constant)]
    if len(body) == 1:
        only = body[0]
        if isinstance(only, ast.Pass):
            return "pass"
        if isinstance(only, ast.Continue):
            return "continue"
        if isinstance(only, ast.Break):
            return "break"
        if isinstance(only, ast.Return):
            if only.value is None:
                return "return-sentinel"
            try:
                if ast.unparse(only.value).strip() in SENTINEL_RETURNS:
                    return "return-sentinel"
            except Exception:  # noqa: silent — ast.unparse on an exotic node; shape falls back to a coarser label
                pass
    return "body"


def scan_source_for_silent_excepts(source: str, rel_path: str) -> tuple[list[str], list[str]]:
    """Findings and waivers for one file.

    Returns (findings, waivers). A finding is `path::function::kind` with no
    line number, deliberately: reformatting a file must never break this test,
    or the test becomes the thing people work around.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    quals = _qualname_map(tree)
    debug_is_silence = any(rel_path.startswith(p) for p in DEBUG_IS_SILENCE)
    loud = LOUD_LOG_METHODS if debug_is_silence else (LOUD_LOG_METHODS | {"debug"})

    findings: list[str] = []
    waivers: list[str] = []

    def record(node: ast.AST, kind: str, waiver_lines: list[int]) -> None:
        func = quals.get(id(node), "<module>")
        reason = _waiver_on_lines(lines, waiver_lines)
        if reason is not None:
            if len(reason) < MIN_WAIVER_REASON:
                findings.append(f"{rel_path}::{func}::waiver-without-a-reason")
            else:
                waivers.append(f"{rel_path}::{func} — {reason}")
            return
        findings.append(f"{rel_path}::{func}::{kind}")

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            exc_name = node.name
            has_proper_log = False
            has_improper_log = False
            has_debug_only = False
            has_proper_raise = False
            has_improper_raise = False

            for sub in ast.walk(node):
                found = _is_logger_call(sub)
                if found:
                    method, call = found
                    if method in loud:
                        if _log_carries_the_exception(call, method, exc_name):
                            has_proper_log = True
                        else:
                            has_improper_log = True
                    elif method == "debug":
                        # Production runs at INFO. A debug line here is a note
                        # to a developer who is not there.
                        has_debug_only = True
                elif isinstance(sub, ast.Raise):
                    if _raise_preserves_the_cause(sub, exc_name):
                        has_proper_raise = True
                    else:
                        has_improper_raise = True

            if has_proper_log or has_proper_raise:
                continue

            if has_improper_log:
                kind = "log-without-exception"
            elif has_debug_only:
                kind = "debug-only-log"
            elif has_improper_raise:
                kind = "raise-without-from"
            else:
                kind = _body_shape(node)

            first_body_line = node.body[0].lineno if node.body else node.lineno
            record(node, kind, [node.lineno, first_body_line])

        elif isinstance(node, ast.With) or isinstance(node, ast.AsyncWith):
            if not debug_is_silence:
                continue
            for item in node.items:
                call = item.context_expr
                if not isinstance(call, ast.Call):
                    continue
                fn = call.func
                name = (
                    fn.attr if isinstance(fn, ast.Attribute)
                    else fn.id if isinstance(fn, ast.Name)
                    else ""
                )
                if name == "suppress":
                    first_body_line = node.body[0].lineno if node.body else node.lineno
                    record(node, "suppress", [node.lineno, first_body_line])

    return findings, waivers


def scan_file_for_silent_excepts(path: Path, rel_path: str | None = None):
    return scan_source_for_silent_excepts(_read(path), rel_path or _rel(path))


def collect_silent_excepts() -> tuple[Counter, list[str]]:
    found: Counter = Counter()
    waivers: list[str] = []
    for path in _app_files():
        f, w = scan_file_for_silent_excepts(path)
        found.update(f)
        waivers.extend(w)
    return found, waivers


HOW_TO_FIX_SILENT = """
Two ways to fix — pick one:
  1) logger.warning(f"[{cid}] <what was lost>: {e}")  + a /health counter
  2) except Exception:  # noqa: silent — <why this failure is genuinely harmless>
Never edit the baseline by hand.
""".strip()


def _compare_to_baseline(found: Counter, baseline: Counter, rule: str, how_to_fix: str) -> None:
    added = found - baseline
    removed = baseline - found

    problems: list[str] = []
    if added:
        listed = "\n".join(f"  + {k}" + (f"  (x{v})" if v > 1 else "") for k, v in sorted(added.items()))
        problems.append(f"{rule}: new finding(s) not in the baseline:\n{listed}\n\n{how_to_fix}")
    if removed:
        listed = "\n".join(f"  - {k}" + (f"  (x{v})" if v > 1 else "") for k, v in sorted(removed.items()))
        problems.append(
            f"{rule}: the baseline still lists finding(s) that no longer exist:\n{listed}\n\n"
            f"You fixed them — good. Now delete those exact lines from the baseline file\n"
            f"so the number on the tin stays true. The baseline may only ever shrink."
        )
    if problems:
        pytest.fail("\n\n".join(problems), pytrace=False)


def test_no_new_silent_excepts():
    """An exception nobody logged is a failure the client discovers for us."""
    found, waivers = collect_silent_excepts()
    print(f"\nsupape: {len(waivers)}")
    for w in sorted(waivers):
        print(f"  {w}")
    _compare_to_baseline(
        found,
        _load_baseline(SILENT_BASELINE),
        "Silent exception handler",
        HOW_TO_FIX_SILENT,
    )


def test_every_waiver_gives_a_reason_a_reviewer_can_argue_with():
    """`# noqa: silent` with no reason is the same shrug in a costume."""
    found, _ = collect_silent_excepts()
    bad = [k for k in found if k.endswith("::waiver-without-a-reason")]
    assert not bad, (
        "These waivers do not say why the failure is harmless:\n  "
        + "\n  ".join(sorted(bad))
        + f"\nA reason must be at least {MIN_WAIVER_REASON} characters and must explain "
        "the harm to the CLIENT, not to the code."
    )


# ══════════════════════════════════════════════════════════════════
# Rule 2 — conditionally assigned locals
# ══════════════════════════════════════════════════════════════════

LOCALS_SCOPE_GLOBS = ("app/pipeline/*.py",)
LOCALS_SCOPE_FILES = ("app/api/chat.py",)

UNBOUND_LESSON = (
    "initialize `{name} = <neutral default>` at function top — 17 Aug 2026: five "
    "UnboundLocalError in `_pipeline`, 10 hours of template replies, 16 clients."
)


def _iter_functions(tree: ast.AST):
    quals = _qualname_map(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield quals.get(id(node), node.name), node


def _own_body(fn: ast.AST):
    """Every node in this function EXCEPT the bodies of functions nested in it.

    A nested def has its own scope; its locals are none of this rule's business,
    and a closure reads its enclosing names when it is CALLED, not here.
    """
    stack = [s for s in fn.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


# ── Definite assignment ───────────────────────────────────────────
# The naive version of this rule ("all bindings sit inside some block") found
# 276 sites in the pipeline, almost all of them safe: `res = db.query()` inside
# a `try` whose handler returns is not a hazard. A rule that cries 276 times
# gets deleted in a month. So the guard answers the real question instead:
# *on every path that reaches this read, has the name been bound?*


def _exits(body: list[ast.stmt]) -> bool:
    """Does this block always leave — return, raise, continue or break?"""
    for st in body:
        if isinstance(st, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
            return True
        if isinstance(st, ast.If) and st.orelse and _exits(st.body) and _exits(st.orelse):
            return True
        if isinstance(st, ast.With) or isinstance(st, ast.AsyncWith):
            if _exits(st.body):
                return True
    return False


def _bound_targets(node: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(node, ast.Assign):
        for t in node.targets:
            names |= _names_in(t)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        if node.value is not None or isinstance(node, ast.AugAssign):
            names |= _names_in(node.target)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            names.add((alias.asname or alias.name).split(".")[0])
    return names


def _always_binds(st: ast.stmt, name: str) -> bool:
    """Does executing this ONE statement bind `name` on every path that survives it?"""
    if name in _bound_targets(st):
        return True
    if isinstance(st, (ast.With, ast.AsyncWith)):
        for item in st.items:
            if item.optional_vars is not None and name in _names_in(item.optional_vars):
                return True
        return _always_binds_body(st.body, name)
    if isinstance(st, ast.If):
        if not st.orelse:
            return False  # the branch that is not taken binds nothing
        return (
            (_always_binds_body(st.body, name) or _exits(st.body))
            and (_always_binds_body(st.orelse, name) or _exits(st.orelse))
        )
    if isinstance(st, ast.Try):
        if _always_binds_body(st.finalbody, name):
            return True
        main = _always_binds_body(st.body, name) or _always_binds_body(st.orelse, name)
        handlers = all(
            _always_binds_body(h.body, name) or _exits(h.body) for h in st.handlers
        )
        return main and handlers
    if isinstance(st, ast.Match):
        cases = st.cases
        catch_all = any(
            isinstance(c.pattern, ast.MatchAs) and c.pattern.pattern is None and c.guard is None
            for c in cases
        )
        return bool(cases) and catch_all and all(
            _always_binds_body(c.body, name) or _exits(c.body) for c in cases
        )
    # for / while: the body may run zero times.
    if isinstance(st, (ast.For, ast.AsyncFor, ast.While)):
        return _always_binds_body(st.orelse, name)
    return False


def _always_binds_body(body: list[ast.stmt], name: str) -> bool:
    return any(_always_binds(st, name) for st in body)


def _binds_before(body: list[ast.stmt], name: str, target_line: int) -> bool:
    """Is `name` bound on every path through `body` that reaches `target_line`?"""
    for st in body:
        start, end = st.lineno, (st.end_lineno or st.lineno)
        if start > target_line:
            break
        if start <= target_line <= end:
            # The read happens INSIDE this statement, so this statement's own
            # assignment does not count (`x = x + 1` reads before it binds).
            # Descend only into the branch that actually contains the read —
            # the sibling branches never ran.
            if _binds_on_entry(st, name):
                return True
            for branch in _containing_branches(st, target_line):
                if _binds_before(branch, name, target_line):
                    return True
            return False
        if _always_binds(st, name):
            return True
    return False


def _binds_on_entry(st: ast.stmt, name: str) -> bool:
    """Bindings a block makes as you enter it — `with ... as f`, `for x in`,
    `except ... as e` — which ARE in force inside that block."""
    if isinstance(st, (ast.With, ast.AsyncWith)):
        return any(
            item.optional_vars is not None and name in _names_in(item.optional_vars)
            for item in st.items
        )
    if isinstance(st, (ast.For, ast.AsyncFor)):
        return name in _names_in(st.target)
    if isinstance(st, ast.Try):
        return any(h.name == name for h in st.handlers)
    return False


def _containing_branches(st: ast.stmt, target_line: int) -> list[list[ast.stmt]]:
    """The sub-blocks of `st` that contain target_line — the paths actually taken."""
    def hit(body: list[ast.stmt]) -> bool:
        return any(s.lineno <= target_line <= (s.end_lineno or s.lineno) for s in body)

    out: list[list[ast.stmt]] = []
    if isinstance(st, ast.If):
        for b in (st.body, st.orelse):
            if hit(b):
                out.append(b)
    elif isinstance(st, ast.Try):
        # A read inside a handler CANNOT rely on the try body having got that
        # far — that is the classic shape of the 17 Aug incident.
        for h in st.handlers:
            if hit(h.body):
                out.append(h.body)
        if not out:
            for b in (st.body, st.orelse, st.finalbody):
                if hit(b):
                    out.append(b)
    elif isinstance(st, (ast.For, ast.AsyncFor, ast.While)):
        for b in (st.body, st.orelse):
            if hit(b):
                out.append(b)
    elif isinstance(st, (ast.With, ast.AsyncWith)):
        if hit(st.body):
            out.append(st.body)
    elif isinstance(st, ast.Match):
        for c in st.cases:
            if hit(c.body):
                out.append(c.body)
    return out


def scan_source_for_conditional_locals(source: str, rel_path: str) -> list[str]:
    tree = ast.parse(source)
    findings: list[str] = []

    for qual, fn in _iter_functions(tree):
        params = {a.arg for a in (
            fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
        )}
        if fn.args.vararg:
            params.add(fn.args.vararg.arg)
        if fn.args.kwarg:
            params.add(fn.args.kwarg.arg)

        declared: set[str] = set()          # global / nonlocal
        excluded: set[str] = set(params)    # never our business
        binds: dict[str, int] = {}          # name -> first binding line
        reads: list[tuple[str, int]] = []   # (name, lineno), in source order

        for node in _own_body(fn):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                declared.update(node.names)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                excluded.update(_names_in(node.target))
            elif isinstance(node, ast.NamedExpr):
                excluded.update(_names_in(node.target))
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                for gen in node.generators:
                    excluded.update(_names_in(gen.target))
            elif isinstance(node, ast.ExceptHandler) and node.name:
                # Python unbinds it at the end of the handler; reading it
                # afterwards is a different bug, and a louder one.
                excluded.add(node.name)
            elif isinstance(node, ast.Name):
                ln = getattr(node, "lineno", 0)
                if isinstance(node.ctx, ast.Store):
                    binds[node.id] = min(binds.get(node.id, ln), ln)
                elif isinstance(node.ctx, ast.Load):
                    reads.append((node.id, ln))
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                for n in _names_in(node.optional_vars):
                    ln = getattr(node.context_expr, "lineno", 0)
                    binds[n] = min(binds.get(n, ln), ln)

        seen: set[str] = set()
        for name, lineno in sorted(reads, key=lambda r: r[1]):
            if name in seen or name in excluded or name in declared or name not in binds:
                continue
            if _binds_before(fn.body, name, lineno):
                continue
            seen.add(name)
            kind = "conditional" if binds[name] < lineno else "read-before-assignment"
            findings.append(f"{rel_path}::{qual}::{name}::{kind}")

    return findings


def _locals_scope_files() -> list[Path]:
    files: list[Path] = []
    for pattern in LOCALS_SCOPE_GLOBS:
        files.extend(sorted(API_ROOT.glob(pattern)))
    for rel in LOCALS_SCOPE_FILES:
        p = API_ROOT / rel
        if p.exists():
            files.append(p)
    return [p for p in files if "__pycache__" not in p.parts]


def test_no_conditionally_assigned_locals():
    """A name that only exists on the happy path stops existing on the other one."""
    found: Counter = Counter()
    for path in _locals_scope_files():
        found.update(scan_source_for_conditional_locals(_read(path), _rel(path)))
    _compare_to_baseline(
        found,
        _load_baseline(LOCALS_BASELINE),
        "Conditionally assigned local",
        UNBOUND_LESSON.format(name="<name>"),
    )


# ══════════════════════════════════════════════════════════════════
# Rule 3 — early returns must persist what the client said
# ══════════════════════════════════════════════════════════════════

ORCHESTRATOR = API_ROOT / "app" / "pipeline" / "orchestrator.py"
STEP4_MARKER = "# ── STEP 4"
PERSIST_HELPERS = ("update_lead_from_entities", "_persist_unambiguous_correction")

PERSIST_LESSON = (
    "a branch that answers the client must first save what the client said "
    "(route, dates, passengers, cabin)."
)


def _step4_lineno(source: str) -> int:
    for i, line in enumerate(source.splitlines(), start=1):
        if STEP4_MARKER in line:
            return i
    raise AssertionError(
        f"Anchor {STEP4_MARKER!r} not found in {ORCHESTRATOR.name}. "
        "If the step was renamed, update STEP4_MARKER — do not delete this rule."
    )


def scan_source_for_unpersisted_early_returns(source: str, rel_path: str) -> list[str]:
    marker = _step4_lineno(source)
    tree = ast.parse(source)
    quals = _qualname_map(tree)

    # The rule belongs to the function that actually owns the pipeline steps.
    owner = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno < marker < (node.end_lineno or node.lineno):
                if owner is None or node.lineno > owner.lineno:
                    owner = node
    if owner is None:
        raise AssertionError(f"{STEP4_MARKER!r} is not inside any function in {rel_path}")

    parents: dict[int, ast.AST] = {}
    for node in ast.walk(owner):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    def persists_before(return_node: ast.Return) -> bool:
        """Does any block enclosing this return call a persistence helper first?"""
        cur: ast.AST | None = return_node
        while cur is not None:
            for sub in ast.walk(cur):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                    if name in PERSIST_HELPERS and sub.lineno < return_node.lineno:
                        return True
            if cur is owner:
                return False
            cur = parents.get(id(cur))
        return False

    findings: list[str] = []
    for node in ast.walk(owner):
        if not isinstance(node, ast.Return) or node.lineno >= marker:
            continue
        value = node.value
        if not (isinstance(value, ast.Call) and getattr(value.func, "id", getattr(value.func, "attr", "")) == "ChatResponse"):
            continue
        if not persists_before(node):
            func = quals.get(id(node), owner.name)
            findings.append(f"{rel_path}::{func}::early-return-without-persist")

    return findings


def test_early_returns_persist_client_input():
    """Answering the client is not the job. Keeping what they told us is."""
    found = Counter(
        scan_source_for_unpersisted_early_returns(_read(ORCHESTRATOR), _rel(ORCHESTRATOR))
    )
    _compare_to_baseline(
        found,
        _load_baseline(RETURNS_BASELINE),
        "Early return without persistence",
        PERSIST_LESSON,
    )


# ══════════════════════════════════════════════════════════════════
# The guard's own tests — synthetic sources, never the real tree
# ══════════════════════════════════════════════════════════════════

def _scan(tmp_path, code: str, rel: str = "app/services/thing.py"):
    f = tmp_path / "sample.py"
    f.write_text(code, encoding="utf-8")
    return scan_file_for_silent_excepts(f, rel)


class TestTheGuardItself:
    def test_bare_pass_is_caught(self, tmp_path):
        found, _ = _scan(tmp_path, "def f():\n    try:\n        g()\n    except Exception:\n        pass\n")
        assert found == ["app/services/thing.py::f::pass"]

    def test_a_waiver_clears_it_and_is_counted(self, tmp_path):
        found, waivers = _scan(
            tmp_path,
            "def f():\n    try:\n        g()\n    except Exception:  # noqa: silent — harmless cache warm-up\n        pass\n",
        )
        assert found == []
        assert waivers == ["app/services/thing.py::f — harmless cache warm-up"]

    def test_a_waiver_on_the_first_body_line_also_counts(self, tmp_path):
        found, waivers = _scan(
            tmp_path,
            "def f():\n    try:\n        g()\n    except Exception:\n        pass  # noqa: silent — harmless cache warm-up\n",
        )
        assert found == [] and len(waivers) == 1

    def test_a_waiver_without_a_real_reason_fails(self, tmp_path):
        found, waivers = _scan(
            tmp_path,
            "def f():\n    try:\n        g()\n    except Exception:  # noqa: silent — meh\n        pass\n",
        )
        assert found == ["app/services/thing.py::f::waiver-without-a-reason"]
        assert waivers == []

    def test_logging_the_exception_passes(self, tmp_path):
        found, _ = _scan(
            tmp_path,
            'def f():\n    try:\n        g()\n    except Exception as e:\n        logger.warning(f"x: {e}")\n',
        )
        assert found == []

    def test_logging_without_the_exception_fails(self, tmp_path):
        found, _ = _scan(
            tmp_path,
            'def f():\n    try:\n        g()\n    except Exception as e:\n        logger.warning("failed")\n',
        )
        assert found == ["app/services/thing.py::f::log-without-exception"]

    def test_exc_info_is_enough(self, tmp_path):
        found, _ = _scan(
            tmp_path,
            'def f():\n    try:\n        g()\n    except Exception:\n        logger.error("failed", exc_info=True)\n',
        )
        assert found == []

    def test_debug_is_silence_in_the_money_packages(self, tmp_path):
        code = 'def f():\n    try:\n        g()\n    except Exception as e:\n        logger.debug(f"x: {e}")\n'
        assert _scan(tmp_path, code, "app/services/thing.py")[0] == [
            "app/services/thing.py::f::debug-only-log"
        ]
        assert _scan(tmp_path, code, "app/pipeline/thing.py")[0]
        assert _scan(tmp_path, code, "app/api/thing.py")[0]

    def test_debug_is_a_real_log_in_scripts(self, tmp_path):
        found, _ = _scan(
            tmp_path,
            'def f():\n    try:\n        g()\n    except Exception as e:\n        logger.debug(f"x: {e}")\n',
            "scripts/backfill.py",
        )
        assert found == []

    def test_suppress_is_the_same_silence_with_better_manners(self, tmp_path):
        code = "def f():\n    with contextlib.suppress(KeyError):\n        g()\n"
        assert _scan(tmp_path, code, "app/pipeline/thing.py")[0] == [
            "app/pipeline/thing.py::f::suppress"
        ]

    def test_suppress_outside_the_money_packages_is_left_alone(self, tmp_path):
        code = "def f():\n    with contextlib.suppress(KeyError):\n        g()\n"
        assert _scan(tmp_path, code, "app/utils/thing.py")[0] == []

    def test_return_sentinels_are_caught(self, tmp_path):
        for value in ("None", "False", "{}", "[]", '""', "0"):
            code = f"def f():\n    try:\n        g()\n    except Exception:\n        return {value}\n"
            assert _scan(tmp_path, code)[0] == ["app/services/thing.py::f::return-sentinel"], value

    def test_reraising_passes(self, tmp_path):
        found, _ = _scan(tmp_path, "def f():\n    try:\n        g()\n    except Exception:\n        raise\n")
        assert found == []

    def test_raise_from_passes(self, tmp_path):
        found, _ = _scan(
            tmp_path,
            "def f():\n    try:\n        g()\n    except Exception as e:\n        raise Other('x') from e\n",
        )
        assert found == []

    def test_raise_that_loses_the_cause_is_caught(self, tmp_path):
        found, _ = _scan(
            tmp_path,
            "def f():\n    try:\n        g()\n    except Exception as e:\n        raise Other('x')\n",
        )
        assert found == ["app/services/thing.py::f::raise-without-from"]

    def test_a_handler_that_does_work_but_never_tells_anyone(self, tmp_path):
        found, _ = _scan(
            tmp_path,
            "def f():\n    try:\n        g()\n    except Exception:\n        cache.clear()\n        return fallback()\n",
        )
        assert found == ["app/services/thing.py::f::body"]

    # ── Rule 2 ────────────────────────────────────────────────

    def test_a_local_assigned_only_in_an_if_is_caught(self):
        found = scan_source_for_conditional_locals(
            "def f(flag):\n    if flag:\n        x = 1\n    return x\n", "app/pipeline/p.py"
        )
        assert found == ["app/pipeline/p.py::f::x::conditional"]

    def test_initialising_at_the_top_clears_it(self):
        found = scan_source_for_conditional_locals(
            "def f(flag):\n    x = None\n    if flag:\n        x = 1\n    return x\n", "app/pipeline/p.py"
        )
        assert found == []

    def test_reading_before_the_first_assignment_is_caught(self):
        found = scan_source_for_conditional_locals(
            "def f():\n    y = x\n    x = 1\n    return y\n", "app/pipeline/p.py"
        )
        assert "app/pipeline/p.py::f::x::read-before-assignment" in found

    def test_loop_targets_and_comprehensions_are_not_the_rule(self):
        found = scan_source_for_conditional_locals(
            "def f(items):\n    for i in items:\n        pass\n    return [j for j in items] and i\n",
            "app/pipeline/p.py",
        )
        assert found == []

    def test_a_with_binding_is_not_the_rule(self):
        found = scan_source_for_conditional_locals(
            "def f(p):\n    with open(p) as fh:\n        data = fh.read()\n    return data\n",
            "app/pipeline/p.py",
        )
        assert found == []

    def test_globals_are_not_locals(self):
        found = scan_source_for_conditional_locals(
            "def f(flag):\n    global x\n    if flag:\n        x = 1\n    return x\n", "app/pipeline/p.py"
        )
        assert found == []

    def test_a_nested_def_keeps_its_own_scope(self):
        found = scan_source_for_conditional_locals(
            "def f(flag):\n    def g():\n        if flag:\n            y = 1\n        return y\n    return g\n",
            "app/pipeline/p.py",
        )
        assert found == ["app/pipeline/p.py::f.g::y::conditional"]

    # ── Baseline mechanics ────────────────────────────────────

    def test_a_new_finding_fails_with_instructions(self):
        with pytest.raises(pytest.fail.Exception) as exc:
            _compare_to_baseline(
                Counter(["app/pipeline/orchestrator.py::_pipeline::pass"]),
                Counter(),
                "Silent exception handler",
                HOW_TO_FIX_SILENT,
            )
        text = str(exc.value)
        assert "app/pipeline/orchestrator.py::_pipeline::pass" in text
        assert "Two ways to fix" in text
        assert "Never edit the baseline by hand" in text

    def test_a_stale_baseline_line_fails_with_the_cleanup_message(self):
        with pytest.raises(pytest.fail.Exception) as exc:
            _compare_to_baseline(
                Counter(),
                Counter(["app/services/gone.py::f::pass"]),
                "Silent exception handler",
                HOW_TO_FIX_SILENT,
            )
        assert "no longer exist" in str(exc.value)
        assert "may only ever shrink" in str(exc.value)

    def test_the_total_may_not_grow_even_for_a_known_line(self):
        with pytest.raises(pytest.fail.Exception):
            _compare_to_baseline(
                Counter({"app/services/x.py::f::pass": 2}),
                Counter({"app/services/x.py::f::pass": 1}),
                "Silent exception handler",
                HOW_TO_FIX_SILENT,
            )

    def test_the_scan_gives_the_same_answer_twice(self):
        """A guard whose answer drifts between runs is worse than no guard:
        it fails on someone else's pull request and teaches them the check is
        noise. Both scans are pure functions of the source — prove it."""
        first = [
            scan_source_for_conditional_locals(_read(p), _rel(p))
            for p in _locals_scope_files()
        ]
        second = [
            scan_source_for_conditional_locals(_read(p), _rel(p))
            for p in _locals_scope_files()
        ]
        assert first == second
        assert collect_silent_excepts()[0] == collect_silent_excepts()[0]

    def test_the_baselines_exist_and_say_when_they_were_frozen(self):
        for path in (SILENT_BASELINE, LOCALS_BASELINE, RETURNS_BASELINE):
            assert path.exists(), f"missing baseline: {path}"
            head = _read(path).splitlines()[0]
            assert head.startswith("#"), f"{path.name} must open with a comment saying when and why"
