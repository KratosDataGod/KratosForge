"""WR-209: every way to change a warehouse goes through the confirmed name and the audit line written first.

A warehouse change has one way in: the ``sql_start`` RPC with ``allow=["warehouse_write"]``. SqlExecutions
runs it in sql_worker, the only code that opens a connection to a warehouse. The shell sends that call from
two places, the Warehouse page's Run and a deploy, and each one asks for the warehouse's name to be typed
in Prod (and in a workspace with no environment, which counts as Prod).

This file lists those paths and fails in these cases:
- A path reaches a change without ``confirm`` and the confirmed name.
- A change can start without the fail-closed "started" audit line.
- A gate in front of a path loses its typed name.
- A new path appears that isn't listed.

To add a path, put it in WRITE_PATHS or UI_WRITE_PATHS with the gate it goes through, and give that gate
a test. The browser tests named here are what prove each dialog's behaviour, and this file checks they're
still there.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from fabric_desktop import audit, server, sql_exec
from tests.test_warehouse_queries import SIDECAR, FakeFabric, limits

PACKAGE = SIDECAR / "fabric_desktop"
UI = SIDECAR.parent / "app" / "ui"
BROWSER_TESTS = SIDECAR.parent / "app" / "tests" / "browser"

# Engine code that starts a SQL run, by (file, function). Only sql_start can make it a warehouse change.
WRITE_PATHS = {
    ("service.py", "sql_start"): "the RPC: a change needs allow, confirm and confirm_name, checked by SqlExecutions.start",
}
# Callers that start a run that can never be a warehouse change, with why: each start() in them names a
# target other than the warehouse (or none, which is the lakehouse) and allows nothing beyond reading.
NOT_WRITE_PATHS = {
    ("localdata_service.py", "local_query_start"): "a local data project's own tables (target='local')",
    ("pipeline_script.py", "_policy"): "a pipeline script's dry run, on a local project or a lakehouse",
    ("pipeline_script.py", "_block"): "a pipeline script's block, on a local project or a lakehouse, read_only",
}
# Where the shell sends a warehouse change (a sql_start with warehouse_write that isn't a dry run), by
# (file, function that sends it), with the typed-name gate in front of it and the browser test of that gate.
UI_WRITE_PATHS = {
    ("query.js", "run"): ("whChangeConfirm", "warehouse-changes.test.cjs",
                          ("in Prod the warehouse's name has to be typed",
                           "a workspace with no environment is treated like Prod, whatever its name says",
                           "a workspace Dev and Prod share counts as Prod")),
    ("whproject.js", "whRunChange"): ("whProjectDeploy", "warehouse-deploy.test.cjs",
                                      ("Prod deploys only what's committed and pushed, and asks for the name",)),
}

CHANGE = {"query": "UPDATE dbo.sales SET a = 1", "target": "warehouse", "dialect": "tsql",
          "warehouse_ref": {"workspace_id": "ws", "item_id": "wh1", "kind": "warehouse"},
          "allow": ["warehouse_write"], "confirm": True, "confirm_name": "Sales WH"}
# Each kind of change the engine runs: a script of batches (WR-207), a script wrapped in its own
# transaction (WR-204) and a deploy's change with the object as its preview saw it (WH-13).
SHAPES = {
    "batches": {"query": "UPDATE dbo.sales SET a = 1\nGO\nUPDATE dbo.sales SET b = 2"},
    "wrapped": {"query": "BEGIN TRAN\nUPDATE dbo.sales SET a = 1\nCOMMIT"},
    "deploy": {"query": "CREATE SCHEMA gold", "precondition": {"kind": "schema", "schema": "gold", "name": "gold",
                                                               "exists": False}},
}


def _modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        if "_vendor" not in path.parts:
            yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _name(path: Path) -> str:
    return path.relative_to(PACKAGE).as_posix()


def _enclosing(tree: ast.AST):
    """Each call with the function it's in."""
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    yield fn.name, node


def test_only_the_worker_opens_a_connection_to_a_warehouse():
    found = set()
    for path, tree in _modules():
        if path.name == "warehouse.py":
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in {"run", "connect", "open_connection"} \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "warehouse":
                found.add(_name(path))
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("warehouse") \
                    and {a.name for a in node.names} & {"run", "connect", "open_connection"}:
                found.add(_name(path))
    assert found == {"sql_worker.py"}, "a new way to reach a warehouse: list it in WRITE_PATHS with its gate"


def test_only_sql_exec_starts_the_worker():
    found = set()
    for path, tree in _modules():
        docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs \
                    and "sql_worker" in node.value:
                found.add(_name(path))
            if isinstance(node, (ast.Import, ast.ImportFrom)) and path.name != "sql_worker.py" \
                    and any("sql_worker" in (a.name or "") or "sql_worker" in (getattr(node, "module", "") or "")
                            for a in node.names):
                found.add(_name(path))
    assert found == {"sql_exec.py"}


