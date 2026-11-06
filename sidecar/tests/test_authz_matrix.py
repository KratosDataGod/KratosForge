"""Every platform operation that changes something decides who may make it (docs/audits/AUTHORIZATION_MATRIX.md).

A new operation either calls a check (require_admin, require_member, an owner or author check, is_admin)
or is added below with the reason anyone may make it, in .github/scripts/authz_matrix.py too."""

import ast
import functools
import inspect
from pathlib import Path

from fabric_desktop.central import core

OPEN_BY_DESIGN = {"call_once", "claim_exception_warning", "ensure_policy_schema", "ensure_schema",
                  "ensure_template_schema", "follow_product", "unfollow_product", "log_workload_action",
                  "note_seen", "request"}
GUARD_WORDS = ("require", "is_admin", "_product_owners", "_shortcut_changer", "_can_", "_may_", "owner", "author")


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
