"""Failure paths of Optimize, Vacuum and Z-Order (PB-104): a Blocked capacity stops them before anything
starts, a run stopped partway leaves the table readable at its last version, and every run, refused or
not, has its line in the audit before it starts. X-ray's runs are Fabric jobs (a fake here); a notebook's
OPTIMIZE, ZORDER and VACUUM run with delta-rs on this PC. No savings are claimed anywhere: these record
what ran, never what it saved."""

import json
from types import SimpleNamespace

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake
from deltalake.table import TableOptimizer

from fabric_desktop import app_settings, audit, lakehouse, server
from fabric_desktop.audit import AuditLog
from fabric_desktop.meta_service import MetaMixin
from tests.test_spark_writer import lake  # noqa: F401 - a fixture
from tests.test_xray import _optimize_capacity, vacuum_svc  # noqa: F401 - a fixture

ARGS = {"workspace_id": "ws-dev", "lakehouse_id": "lh-silver", "table": "orders", "schema": "dbo",
        "lakehouse_name": "LH_Silver", "workspace_name": "Dev"}


def rpc(svc, method, **params):
    return server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))


def lines(svc, action):
    return [(r["outcome"], r.get("reason", "")) for r in svc.audit.recent(50, action)[::-1]]


# ---- X-ray's Optimize and Vacuum (Fabric's table maintenance job)

def test_optimize_and_vacuum_are_recorded_before_they_start_and_name_the_table(vacuum_svc, monkeypatch):  # noqa: F811
    svc, _uri, jobs = vacuum_svc
    monkeypatch.setattr(svc, "_wl_token", lambda _s: "token", raising=False)
    monkeypatch.setattr(svc, "_risk_capacity", lambda *a: _optimize_capacity("running"), raising=False)
    for method in ("xray_optimize", "xray_vacuum"):
        assert audit.fails_closed(method, {})  # a pass needs no risk id, and still writes its line first
        assert rpc(svc, method, **ARGS)["result"]["job_id"] == "job-1"
        assert lines(svc, method) == [("started", ""), ("ok", "")]
        started = svc.audit.recent(1, method)[0]
        assert started["target"]["table"] == "orders" and started["target"]["lakehouse_id"] == "lh-silver"
    assert len(jobs) == 2


def test_an_audit_that_cant_be_written_starts_no_job(vacuum_svc, monkeypatch):  # noqa: F811
    svc, _uri, jobs = vacuum_svc
    monkeypatch.setattr(svc, "_wl_token", lambda _s: "token", raising=False)
    monkeypatch.setattr(svc, "_risk_capacity", lambda *a: _optimize_capacity("running"), raising=False)

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(svc.audit, "record", full)
    for method in ("xray_optimize", "xray_vacuum"):
        reply = rpc(svc, method, **ARGS)
        assert "audit log couldn't be written" in reply["error"], reply
    assert jobs == [] and not svc._settings().get("xray_submitted")
    assert svc.fabric_runs(refresh=False) == []


def test_a_blocked_capacity_starts_nothing_and_the_refusal_is_in_the_audit(vacuum_svc, monkeypatch):  # noqa: F811
    svc, _uri, jobs = vacuum_svc
    monkeypatch.setattr(svc, "_wl_token", lambda _s: "token", raising=False)
    monkeypatch.setattr(svc, "_risk_capacity", lambda *a: _optimize_capacity("not_running"), raising=False)
    monkeypatch.setattr(app_settings, "value", lambda k: False if k == "allow_fabric_runs" else None)
    for method, check in (("xray_optimize", "xray_optimize_check"), ("xray_vacuum", "xray_vacuum_preview")):
        risk = getattr(svc, check)(**ARGS)["risk"]
        assert risk["result"] == "blocked"
        reply = rpc(svc, method, **ARGS, finding_id="f1", risk_id=risk["id"])  # even with the id it was shown
        assert "can't be started" in reply["error"], reply
        assert lines(svc, method) == [("started", ""), ("error", "")]
    assert jobs == [] and svc.risk_acknowledgements() == []
    assert not svc._settings().get("xray_submitted") and svc.fabric_runs(refresh=False) == []


