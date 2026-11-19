"""WR-410: every Optimize, Vacuum and Z-Order entry point checks the capacity risk first, and Blocked is a hard
stop; no entry point keeps less than the safe default of history; results count files from the Delta log
(measured), never estimate savings.

The inventory below lists every call that compacts, Z-Orders, vacuums or starts Fabric's table maintenance job.
A new call fails test_every_entry_point_is_reviewed until it is added here with its gate, or with why it needs
none."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import maintenance, server
from fabric_desktop.cloud_service import CloudMixin
from fabric_desktop.meta.store import OneLakeStore
from fabric_desktop.meta_service import MetaMixin
from tests.test_xray import _optimize_capacity, vacuum_svc  # noqa: F401 - a fixture

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"
CALLS = {"run_table_maintenance", "compact", "z_order", "vacuum"}

# (file, function) -> how it is stopped when the capacity check is Blocked, or why it needs no check.
REVIEWED = {
    ("xray_service.py", "xray_optimize"): "gate: _xray_risk_gate (Blocked raises PermissionError)",
    ("xray_service.py", "xray_vacuum"): "gate: _xray_risk_gate (Blocked raises PermissionError)",
    ("cloud_service.py", "_maintain"): "gate: _maintenance_gate per workspace (Maintain > Optimize, and "
                                       "Optimize after a run: Blocked stops, a Warning needs its risk_id); its "
                                       "local branch is a PC-only table",
    ("lakehouse.py", "maintain_table"): "gate: maintenance_guard -> MetaMixin._maintenance_refusal (notebook "
                                        "OPTIMIZE, ZORDER BY, VACUUM, RESTORE)",
    ("fabric_compute.py", "_optimize"): "none needed: runs inside a Fabric notebook on the capacity itself, after "
                                        "a run whose own risk check (paused, throttled or otherwise Blocked) already passed",
    ("maintenance.py", "compact_local"): "none needed: a table on this PC (LocalStore), no Fabric capacity",
    ("maintenance.py", "vacuum_preview"): "dry run only",
    ("maintenance.py", "_unnamed"): "dry run only",
    ("maintenance.py", "_named"): "dry run only",
    ("meta/store.py", "_tidy_meta"): "none needed: LocalStore's meta tables on this PC",
    ("publish.py", "_queue_v_order"): "gate: capacity_gate -> MetaMixin._maintenance_capacity_refusal (V-Order "
                                      "after a job's write); no gate means no V-Order",
}


def _dry_run(call: ast.Call) -> bool:
    return any(k.arg == "dry_run" and isinstance(k.value, ast.Constant) and k.value.value is True for k in call.keywords)


def entry_points() -> set[tuple[str, str]]:
    found = set()
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in CALLS
                        and not (node.func.attr == "vacuum" and _dry_run(node))):
                    found.add((path.relative_to(PACKAGE).as_posix(), fn.name))
    return found


def test_every_entry_point_is_reviewed():
    found = entry_points()
    dry = {k for k, v in REVIEWED.items() if v == "dry run only"}
    assert found - set(REVIEWED) == set(), "a new Optimize, Vacuum or Z-Order call: add it to REVIEWED with its gate"
    assert set(REVIEWED) - found - dry == set(), "REVIEWED names a call that is gone"
    assert {k for k, v in REVIEWED.items() if v.startswith("gate:")} == {
        ("xray_service.py", "xray_optimize"), ("xray_service.py", "xray_vacuum"),
        ("cloud_service.py", "_maintain"), ("lakehouse.py", "maintain_table"), ("publish.py", "_queue_v_order")}


# ---- Maintain > Optimize (and Optimize after a run) in OneLake

class Store(OneLakeStore):
    """OneLake's store as _maintain sees it, without reaching OneLake."""
    def __init__(self):  # noqa: D107 - nothing to open
        pass

    lakehouse_ids = {"LH_Silver": "lh-silver", "LH_Gold": "lh-gold"}

    def workspace_of(self, lakehouse):
        return {"LH_Silver": "ws-dev", "LH_Gold": "ws-serve"}[lakehouse]

    def uri(self, lakehouse, schema, table):
        return f"abfss://{lakehouse}/{schema}/{table}"

    def options(self):
        return {}


def mixin(capacity_by_ws, monkeypatch):
    jobs, retention = [], []
    monkeypatch.setattr(maintenance, "set_retention", lambda uri, days, options=None, **k: retention.append(uri))
    m = SimpleNamespace(_project=lambda: SimpleNamespace(environments={}),
                        _risk_capacity=lambda ws, name, refresh, reads: capacity_by_ws.get(ws),
                        fabric_jobs=SimpleNamespace(run_table_maintenance=lambda *a, **k: jobs.append(a) or f"job-{len(jobs)}"),
                        _log_fabric_run=lambda r: r | {"status": "NotStarted"},
                        _wl_user=lambda: "chris", acks=[])
    m._risk_ack_write = m.acks.append
    m._maintenance_capacity = lambda ws: MetaMixin._maintenance_capacity(m, ws)
    m._maintenance_capacity_refusal = lambda *a, **k: MetaMixin._maintenance_capacity_refusal(m, *a, **k)
    m._maintenance_gate = lambda *a: CloudMixin._maintenance_gate(m, *a)
    return m, jobs, retention


