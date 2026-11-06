"""Every platform operation that changes something decides who may make it (docs/audits/AUTHORIZATION_MATRIX.md).

A new operation either calls a check (require_admin, require_member, an owner or author check, is_admin)
or is added to OPEN_BY_DESIGN in .github/scripts/authz_matrix.py with the reason anyone may make it."""

import ast
import contextlib
import functools
import importlib.util
import inspect
import io
from pathlib import Path

from fabric_desktop.central import core

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("authz_matrix", ROOT / ".github" / "scripts" / "authz_matrix.py")
authz_matrix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(authz_matrix)

OPEN_BY_DESIGN = set(authz_matrix.OPEN_BY_DESIGN)
GUARD_WORDS = authz_matrix.GUARD_WORDS


@functools.cache
def _functions(path: str) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in ast.parse(Path(path).read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)}


def _checks(path: str, name: str, seen: set) -> bool:
    fns = _functions(path)
    if (path, name) in seen or name not in fns or len(seen) > 200:
        return False
    seen.add((path, name))
    for c in ast.walk(fns[name]):
        if isinstance(c, ast.Call):
            called = c.func.id if isinstance(c.func, ast.Name) else c.func.attr if isinstance(c.func, ast.Attribute) else ""
            if any(w in called for w in GUARD_WORDS) or (called in fns and _checks(path, called, seen)):
                return True
    return False


def test_every_changing_platform_operation_checks_who_makes_it():
    unchecked = set()
    for op, fn in core.OPERATIONS.items():
        if op in core.READ_OPERATIONS:
            continue
        fn = inspect.unwrap(fn)
        if not _checks(inspect.getsourcefile(fn), fn.__name__, set()):
            unchecked.add(op)
    assert unchecked == OPEN_BY_DESIGN, (
        f"New without a check: {sorted(unchecked - OPEN_BY_DESIGN)}; listed but now checked: {sorted(OPEN_BY_DESIGN - unchecked)}")


def test_the_open_ones_are_changes_only_the_caller_or_a_later_check_owns():
    for op in OPEN_BY_DESIGN:
        assert op in core.OPERATIONS and op not in core.READ_OPERATIONS


def test_the_committed_matrix_is_what_the_generator_writes():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        authz_matrix.main()
    doc = (ROOT / "docs" / "audits" / "AUTHORIZATION_MATRIX.md").read_text(encoding="utf-8")
    assert out.getvalue() == doc, ("docs/audits/AUTHORIZATION_MATRIX.md is out of date: from sidecar/, run "
                                   "python ../.github/scripts/authz_matrix.py > ../docs/audits/AUTHORIZATION_MATRIX.md")