@pytest.mark.parametrize("status", ["Cancelled", "Failed"])
def test_a_cancelled_or_failed_job_never_shows_its_finding_as_fixed(vacuum_svc, status):  # noqa: F811
    svc, _uri, _jobs = vacuum_svc
    svc._risk_capacity = lambda *a: _optimize_capacity("running")
    svc._wl_token = lambda _s: "token"
    svc.xray_vacuum(**ARGS, finding_id="vacuum|orders")
    runs = svc._read_fabric_runs()
    runs[-1].update(status=status, finished_at="2999-01-01T00:00:00+00:00", message="Stopped by the user")
    svc._write_fabric_runs(runs)
    fix = svc._xray_fixes(None, refresh=False)["vacuum|orders"]
    assert fix["state"] == "failed" and fix["status"] == status


# ---- a notebook's OPTIMIZE, ZORDER and VACUUM on this PC

def engine(tmp_path, capacity=None):
    """The engine's maintenance guard over a real audit log, with the workspace's last capacity reading."""
    m = SimpleNamespace(_project=lambda: SimpleNamespace(environments={}), audit=AuditLog(tmp_path / "audit"),
                        _risk_capacity=lambda ws, name, refresh, reads: capacity)
    m._maintenance_capacity_refusal = lambda *a, **k: MetaMixin._maintenance_capacity_refusal(m, *a, **k)
    m._maintenance_audit = lambda *a: MetaMixin._maintenance_audit(m, *a)
    return m


def recorded(m):
    return [(r["outcome"], r["target"]["operation"], r["target"]["table"], r.get("reason", ""))
            for r in m.audit.recent(50, "notebook_maintenance")[::-1]]


def sales(lake, at=None):  # noqa: F811
    return sorted(r["id"] for r in DeltaTable(str(lake / "dbo" / "sales"), version=at).to_pyarrow_table().to_pylist())


def more(lake, n=3):  # noqa: F811
    for i in range(n):
        write_deltalake(str(lake / "dbo" / "sales"), pa.table({"id": [10 + i], "region": ["EU"]}), mode="append")


RUNNING = {"access": "ok", "status": "running", "capacity": {"name": "F64"}, "age_seconds": 30}
PAUSED = dict(RUNNING, status="not_running")


def test_each_notebook_run_is_in_the_audit_before_it_changes_the_table(lake, tmp_path, monkeypatch):  # noqa: F811
    m = engine(tmp_path, RUNNING)
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: MetaMixin._maintenance_refusal(m, op, ws, t))
    more(lake)
    lakehouse.maintain_table("dbo.sales", "optimize", zorder=["id"])
    lakehouse.maintain_table("dbo.sales", "optimize")
    lakehouse.maintain_table("dbo.sales", "vacuum", dry_run=True)
    assert recorded(m) == [("started", op, "dbo.sales", "") for op in ("zorder", "optimize", "vacuum")]
    assert sales(lake) == [1, 2, 3, 10, 11, 12]


def test_a_blocked_capacity_stops_zorder_and_vacuum_and_the_refusal_is_recorded(lake, tmp_path, monkeypatch):  # noqa: F811
    m = engine(tmp_path, PAUSED)
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: MetaMixin._maintenance_refusal(m, op, ws, t))
    more(lake)
    before = DeltaTable(str(lake / "dbo" / "sales")).version()
    for op, kw in (("optimize", {"zorder": ["id"]}), ("optimize", {}), ("vacuum", {})):
        with pytest.raises(PermissionError, match="capacity check is Blocked") as said:
            lakehouse.maintain_table("dbo.sales", op, **kw)
        assert str(said.value).startswith("OPTIMIZE with ZORDER BY on" if kw else f"{op.upper()} on")
    assert DeltaTable(str(lake / "dbo" / "sales")).version() == before
    assert [(o, op, r) for o, op, _t, r in recorded(m)] == [("error", op, "refused") for op in ("zorder", "optimize", "vacuum")]


def test_a_test_or_prod_workspace_refusal_is_recorded_too(tmp_path):
    m = engine(tmp_path, RUNNING)
    m._project = lambda: SimpleNamespace(environments={"prod": {"workspace_id": "w-prod"}})
    said = MetaMixin._maintenance_refusal(m, "zorder", "w-prod", "dbo.t")
    assert said.startswith("OPTIMIZE with ZORDER BY on dbo.t") and "Prod's" in said and "ZORDER on" not in said
    assert MetaMixin._maintenance_refusal(m, "vacuum", "w-dev", "dbo.t") is None
    assert recorded(m) == [("error", "zorder", "dbo.t", "refused"), ("started", "vacuum", "dbo.t", "")]


