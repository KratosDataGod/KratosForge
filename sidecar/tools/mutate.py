"""Mutation testing for one engine module (docs/quality/mutation-testing.md).

A developer tool, run by hand and never in CI. It uses only the standard library and pytest, and the
engine never imports it. Each run works in a temporary copy of sidecar/, so a run in progress (or
one stopped halfway) can't leave a mutant in your working tree, and several modules can run at once.

    cd sidecar
    python tools/mutate.py fabric_desktop/retrying.py --out retrying.json -- \\
        tests/test_retrying.py tests/test_retrying_mutation.py
    python tools/mutate.py fabric_desktop/redact.py --entries _SECRETS --out redact.json -- \\
        tests/test_redact.py

Point mode (the default) rewrites the module with one change at a time: a comparison flipped
(== and !=, < and >=, > and <=, is and is not, in and not in), and/or swapped, an if, while or
conditional expression negated, an integer or boolean constant changed (0 and 1 swapped, n to n+1,
True and False), a return value dropped to None, a raise removed, or + and -, * and // swapped.
Docstrings are never changed. At most --cap mutants are run (150), sampled with --seed (7), so a
re-run with the same seed mutates the same points; --only runs just the mutants with those indexes
again (to re-check survivors once tests are added). --entries NAME instead deletes each entry of the
module-level list or tuple NAME in turn, for a table of patterns the point mutations can't reach.

For each mutant the given tests run with pytest -x and a 120 s timeout. A failing run is "killed",
a passing one "survived", and a run that hangs "timeout" (stopped along with every process it
started), which counts as killed in the score. Each mutant prints
`<index> <line> <kind> <change> <status>`, and --out gets the totals and every result.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SIDECAR = Path(__file__).resolve().parents[1]
TIMEOUT = 120
KILL_WAIT = 30
# Not copied into the temporary sidecar/: caches at any depth, and a local virtual environment and
# build output only at the top of sidecar/, so a folder of the same name deeper down (a test
# fixture, say) is still copied.
IGNORE = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "*.egg-info")
IGNORE_TOP = {".venv", "venv", "build", "dist", ".coverage", "htmlcov"}


def _ignore(folder: str, names: list[str]) -> set[str]:
    skip = set(shutil.ignore_patterns(*IGNORE)(folder, names))
    if Path(folder).resolve() == SIDECAR:
        skip |= IGNORE_TOP & set(names)
    return skip

CMP_SWAP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.Gt: ast.LtE,
            ast.LtE: ast.Gt, ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn, ast.NotIn: ast.In}
BIN_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv, ast.FloorDiv: ast.Mult}


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def points(tree: ast.AST) -> list[tuple[str, ast.AST, int | None]]:
    """Every place a mutant can be made, in ast.walk order (the order --seed samples from)."""
    skip = _docstring_ids(tree)
    out: list[tuple[str, ast.AST, int | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            out.extend(("cmp", node, i) for i, op in enumerate(node.ops) if type(op) in CMP_SWAP)
        elif isinstance(node, ast.BoolOp):
            out.append(("boolop", node, None))
        elif isinstance(node, (ast.If, ast.While, ast.IfExp)):
            out.append(("negate", node, None))
        elif isinstance(node, ast.Constant) and id(node) not in skip:
            if isinstance(node.value, (bool, int)):
                out.append(("const", node, None))
        elif isinstance(node, ast.Return) and node.value is not None and not (
                isinstance(node.value, ast.Constant) and node.value.value is None):
            out.append(("return", node, None))
        elif isinstance(node, ast.Raise):
            out.append(("raise", node, None))
        elif isinstance(node, ast.BinOp) and type(node.op) in BIN_SWAP:
            out.append(("binop", node, None))
    return out


class _DropRaise(ast.NodeTransformer):
    def __init__(self, target: ast.AST) -> None:
        self.target = target

    def visit_Raise(self, node: ast.Raise) -> ast.AST:
        return ast.Pass() if node is self.target else node


def mutate(tree: ast.Module, kind: str, node: ast.AST, i: int | None) -> tuple[ast.Module, str]:
    """Apply one mutation to `tree` (in place where it can) and describe it."""
    if kind == "cmp":
        node.ops[i] = CMP_SWAP[type(node.ops[i])]()  # type: ignore[attr-defined,index]
        return tree, f"compare op {i} swapped"
    if kind == "boolop":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()  # type: ignore[attr-defined]
        return tree, "and/or swapped"
    if kind == "negate":
        test = node.test  # type: ignore[attr-defined]
        node.test = test.operand if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) \
            else ast.UnaryOp(op=ast.Not(), operand=test)  # type: ignore[attr-defined]
        return tree, "condition negated"
    if kind == "const":
        v = node.value  # type: ignore[attr-defined]
        node.value = (not v) if isinstance(v, bool) else (1 if v == 0 else 0 if v == 1 else v + 1)  # type: ignore[attr-defined]
        return tree, f"constant {v!r} -> {node.value!r}"  # type: ignore[attr-defined]
    if kind == "return":
        node.value = ast.Constant(value=None)  # type: ignore[attr-defined]
        return tree, "return value dropped"
    if kind == "raise":
        return _DropRaise(node).visit(tree), "raise removed"
    node.op = BIN_SWAP[type(node.op)]()  # type: ignore[attr-defined]
    return tree, "arithmetic op swapped"


def _list_node(tree: ast.Module, name: str) -> ast.List | ast.Tuple:
    for node in tree.body:
        targets = [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
        if any(getattr(t, "id", None) == name for t in targets) and isinstance(node.value, (ast.List, ast.Tuple)):
            return node.value
    raise SystemExit(f"no module-level list or tuple named {name}")


def _run_tests(work: Path, tests: list[str]) -> str:
    env = {**os.environ, "PYTHONPATH": str(work)}
    # Its own process group (a new console group on Windows), so a timeout stops everything pytest
    # started too, such as an engine or kernel process, not just pytest.
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests],
                            cwd=work, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **group)
    try:
        code = proc.wait(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return "timeout"
    return "survived" if code == 0 else "killed"


def _kill_group(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
        try:
            subprocess.run([taskkill, "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False,
                           timeout=KILL_WAIT)
        except subprocess.TimeoutExpired:
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=KILL_WAIT)
    except subprocess.TimeoutExpired:  # the kill failed: stop pytest itself and move on
        proc.kill()
        try:
            proc.wait(timeout=KILL_WAIT)
        except subprocess.TimeoutExpired:
            print(f"pytest {proc.pid} would not stop; moving on", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--" not in argv:
        raise SystemExit("usage: mutate.py MODULE [--cap N] [--seed N] [--entries NAME] --out FILE -- TESTS...")
    split = argv.index("--")
    ap = argparse.ArgumentParser(description="Mutation testing for one engine module.")
    ap.add_argument("module", help="path under sidecar/, such as fabric_desktop/retrying.py")
    ap.add_argument("--cap", type=int, default=150)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--entries", help="delete each entry of this module-level list instead")
    ap.add_argument("--only", help="comma-separated mutant indexes (the first number of each line) to run again")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv[:split])
    tests = argv[split + 1:]
    out_path = Path(args.out).resolve()

    with tempfile.TemporaryDirectory(prefix="mutate-") as tmp:
        work = Path(tmp) / "sidecar"
        shutil.copytree(SIDECAR, work, ignore=_ignore)
        target = work / args.module
        src = target.read_text(encoding="utf-8")
        base = ast.parse(src)
        results = []
        if args.entries:
            n = len(_list_node(base, args.entries).elts)
            plan = list(range(n))
        else:
            n = len(points(base))
            idx = list(range(n))
            random.Random(args.seed).shuffle(idx)
            plan = sorted(idx[:args.cap])
        if args.only:
            wanted = {int(x) for x in args.only.split(",")}
            missing = sorted(wanted - set(plan))
            if missing:
                print(f"--only: not in this run's plan (check --cap, --seed or --entries): "
                      f"{', '.join(map(str, missing))}", file=sys.stderr, flush=True)
            plan = [k for k in plan if k in wanted]
        for k in plan:
            tree = copy.deepcopy(base)
            if args.entries:
                elts = _list_node(tree, args.entries).elts
                kind, line, desc = "entry", elts[k].lineno, f"entry {k} deleted"
                del elts[k]
            else:
                kind, node, i = points(tree)[k]
                line = getattr(node, "lineno", 0)
                tree, desc = mutate(tree, kind, node, i)
            ast.fix_missing_locations(tree)
            target.write_text(ast.unparse(tree), encoding="utf-8")
            t0 = time.time()
            status = _run_tests(work, tests)
            results.append({"k": k, "line": line, "kind": kind, "desc": desc, "status": status,
                            "secs": round(time.time() - t0, 1)})
            print(k, line, kind, desc, status, flush=True)
            target.write_text(src, encoding="utf-8")

    summary = {"module": args.module, "points": n, "run": len(results),
               "killed": sum(r["status"] == "killed" for r in results),
               "survived": sum(r["status"] == "survived" for r in results),
               "timeout": sum(r["status"] == "timeout" for r in results), "results": results}
    out_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print({k: v for k, v in summary.items() if k != "results"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
