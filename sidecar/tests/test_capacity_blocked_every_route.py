"""WR-410 / FINOPS-004: a capacity check of Blocked stops OPTIMIZE, ZORDER BY, VACUUM and RESTORE whichever way
notebook code asks for them on this PC, and whichever kernel runs that code.

test_capacity_hard_stop_matrix runs every capacity reading through every gate, and calls lakehouse.maintain_table
itself for the notebook's gate. This file runs the routes notebook code takes to it: Spark SQL through the local
Spark session (spark.sql, a %%sql cell and %sql), and delta-spark's DeltaTable API, each in the app's own kernel,
with every Blocked reading installed through MetaMixin._maintenance_refusal as the engine installs it. Each must be
refused, say Blocked, reach lakehouse.maintain_table (so its gate is the one that stops it), and leave the table's
version, its files on disk and its properties as they were. A kernel in another process asks the engine over its
channel, and gets the same refusal.

The last tests tie this to test_maintenance_entry_points.REVIEWED: every gated entry point there is run under
Blocked by a named test. No network, no Fabric."""

import ast
import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import kernel_host, kernel_proc, lakehouse, publish, risk
from fabric_desktop.audit import AuditLog
from fabric_desktop.meta_service import MetaMixin
from fabric_desktop.notebook import Kernel
from tests.test_capacity_hard_stop_matrix import STOPPED, classify, reader
from tests.test_maintenance_entry_points import REVIEWED
from tests.test_maintenance_entry_surfaces import svc  # noqa: F401 - a fixture
from tests.test_spark_writer import lake  # noqa: F401 - a fixture

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"
DT = "from delta.tables import DeltaTable\n"

# route -> (notebook code, the operation the guard is asked for, the table, what maintain_table is given)
ROUTES = {
    # Spark SQL through the local Spark session
    "sql_optimize": ("spark.sql('OPTIMIZE dbo.sales')", "optimize", "dbo.sales", {"operation": "optimize"}),
    "sql_optimize_zorder": ("spark.sql('OPTIMIZE dbo.sales ZORDER BY (id)')", "zorder", "dbo.sales",
                            {"operation": "optimize", "zorder": ["id"]}),
    "sql_optimize_where_partition": ("spark.sql(\"OPTIMIZE dbo.parts WHERE region = 'EU'\")", "optimize", "dbo.parts",
                                     {"operation": "optimize", "where": [("region", "=", "EU")]}),
    "sql_vacuum": ("spark.sql('VACUUM dbo.sales')", "vacuum", "dbo.sales", {"operation": "vacuum", "retain_hours": None}),
    "sql_vacuum_retain_200_hours": ("spark.sql('VACUUM dbo.sales RETAIN 200 HOURS')", "vacuum", "dbo.sales",
                                    {"operation": "vacuum", "retain_hours": 200.0}),
    "sql_vacuum_dry_run": ("spark.sql('VACUUM dbo.sales DRY RUN')", "vacuum", "dbo.sales",
                           {"operation": "vacuum", "dry_run": True}),
    "sql_restore_version": ("spark.sql('RESTORE TABLE dbo.sales TO VERSION AS OF 0')", "restore", "dbo.sales",
                            {"operation": "restore", "version": 0}),
    "sql_restore_timestamp": ("spark.sql(\"RESTORE dbo.sales TO TIMESTAMP AS OF '2020-01-01'\")", "restore",
                              "dbo.sales", {"operation": "restore"}),
    "sql_cell_optimize_zorder": ("%%sql\nOPTIMIZE dbo.sales ZORDER BY (id)", "zorder", "dbo.sales",
                                 {"operation": "optimize", "zorder": ["id"]}),
    "sql_cell_vacuum": ("%%sql\nVACUUM dbo.sales", "vacuum", "dbo.sales", {"operation": "vacuum"}),
    "sql_cell_restore": ("%%sql\nRESTORE TABLE dbo.sales TO VERSION AS OF 0", "restore", "dbo.sales",
                         {"operation": "restore", "version": 0}),
    "sql_line_magic_vacuum": ("r = %sql VACUUM dbo.sales RETAIN 200 HOURS", "vacuum", "dbo.sales",
                              {"operation": "vacuum", "retain_hours": 200.0}),
    # delta-spark's DeltaTable API (spark_local.LocalDeltaTable)
    "api_vacuum": (DT + "DeltaTable.forName(spark, 'dbo.sales').vacuum()", "vacuum", "dbo.sales",
                   {"operation": "vacuum", "retain_hours": None}),
    "api_vacuum_200": (DT + "DeltaTable.forName(spark, 'dbo.sales').vacuum(200)", "vacuum", "dbo.sales",
                       {"operation": "vacuum", "retain_hours": 200}),
    "api_vacuum_for_path": (DT + "DeltaTable.forPath(spark, 'Tables/dbo/sales').vacuum()", "vacuum", "dbo.sales",
                            {"operation": "vacuum"}),
    "api_compaction": (DT + "DeltaTable.forName(spark, 'dbo.sales').optimize().executeCompaction()", "optimize",
                       "dbo.sales", {"operation": "optimize", "zorder": None}),
    "api_zorder": (DT + "DeltaTable.forName(spark, 'dbo.sales').optimize().executeZOrderBy('id')", "zorder",
                   "dbo.sales", {"operation": "optimize", "zorder": ["id"]}),
    "api_where_partition": (DT + "DeltaTable.forName(spark, 'dbo.parts').optimize().where(\"region = 'EU'\")"
                            ".executeCompaction()", "optimize", "dbo.parts",
                            {"operation": "optimize", "where": [("region", "=", "EU")]}),
    "api_where_partition_zorder": (DT + "DeltaTable.forName(spark, 'dbo.parts').optimize().where(\"region = 'EU'\")"
                                   ".executeZOrderBy('id')", "zorder", "dbo.parts",
                                   {"operation": "optimize", "zorder": ["id"], "where": [("region", "=", "EU")]}),
    "api_restore_to_version": (DT + "DeltaTable.forName(spark, 'dbo.sales').restoreToVersion(0)", "restore",
                               "dbo.sales", {"operation": "restore", "version": 0}),
    "api_restore_to_timestamp": (DT + "DeltaTable.forName(spark, 'dbo.sales').restoreToTimestamp('2020-01-01')",
                                 "restore", "dbo.sales", {"operation": "restore"}),
}