def test_an_audit_that_cant_be_written_stops_a_notebook_run(lake, tmp_path, monkeypatch):  # noqa: F811
    m = engine(tmp_path, RUNNING)

    def locked(*a, **k):
        raise PermissionError(13, "The file is in use")
    m.audit.record = locked
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: MetaMixin._maintenance_refusal(m, op, ws, t))
    more(lake)
    before = DeltaTable(str(lake / "dbo" / "sales")).version()
    with pytest.raises(PermissionError, match="audit log couldn't be written"):
        lakehouse.maintain_table("dbo.sales", "optimize", zorder=["id"])
    assert DeltaTable(str(lake / "dbo" / "sales")).version() == before


def test_a_refusal_stands_when_the_audit_cant_record_it(tmp_path):
    m = engine(tmp_path, PAUSED)

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    m.audit.record = full
    assert "Blocked" in MetaMixin._maintenance_refusal(m, "vacuum", "w-dev", "dbo.t")


@pytest.mark.parametrize("op, method", [("optimize", "compact"), ("optimize", "z_order")])
def test_optimize_or_zorder_stopped_partway_leaves_the_last_version(lake, tmp_path, monkeypatch, op, method):  # noqa: F811
    """A stop before the commit (the cell interrupted, the PC shut down) can leave new data files in the
    folder, but no commit names them: readers see the last version, and the next OPTIMIZE runs as usual."""
    history = tmp_path / "history.jsonl"
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path, history_file=history))
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: None)
    more(lake)
    table = DeltaTable(str(lake / "dbo" / "sales"))
    before, rows = table.version(), sales(lake)

    def stopped(self, *a, **k):
        write_deltalake(str(tmp_path / "half"), pa.table({"id": [99]}))  # a file written before the stop
        (lake / "dbo" / "sales" / "part-00000-half-written.snappy.parquet").write_bytes(
            next((tmp_path / "half").glob("*.parquet")).read_bytes())
        raise KeyboardInterrupt
    real = getattr(TableOptimizer, method)
    monkeypatch.setattr(TableOptimizer, method, stopped)
    kw = {"zorder": ["id"]} if method == "z_order" else {}
    with pytest.raises(KeyboardInterrupt):
        lakehouse.maintain_table("dbo.sales", op, **kw)
    after = DeltaTable(str(lake / "dbo" / "sales"))
    assert after.version() == before and sales(lake) == rows  # the half-written file isn't read
    assert not history.exists()  # nothing claims it ran

    monkeypatch.setattr(TableOptimizer, method, real)
    done = lakehouse.maintain_table("dbo.sales", op, **kw)
    assert done.delta_version == before + 1 and sales(lake) == rows


def test_vacuum_stopped_partway_leaves_the_last_version_readable(lake, monkeypatch):  # noqa: F811
    """VACUUM deletes only files no current version names, so a stop after some deletes leaves the
    latest version whole; only time travel to the versions those files belonged to can be lost."""
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: None)
    more(lake)
    lakehouse.maintain_table("dbo.sales", "optimize")  # the small files are now history
    table = DeltaTable(str(lake / "dbo" / "sales"))
    version, rows = table.version(), sales(lake)
    live = {u.rsplit("/", 1)[-1] for u in table.file_uris()}
    real = DeltaTable.vacuum

    def stopped(self, retention_hours=None, dry_run=True, enforce_retention_duration=True, **k):
        old = [p for p in (lake / "dbo" / "sales").glob("*.parquet") if p.name not in live]
        assert len(old) > 1
        old[0].unlink()  # one deleted, then the stop
        raise KeyboardInterrupt
    monkeypatch.setattr(DeltaTable, "vacuum", stopped)
    with pytest.raises(KeyboardInterrupt):
        lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=24 * 7)
    after = DeltaTable(str(lake / "dbo" / "sales"))
    assert after.version() == version and sales(lake) == rows
    monkeypatch.setattr(DeltaTable, "vacuum", real)
    lakehouse.maintain_table("dbo.sales", "vacuum", dry_run=True)  # and VACUUM can run again


def test_a_vacuum_keeping_too_little_history_is_refused_before_it_is_recorded_as_started(lake, tmp_path, monkeypatch):  # noqa: F811
    m = engine(tmp_path, RUNNING)
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: MetaMixin._maintenance_refusal(m, op, ws, t))
    before = DeltaTable(str(lake / "dbo" / "sales")).version()
    for hours in (0, 24, 167):
        with pytest.raises(ValueError, match="at least 7 days"):
            lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=hours)
    assert recorded(m) == [] and DeltaTable(str(lake / "dbo" / "sales")).version() == before
    lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=168, dry_run=True)
    assert recorded(m) == [("started", "vacuum", "dbo.sales", "")]