ROWS = [{"lakehouse": "LH_Silver", "schema": "dbo", "table": "orders", "retention_days": 7},
        {"lakehouse": "LH_Silver", "schema": "dbo", "table": "lines", "retention_days": 7},
        {"lakehouse": "LH_Gold", "schema": "dbo", "table": "sales", "retention_days": 30}]


def test_a_blocked_workspace_starts_no_maintenance_job_and_changes_no_table(monkeypatch):
    paused = _optimize_capacity("not_running")
    m, jobs, retention = mixin({"ws-dev": paused, "ws-serve": _optimize_capacity("running")}, monkeypatch)
    out = CloudMixin._maintain(m, Store(), ROWS, "dev")
    assert [(r["table"], r["status"], r.get("blocked", False)) for r in out] == [
        ("dbo.orders", "Failed", True), ("dbo.lines", "Failed", True), ("dbo.sales", "NotStarted", False)]
    assert "capacity check is Blocked" in out[0]["message"] and "Nothing was changed" in out[0]["message"]
    assert [a[1] for a in jobs] == ["lh-gold"]  # only the running capacity's table
    assert retention == ["abfss://LH_Gold/dbo/sales"]  # a Blocked table's properties aren't touched either


def test_a_capacity_that_cant_be_read_is_unknown_and_doesnt_stop_maintenance(monkeypatch):
    m, jobs, _ = mixin({}, monkeypatch)  # no reading: unknown, as in every risk check
    assert [r["status"] for r in CloudMixin._maintain(m, Store(), ROWS, "dev")] == ["NotStarted"] * 3
    assert len(jobs) == 3


def test_optimize_after_a_run_is_stopped_by_a_blocked_capacity_too(monkeypatch):
    m, jobs, _ = mixin({"ws-dev": _optimize_capacity("not_running")}, monkeypatch)
    m._optimize_after_run = lambda project, env: True
    m._managed_tables = lambda project, include_meta=True: [dict(ROWS[0], entity="orders")]
    m._maintain = lambda *a: CloudMixin._maintain(m, *a)
    store = Store()
    project = SimpleNamespace(entities={"orders": SimpleNamespace(schema="dbo", table="orders")})
    results = [{"entity": "orders", "status": "ok", "rows_written": 5}]
    CloudMixin._after_run(m, project, "dev", store, results)
    assert results[0]["maintenance"] == "Failed" and "Blocked" in results[0]["maintenance_message"]
    assert jobs == []


# ---- the history VACUUM keeps

@pytest.mark.parametrize("days", [0, 1, 6])
def test_no_entry_point_keeps_less_than_the_safe_default(tmp_path, days):
    uri = str(tmp_path / "t")
    write_deltalake(uri, pa.table({"id": [1]}))
    for call in (lambda: maintenance.compact_local(uri, days), lambda: maintenance.vacuum_preview(uri, days),
                 lambda: maintenance.fabric_retention(days), lambda: maintenance.set_retention(uri, days)):
        with pytest.raises(ValueError, match="at least 7 days"):
            call()
    assert DeltaTable(uri).version() == 0


def test_xray_vacuum_keeps_the_safe_default_even_when_the_table_asks_for_less(vacuum_svc, monkeypatch):  # noqa: F811
    """A table set to keep one day still vacuums with seven: X-ray keeps the longest of Fabric's default, the
    table's own setting and the layer's. The run is in the audit, started then ok."""
    svc, uri, jobs = vacuum_svc
    DeltaTable(str(uri)).alter.set_table_properties({"delta.deletedFileRetentionDuration": "interval 1 days"})
    monkeypatch.setattr(svc, "_wl_token", lambda _s: "token", raising=False)
    monkeypatch.setattr(svc, "_risk_capacity", lambda *a: _optimize_capacity("running"), raising=False)
    monkeypatch.setattr(svc, "_project", lambda: (_ for _ in ()).throw(__import__(
        "fabric_desktop.xray_service", fromlist=["NoMetadataProject"]).NoMetadataProject("none")))
    reply = server.handle(svc, json.dumps({"id": 1, "method": "xray_vacuum", "params": {
        "workspace_id": "ws-other", "lakehouse_id": "lh-silver", "table": "orders", "schema": "dbo",
        "lakehouse_name": "LH_X"}}))
    assert reply["result"]["keep_days"] == 7, reply
    assert jobs[-1][1]["vacuum_retention"] == "7.01:00:00"
    assert [r["outcome"] for r in svc.audit.recent(5, "xray_vacuum")[::-1]] == ["started", "ok"]


# ---- measured file counts

def test_compaction_reports_file_counts_read_from_the_delta_log(tmp_path):
    uri = str(tmp_path / "t")
    for i in range(4):
        write_deltalake(uri, pa.table({"id": [i]}), mode="append")
    out = maintenance.compact_local(uri, 7)
    assert (out["files_before"], out["files_after"]) == (4, 1)
    assert out["files_after"] == len(DeltaTable(uri).file_uris())
    assert not any("saving" in k or "saved" in k or "cu" == k for k in out)  # counts only, no savings