def _run_starts(tree: ast.AST):
    """Each function that starts a SQL run (a ``.start(`` on ``sql_runs``, or on a name taken from it), with those calls."""
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and "sql_runs" in ast.unparse(fn):
            calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                     and c.func.attr == "start" and "runs" in ast.unparse(c.func.value)]
            if calls:
                yield fn.name, calls


def _never_a_change(call: ast.Call) -> bool:
    words = {k.arg: k.value for k in call.keywords}
    target = words.get("target")
    allow = words.get("allow", call.args[3] if len(call.args) > 3 else None)
    target_ok = target is None or (isinstance(target, ast.Constant) and target.value in ("local", "lakehouse"))
    allow_ok = allow is None or (isinstance(allow, ast.Constant) and allow.value is None) or (
        isinstance(allow, ast.List) and all(isinstance(e, ast.Constant) and e.value == "read_only" for e in allow.elts))
    return target_ok and allow_ok and None not in words  # no **kwargs that could carry either


def test_every_caller_that_starts_a_sql_run_is_listed():
    found = {}
    for path, tree in _modules():
        if path.name == "sql_exec.py":
            continue
        for fn, calls in _run_starts(tree):
            found[(path.name, fn)] = calls
    assert set(found) == set(WRITE_PATHS) | set(NOT_WRITE_PATHS), \
        "a new caller of sql_runs.start: list it in WRITE_PATHS with its gate, or in NOT_WRITE_PATHS with why"
    for key in NOT_WRITE_PATHS:
        assert all(_never_a_change(c) for c in found[key]), (key, [ast.unparse(c) for c in found[key]])


def test_sql_start_is_the_only_rpc_and_nothing_from_outside_the_shell_reaches_it():
    assert "sql_start" in server.METHODS and audit.audited("sql_start")
    for path in sorted((PACKAGE / "integrations").glob("*.py")):  # MCP, the local HTTP API, extensions
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"sql_start|sql_runs|warehouse_write|SqlExecutions", text), path.name
    for name in ("cli.py", "vscode_setup.py"):
        assert not re.search(r"sql_start|sql_runs|warehouse_write", (PACKAGE / name).read_text(encoding="utf-8")), name


@pytest.mark.parametrize("params", [
    CHANGE,
    {k: v for k, v in CHANGE.items() if k not in ("confirm", "confirm_name")},  # refused later, still recorded first
    CHANGE | SHAPES["deploy"],
    {"query": "x", "allow": ["warehouse_write"]},
])
def test_a_change_always_fails_closed(params):
    # A change needs allow (sql_exec: change = target warehouse and warehouse_write in allow), which is a guard parameter.
    assert "allow" in audit.GUARD_PARAMS and {"confirm", "confirm_name"} <= audit.GUARD_PARAMS
    assert audit.fails_closed("sql_start", params)


class _Refusing:
    def record(self, *a, **k):
        raise OSError(28, "No space left on device")


class _Service:
    def __init__(self, manager):
        self.audit, self.sql_runs = _Refusing(), manager

    def sql_start(self, query, lakehouse_ref=None, dialect="spark", allow=None, target="lakehouse", project="",
                  confirm=False, dry_run=False, warehouse_ref=None, confirm_name="", estimated_plan=False,
                  precondition=None, profile=False):
        return self.sql_runs.start(query, lakehouse_ref, dialect, allow, target=target, confirm=confirm,
                                   dry_run=dry_run, warehouse_ref=warehouse_ref, confirm_name=confirm_name,
                                   precondition=precondition)


@pytest.fixture
def manager(tmp_path):
    fabric = FakeFabric()
    from fabric_desktop import warehouse
    runs = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "STORAGE", audit=lambda *a: None, limits=limits(),
                                  command=["false"], warehouse_token=lambda: pytest.fail("asked for a token"),
                                  resolve_warehouse=lambda ref: warehouse.resolve(fabric, ref))
    runs.fabric = fabric
    yield runs
    runs.shutdown()


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_change_whose_audit_line_cant_be_written_never_starts(manager, shape):
    reply = server.handle(_Service(manager), json.dumps({"id": 1, "method": "sql_start", "params": CHANGE | SHAPES[shape]}))
    assert "error" in reply and "audit log couldn't be written, so this didn't run" in reply["error"]
    assert manager.recent() == [] and manager.fabric.calls == []


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("missing", [{"confirm": False}, {"confirm_name": ""}, {"confirm_name": "   "}, {"confirm_name": None}])
def test_every_kind_of_change_needs_the_confirm_and_the_name(manager, shape, missing):
    params = (CHANGE | SHAPES[shape] | missing)
    params.pop("query")
    with pytest.raises(PermissionError, match="after you confirm it, naming the warehouse"):
        manager.start((CHANGE | SHAPES[shape])["query"], None, "tsql", **{k: v for k, v in params.items() if k != "dialect"})
    assert manager.recent() == [] and manager.fabric.calls == []


