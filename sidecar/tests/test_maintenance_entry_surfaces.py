"""Every way into Optimize, Vacuum, Z-Order and Restore meets the capacity check: the app's pages, a
notebook (in the app's own kernel or a separate kernel process), an AI tool over MCP, and a schedule.

test_maintenance_entry_points lists the functions that start maintenance and each one's gate. This file
checks the routes to them, so a new route can't skip a gate:

- MCP and a schedule run in an engine ``Service``, which installs the notebook guard when it starts. The
  MCP server has no maintenance tool of its own. Its code tools (execute_python, run_notebook) run code
  that meets that guard, and always ask first.
- A kernel in another process asks the engine every time, and is refused when no engine answers.
- Maintain's job (``_maintain``) is reached only from table health and after a run. A scheduled metadata
  run reaches it only through ``_after_run``, which is gated.

No network and no Fabric. Nothing here claims a saving."""

import ast
import datetime as dt
from pathlib import Path

import pytest

from fabric_desktop import kernel_proc, lakehouse, risk
from fabric_desktop.integrations.registry import default_registry
from fabric_desktop.service import Service

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"
MAINTENANCE_WORDS = ("optimize", "vacuum", "zorder", "z_order", "compact", "maintain", "maintenance", "restore")


def _functions_calling(attr: str) -> set[tuple[str, str]]:
    """(file, function) for every function in the engine that calls ``<something>.attr(...)``."""
    found = set()
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == attr:
                        found.add((path.relative_to(PACKAGE).as_posix(), fn.name))
    return found


def _calls_service(path: str, function: str) -> bool:
    tree = ast.parse((PACKAGE / path).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Service" for n in ast.walk(fn))


# ---- Maintain's job: who can start it

def test_maintains_job_is_started_only_from_table_health_and_after_a_run():
    # spark_local's own ``_maintain`` is a notebook's DeltaTable API (vacuum(), restoreToVersion(), ...): it
    # goes to lakehouse.maintain_table and its guard, which test_maintenance_entry_points covers.
    notebook = {("spark_local.py", f) for f in ("_run", "vacuum", "restoreToVersion", "restoreToTimestamp")}
    assert _functions_calling("_maintain") - notebook == {("cloud_service.py", "meta_optimize"),
                                                          ("cloud_service.py", "_after_run")}
    assert {p for p, _f in _functions_calling("_maintain") & notebook} <= {"spark_local.py"}


def test_after_a_run_is_reached_only_from_the_two_run_paths():
    # meta_service._run_here: a run from the app or a schedule on this PC; runtime.run_environment: an agent's. Both are
    # metadata runs whose maintenance goes through _maintain's capacity gate.
    assert _functions_calling("_after_run") == {("meta_service.py", "_run_here"), ("runtime.py", "run_environment")}


def test_a_scheduled_metadata_run_goes_through_the_gated_run_path():
    tree = ast.parse((PACKAGE / "schedule_service.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_run_meta_here")
    called = {n.func.attr for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "_run_here" in called and not called & {"_maintain", "meta_optimize", "xray_optimize", "xray_vacuum"}


# ---- an engine Service installs the notebook guard; MCP and a schedule run in one

@pytest.mark.parametrize("path,function", [("integrations/mcp_server.py", "_engine_service"), ("cli.py", "_run_schedule")])
def test_mcp_and_a_schedule_run_in_an_engine_service(path, function):
    assert _calls_service(path, function)


@pytest.fixture
def svc(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(lakehouse, "maintenance_guard", None)  # restored after the test
    monkeypatch.setattr(lakehouse, "prod_guard", None)
    s = Service(root=tmp_path / "FabricDesktop")
    monkeypatch.setattr(s, "_start_retention_sweep", lambda: None, raising=False)
    monkeypatch.setattr(s, "_start_usage_send", lambda: None, raising=False)
    return s


def _paused():
    """A capacity read a minute ago as paused: check_capacity's Blocked."""
    from tests.test_capacity_hard_stop_matrix import _cap, read
    return read(capacities=_cap("Inactive"))


def test_starting_an_engine_service_installs_its_guard_and_a_blocked_capacity_refuses(svc, monkeypatch):
    guard = lakehouse.maintenance_guard
    assert guard is not None and getattr(guard, "__func__", None) is type(svc)._maintenance_refusal
    assert getattr(guard, "__self__", None) is svc
    monkeypatch.setattr(svc, "_project", lambda: (_ for _ in ()).throw(LookupError("no project")), raising=False)
    monkeypatch.setattr(svc, "_risk_capacity", lambda *a, **k: _paused(), raising=False)
    found = risk.check_capacity({"where": "fabric", "capacity": _paused()}, dt.datetime.now(dt.timezone.utc))
    assert found["outcome"] == risk.BLOCKED
    for op in ("optimize", "zorder", "vacuum", "restore"):
        reason = guard(op, "ws-dev", "dbo.orders")
        assert reason and "Blocked" in reason, op


def test_a_kernel_in_another_process_is_refused_when_no_engine_answers(monkeypatch):
    monkeypatch.setattr(lakehouse, "maintenance_guard", None)
    for op in ("optimize", "zorder", "vacuum", "restore"):
        assert kernel_proc._maintenance_refusal(op, "ws-dev", "dbo.orders") == lakehouse.MAINTENANCE_NEEDS_APP


def test_a_kernel_in_another_process_asks_the_engine_every_time(monkeypatch):
    asked = []
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda *a: asked.append(a) or "refused here")
    assert kernel_proc._maintenance_refusal("vacuum", "ws-dev", "dbo.orders") == "refused here"
    assert asked == [("vacuum", "ws-dev", "dbo.orders")]
    # and the kernel host's module hook sends each one to the engine over its channel, never answering itself
    src = (PACKAGE / "kernel_host.py").read_text(encoding="utf-8")
    assert 'self.channel.call("maintenance_guard", op, workspace_id, table)' in src


# ---- MCP: no maintenance tool; code tools ask first

def test_mcp_has_no_maintenance_tool():
    for cap in default_registry().all():
        handler = getattr(cap.handler, "__name__", "")
        names = f"{cap.id} {handler}".lower()
        assert not any(w in names for w in MAINTENANCE_WORDS), (cap.id, handler)


def test_mcp_tools_that_run_code_ask_first_and_write():
    runs = [c for c in default_registry().all() if getattr(c.handler, "__name__", "") in ("execute_python", "run_notebook")]
    assert len(runs) == 2
    for cap in runs:
        assert cap.approval == "always" and cap.read_only is False, cap.id