def _mixin(name, audit=None):
    """The engine's notebook guard (MetaMixin._maintenance_refusal) over the matrix's reading ``name``."""
    m = SimpleNamespace(_project=lambda: SimpleNamespace(environments={}), audit=audit, _risk_capacity=reader(name))
    m._maintenance_capacity = lambda ws: MetaMixin._maintenance_capacity(m, ws)
    m._maintenance_capacity_refusal = lambda *a, **k: MetaMixin._maintenance_capacity_refusal(m, *a, **k)
    m._maintenance_audit = lambda *a: MetaMixin._maintenance_audit(m, *a)
    return m


def _state(path):
    """What a refusal must leave as it was: the version, the properties and every file, log included."""
    t = DeltaTable(path)
    files = sorted((p.relative_to(path).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                   for p in Path(path).rglob("*") if p.is_file())
    return t.version(), dict(t.metadata().configuration), files


@pytest.fixture(params=STOPPED)
def blocked(request, lake, tmp_path, monkeypatch):  # noqa: F811
    """dbo.sales in four commits and dbo.parts partitioned by region, the engine's guard reading a Blocked capacity,
    and every call of lakehouse.maintain_table recorded before it runs."""
    expected, title = classify(request.param)
    assert expected == risk.BLOCKED
    m = _mixin(request.param, AuditLog(tmp_path / "audit"))
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: MetaMixin._maintenance_refusal(m, op, ws, t))
    for i in range(3):
        write_deltalake(str(lake / "dbo" / "sales"), pa.table({"id": [10 + i], "region": ["EU"]}), mode="append")
    for region in ("EU", "EU", "US"):
        write_deltalake(str(lake / "dbo" / "parts"), pa.table({"id": [1], "region": [region]}), mode="append",
                        partition_by=["region"])
    calls, real = [], lakehouse.maintain_table
    monkeypatch.setattr(lakehouse, "maintain_table", lambda *a, **k: calls.append((a, k)) or real(*a, **k))
    tables = {t: str(lake / "dbo" / t.split(".")[1]) for t in ("dbo.sales", "dbo.parts")}
    return SimpleNamespace(title=title, audit=m.audit, calls=calls, tables=tables, maintain_table=real,
                           before={t: _state(p) for t, p in tables.items()})


def _refused_as_blocked(blocked, text):
    assert "capacity check is Blocked" in text and blocked.title in text and "Nothing was changed" in text, text


def _unchanged(blocked):
    assert {t: _state(p) for t, p in blocked.tables.items()} == blocked.before


