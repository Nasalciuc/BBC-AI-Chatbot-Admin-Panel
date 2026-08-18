"""The pipeline must not depend on which branch a turn happened to take.

18 Aug 2026: PR #204 rewrote the block above Step 3.45 and deleted five
initialisers along with it. Step 4 reads `_correction_context` on EVERY turn,
so every ordinary message — the whole chatbot — died with

    UnboundLocalError: cannot access local variable '_correction_context'

before the client got a single word back. The five names are set only inside
the post-summary correction branch, which almost no turn enters.

This is the second time the same shape has cost us an outage (17 Aug, five
UnboundLocalError in the same function, ten hours of template replies). So
the contract is pinned in two places: here, cheaply and by name, and
structurally in tests/test_code_discipline.py.
"""

import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ORCHESTRATOR = Path(__file__).resolve().parent.parent / "app" / "pipeline" / "orchestrator.py"

# Set inside the post-summary correction branch, read unconditionally later.
BRANCH_SET_BUT_ALWAYS_READ = (
    "_correction_context",
    "_clear_return_date",
    "_suppress_return_date",
    "_multi_city_declared",
    "_suppress_leg_fields",
    "_awaiting_correction_directive",
)


def _pipeline_node() -> ast.AST:
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_pipeline":
            return node
    raise AssertionError("_pipeline not found in orchestrator.py")


def test_correction_state_is_initialised_at_function_level():
    """Not in a branch. At the top, where every path can see it."""
    fn = _pipeline_node()
    top_level_bindings = set()
    for stmt in fn.body:
        targets = []
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets = [stmt.target]
        for t in targets:
            if isinstance(t, ast.Name):
                top_level_bindings.add(t.id)

    missing = [n for n in BRANCH_SET_BUT_ALWAYS_READ if n not in top_level_bindings]
    assert not missing, (
        f"{missing} are set only inside the post-summary correction branch but read on "
        "every turn. Initialise them at the top of `_pipeline` with a neutral default. "
        "Deleting these lines took the whole chatbot down on 18 Aug 2026 (#204)."
    )


def test_step_4_still_reads_the_name_this_test_protects():
    """If Step 4 ever stops reading it, this test is guarding nothing —
    fail loudly rather than pass for the wrong reason."""
    src = ORCHESTRATOR.read_text(encoding="utf-8")
    step4 = src.index("# ── STEP 4")
    after = src[step4 : step4 + 2000]
    assert "_correction_context" in after, (
        "Step 4 no longer reads _correction_context. Re-point this regression test at "
        "whatever it reads now, or delete it deliberately — do not leave it green and idle."
    )