def test_the_engine_tests_of_the_name_are_still_there():
    text = (SIDECAR / "tests" / "test_warehouse_queries.py").read_text(encoding="utf-8")
    for name in ("test_a_change_needs_its_confirm_and_the_warehouses_name",
                 "test_a_change_named_for_another_warehouse_is_never_sent",
                 "test_a_run_that_wasnt_started_as_a_change_still_only_reads",
                 "test_the_worker_checks_again_and_never_connects_for_a_write"):
        assert f"def {name}(" in text, name


# ---------------------------------------------------------------- the shell


def _call_text(src: str, start: int) -> str:
    """The text of the call that opens at ``start`` (its "("), to its matching ")"."""
    depth = 0
    for i in range(start, len(src)):
        depth += {"(": 1, ")": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
    raise AssertionError("unbalanced call")


def _functions_at(src: str, offset: int) -> list[str]:
    """The named functions around ``offset``, outermost first."""
    return [m.group(1) for m in re.finditer(r"function\s+(\w+)\s*\(", src)
            if m.start() < offset < m.start() + len(_body(src, m.group(1), m))]


def _body(src: str, name: str, m: re.Match[str] | None = None) -> str:
    """Function ``name`` from its keyword to its closing brace."""
    m = m or re.search(rf"function\s+{name}\s*\(", src)
    assert m, name
    params = _call_text(src, m.end() - 1)
    brace = src.index("{", m.end() - 1 + len(params))
    depth = 0
    for i in range(brace, len(src)):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[m.start():i + 1]
    raise AssertionError(name)


def _ui_sends():
    sends = {}
    for path in sorted(UI.glob("*.js")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"""rpc\(\s*["']sql_start["']""", src):
            call = _call_text(src, m.start() + 3)
            if "warehouse_write" in call and not re.search(r"dry_run:\s*true", call):
                sends[(path.name, _functions_at(src, m.start())[-1])] = call
    return sends


def test_every_place_the_shell_sends_a_change_is_listed():
    sends = _ui_sends()
    assert set(sends) == set(UI_WRITE_PATHS), \
        "a new place sends a warehouse change: list it in UI_WRITE_PATHS with its typed-name gate and its browser test"
    for call in sends.values():
        assert re.search(r"confirm:\s*true", call) and re.search(r"confirm_name:\s*ref\.name", call), call


def test_the_pages_run_sends_a_change_only_after_its_confirm():
    body = _body((UI / "query.js").read_text(encoding="utf-8"), "run")
    assert len(re.findall(r"\bchange\s*=\s*true", body)) == 1
    assert re.search(r"if \(!\(await whChangeConfirm\(ref, plan\)\)\) return;\s*change = true;", body)
    assert re.search(r"\.\.\.\(change \? \{ allow: \[\"warehouse_write\"\]", body)


def test_the_change_confirm_needs_the_typed_name_in_prod_and_with_no_environment():
    src = (UI / "sqlchanges.js").read_text(encoding="utf-8")
    assert re.search(r"const whNeedsName = \(ref\) => \[\"prod\", \"none\"\]\.includes\(whEnv\(ref\)\);", src)
    body = _body(src, "whChangeConfirm")
    assert "const named = whNeedsName(ref);" in body
    assert "go.disabled = named;" in body and "go.disabled = typed.value !== ref.name;" in body


def test_a_deploy_needs_the_typed_name_in_prod_and_with_no_environment():
    src = (UI / "whproject.js").read_text(encoding="utf-8")
    calls = [_functions_at(src, m.start()) for m in re.finditer(r"\bwhRunChange\(", src)]
    calls = [around for around in calls if around[-1:] != ["whRunChange"]]  # not its own declaration
    assert calls and all("whProjectDeploy" in around for around in calls), calls
    others = [p.name for p in UI.glob("*.js") if p.name != "whproject.js" and "whRunChange(" in p.read_text(encoding="utf-8")]
    assert others == []
    body = _body(src, "whProjectDeploy")
    assert 'const named = ["prod", "none"].includes(env);' in body
    assert re.search(r"go\.disabled = [^;]*\(named && typed\.value !== ref\.name\)", body)


@pytest.mark.parametrize("key", sorted(UI_WRITE_PATHS))
def test_each_gate_has_its_browser_test(key):
    gate, file, names = UI_WRITE_PATHS[key]
    text = (BROWSER_TESTS / file).read_text(encoding="utf-8")
    for name in names:
        assert f'test("{name}"' in text, (file, name)