@pytest.mark.parametrize("route", list(ROUTES))
def test_notebook_code_is_refused_by_a_blocked_capacity_through_maintain_table_and_changes_nothing(blocked, route):
    code, asked, table, given = ROUTES[route]
    out = Kernel().run(code)
    errors = [o for o in out["outputs"] if o.get("type") == "error"]
    assert out["status"] == "error" and len(errors) == 1, out
    assert errors[0]["ename"] == "PermissionError", errors
    _refused_as_blocked(blocked, errors[0]["evalue"])
    # the route ended in lakehouse.maintain_table, once, asked for what the code asked for
    assert len(blocked.calls) == 1, blocked.calls
    args, kwargs = blocked.calls[0]
    spec = dict(inspect.signature(blocked.maintain_table).bind(*args, **kwargs).arguments)
    assert spec["table"] == table and {k: spec.get(k) for k in given} == given, spec
    # its gate is the one that refused: the audit has the refusal for that operation and table
    assert [(r["outcome"], r["target"]["operation"], r["target"]["table"])
            for r in blocked.audit.recent(10, "notebook_maintenance")] == [("error", asked, table)]
    _unchanged(blocked)


@pytest.mark.parametrize("retain_hours", [None, 200])
def test_a_vacuum_dry_run_called_on_maintain_table_is_refused_by_a_blocked_capacity_too(blocked, retain_hours):
    with pytest.raises(PermissionError) as said:
        lakehouse.maintain_table("dbo.sales", "vacuum", dry_run=True, retain_hours=retain_hours)
    _refused_as_blocked(blocked, str(said.value))
    assert [r["outcome"] for r in blocked.audit.recent(10, "notebook_maintenance")] == ["error"]
    _unchanged(blocked)


# ---- a kernel in another process

OPS = ("optimize", "zorder", "vacuum", "restore")


@pytest.mark.parametrize("name", STOPPED)
def test_a_kernel_in_another_process_gets_the_blocked_refusal_from_an_engine_service(svc, monkeypatch, name):  # noqa: F811
    """The engine's Service installs its guard; the process kernel's call for it (kernel_proc._maintenance_refusal)
    answers with the Blocked reason, for each operation."""
    title = classify(name)[1]
    monkeypatch.setattr(svc, "_project", lambda: (_ for _ in ()).throw(LookupError("no project")), raising=False)
    monkeypatch.setattr(svc, "_risk_capacity", reader(name), raising=False)
    assert getattr(lakehouse.maintenance_guard, "__self__", None) is svc
    for op in OPS:
        reason = kernel_proc._maintenance_refusal(op, "ws-dev", "dbo.orders")
        assert reason and "capacity check is Blocked" in reason and title in reason, (op, reason)
        assert "Nothing was changed" in reason


def test_the_kernel_processs_guard_goes_over_its_channel_to_the_engines_blocked_refusal(svc, monkeypatch):  # noqa: F811
    """The child's hook (kernel_host.Host.attach) calls the engine over the channel; the engine answers with
    ProcessKernel's maintenance_guard, kernel_proc._maintenance_refusal, which asks the Service's guard."""
    monkeypatch.setattr(svc, "_project", lambda: (_ for _ in ()).throw(LookupError("no project")), raising=False)
    monkeypatch.setattr(svc, "_risk_capacity", reader("paused"), raising=False)
    engine = kernel_proc.ProcessKernel()
    assert engine.calls["maintenance_guard"] is kernel_proc._maintenance_refusal
    sent = []
    channel = SimpleNamespace(call=lambda name, *args: sent.append(name) or engine.calls[name](*args))
    child = SimpleNamespace(channel=channel, lakehouse=SimpleNamespace(attach=lambda _a: None))  # the child's module
    kernel_host.Host.attach(child, None)
    for op in OPS:
        reason = child.lakehouse.maintenance_guard(op, "ws-dev", "dbo.orders")
        assert reason and "capacity check is Blocked" in reason and classify("paused")[1] in reason, op
    assert sent == ["maintenance_guard"] * len(OPS)


# ---- a job's V-Order (publish._queue_v_order) under every Blocked reading, through the engine's real gate

@pytest.mark.parametrize("name", STOPPED)
def test_a_jobs_v_order_is_stopped_by_every_blocked_reading_and_the_written_rows_stay(lake, tmp_path, name):  # noqa: F811
    from tests.test_job_v_order import Fabric, _job
    m = _mixin(name)
    fabric = Fabric()
    record = publish.run_job(_job(tmp_path, True), fabric, lambda: "tok", tmp_path, guard=lambda *a: None,
                             capacity_gate=lambda ws, t: MetaMixin._job_v_order_refusal(m, ws, t))
    assert fabric.maintenance == [] and record.v_order_job is None and record.delta_version == 0
    assert f"the capacity check is Blocked ({classify(name)[1]})" in record.v_order_error
    assert DeltaTable(str(lake / "dbo" / "orders")).version() == 0


# ---- coverage: every gated entry point in REVIEWED is run under Blocked

MATRIX = "tests.test_capacity_hard_stop_matrix"
HERE = __name__
COVERED_UNDER_BLOCKED = {
    ("xray_service.py", "xray_optimize"): [f"{MATRIX}::test_xray"],
    ("xray_service.py", "xray_vacuum"): [f"{MATRIX}::test_xray"],
    ("cloud_service.py", "_maintain"): [f"{MATRIX}::test_maintain_optimize", f"{MATRIX}::test_optimize_after_a_run"],
    ("lakehouse.py", "maintain_table"): [
        f"{MATRIX}::test_notebook_maintenance",
        f"{HERE}::test_notebook_code_is_refused_by_a_blocked_capacity_through_maintain_table_and_changes_nothing",
        f"{HERE}::test_a_vacuum_dry_run_called_on_maintain_table_is_refused_by_a_blocked_capacity_too"],
    ("publish.py", "_queue_v_order"): [
        "tests.test_job_v_order::test_a_blocked_capacity_stops_v_order_and_the_written_rows_stay",
        f"{HERE}::test_a_jobs_v_order_is_stopped_by_every_blocked_reading_and_the_written_rows_stay"],
}


def test_every_gated_entry_point_in_the_inventory_is_run_under_a_blocked_capacity():
    gated = {k for k, v in REVIEWED.items() if v.startswith("gate:")}
    assert gated - set(COVERED_UNDER_BLOCKED) == set(), "a new gated entry point: add the tests that run it under Blocked"
    assert set(COVERED_UNDER_BLOCKED) - gated == set(), "COVERED_UNDER_BLOCKED names an entry point REVIEWED no longer gates"
    for entry, tests in COVERED_UNDER_BLOCKED.items():
        assert tests, entry
        for ref in tests:
            module, _, function = ref.partition("::")
            test = getattr(importlib.import_module(module), function, None)
            assert callable(test), ref
            source = inspect.getsource(test)
            # each runs Blocked: over the matrix's Blocked readings, or a Blocked reading or refusal of its own
            assert any(w in source for w in ("STOPPED", "BLOCKED", "Blocked", "blocked")), ref


def _functions_calling(paths, attr, owner=None):
    """(file, function) in ``paths`` calling ``<owner>.attr(...)`` (any owner when None)."""
    found = set()
    for rel in paths:
        tree = ast.parse((PACKAGE / rel).read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == attr
                            and (owner is None or (isinstance(node.func.value, ast.Name) and node.func.value.id == owner))):
                        found.add((rel, fn.name))
    return found


NOTEBOOK_FILES = ("spark_local.py", "spark_maint.py", "notebook.py", "magics.py", "kernel_host.py", "kernel_proc.py")


def test_every_notebook_maintenance_route_ends_in_maintain_table_and_nowhere_else():
    """The routes above, read from the code: spark.sql (a %%sql cell and %sql call it) and LocalDeltaTable go to
    spark_maint.run, the only caller of lakehouse.maintain_table, and none of them reaches delta-rs's
    compact, z_order, vacuum or restore itself."""
    assert _functions_calling(NOTEBOOK_FILES, "run", "spark_maint") == {("spark_local.py", "_sql"),
                                                                       ("spark_local.py", "_maintain")}
    assert _functions_calling(NOTEBOOK_FILES, "maintain_table") == {("spark_maint.py", "run")}
    for attr in ("compact", "z_order", "vacuum", "optimize"):
        assert _functions_calling(NOTEBOOK_FILES, attr) == set(), attr
    # the only restore() there is a proof runtime's (putting the lakehouse module back), not a table's
    assert _functions_calling(NOTEBOOK_FILES, "restore") == {("kernel_host.py", "unbind_proof"),
                                                             ("notebook.py", "unbind_proof")}
    # every DeltaTable API maintenance method goes through _maintain
    tree = ast.parse((PACKAGE / "spark_local.py").read_text(encoding="utf-8"))
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name in ("LocalDeltaTable",
                                                                                          "LocalOptimizeBuilder")):
        for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
            if fn.name in ("vacuum", "restoreToVersion", "restoreToTimestamp", "_run"):
                assert any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "_maintain"
                           for c in ast.walk(fn)), fn.name
            if fn.name in ("executeCompaction", "executeZOrderBy"):
                assert any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "_run"
                           for c in ast.walk(fn)), fn.name
