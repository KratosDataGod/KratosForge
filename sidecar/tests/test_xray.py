"""Estate X-ray over local folders laid out like OneLake, and adopting a lakehouse into metadata."""

import datetime as dt
import re
from pathlib import Path

import polars as pl
import pytest
from deltalake import write_deltalake

from fabric_desktop import maintenance, xray
from fabric_desktop.service import Service
from fabric_desktop.xray_service import adopted_entity_text, guess_keys


def _lake(root: Path):
    """Two workspaces: Sales (a lakehouse with schemas, one without, one empty) and Finance."""
    lh = root / "ws-sales" / "lh-raw" / "Tables"
    for i in range(25):  # 25 tiny commits: small files
        write_deltalake(str(lh / "dbo" / "orders"), pl.DataFrame({"order_id": [i], "amount": [1.0]}).to_arrow(), mode="append")
    write_deltalake(str(lh / "dbo" / "customers"), pl.DataFrame({"customer_id": [1, 2], "name": ["a", "b"],
                                                                 "updated_at": ["2026-01-01", "2026-01-02"]}).to_arrow())
    (lh / "dbo" / "dumped_csv").mkdir(parents=True)
    (lh / "dbo" / "dumped_csv" / "part.csv").write_text("a,b\n1,2\n")
    flat = root / "ws-sales" / "lh-flat" / "Tables"
    write_deltalake(str(flat / "events"), pl.DataFrame({"id": [1]}).to_arrow())
    (root / "ws-sales" / "lh-empty" / "Tables").mkdir(parents=True)
    (root / "ws-sales" / "lh-empty" / "Files").mkdir(parents=True)
    write_deltalake(str(root / "ws-fin" / "lh-gl" / "Tables" / "dbo" / "ledger"), pl.DataFrame({"id": [1]}).to_arrow())


class FakeFabric:
    def list_workspaces(self):
        return [{"id": "ws-sales", "displayName": "Sales"}, {"id": "ws-fin", "displayName": "Finance"}]

    def list_lakehouses(self, ws):
        return {"ws-sales": [{"id": "lh-raw", "displayName": "LH_Raw"}, {"id": "lh-flat", "displayName": "LH_Flat"},
                             {"id": "lh-empty", "displayName": "LH_Empty"}],
                "ws-fin": [{"id": "lh-gl", "displayName": "LH_GL"}]}[ws]

    def list_role_assignments(self, ws):
        if ws == "ws-fin":
            raise PermissionError("not an admin")
        return [{"id": "a1", "role": "Admin", "principal": {"id": "u1", "displayName": "Chris", "type": "User",
                                                            "userDetails": {"userPrincipalName": "chris@kratosbi.com"}}},
                {"id": "a2", "role": "Viewer", "principal": {"id": "g1", "displayName": "Pat", "type": "User",
                                                             "userDetails": {"userPrincipalName": "pat_x.com#EXT#@kratosbi.com"}}},
                {"id": "a3", "role": "Admin", "principal": {"id": "sp1", "displayName": "etl-app", "type": "ServicePrincipal"}}]


def _files(root: Path):
    def files(ws, lh, directory):
        p = root / ws / lh / directory
        if not p.exists():
            return []
        return [{"name": c.name, "is_dir": c.is_dir()} for c in sorted(p.iterdir())]
    return files


def _health(root: Path):
    def health(ws, lh, schema, table):
        return maintenance.health(str(root / ws / lh / "Tables" / (f"{schema}/{table}" if schema else table)))
    return health


def test_scan_finds_what_needs_attention(tmp_path):
    _lake(tmp_path)
    seen = []
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=45)
    out = xray.Scanner(FakeFabric(), _files(tmp_path), _health(tmp_path), seen.append).scan(now=later)
    assert (out["workspaces"], out["lakehouses"], out["tables"]) == (2, 4, 5)
    kinds = [(f["kind"], f.get("table") or f.get("lakehouse_name") or f.get("person")) for f in out["findings"]]
    assert kinds[0] == ("small_files", "orders")  # most urgent first
    assert ("unidentified", "dumped_csv") in kinds and ("empty", "LH_Empty") in kinds
    assert ("stale", "ledger") in kinds and ("stale", "events") in kinds  # 45 days later
    access = [f["title"] for f in out["findings"] if f["kind"] == "access"]
    assert access == ["App etl-app is Admin in 1 workspace", "Guest Pat has access to 1 workspace"]
    raw = next(r for r in out["lakehouse_rows"] if r["lakehouse_name"] == "LH_Raw")
    assert raw["tables"] == 3 and raw["issues"] >= 3
    assert {p["phase"] for p in seen} >= {"Finding lakehouses", "Reading table logs"}


def test_a_lakehouse_or_workspace_the_scan_skips_is_logged_with_why(tmp_path, caplog):
    """Skipped parts are listed in the report; the log says why, so a support case can be traced."""
    _lake(tmp_path)

    class Patchy(FakeFabric):
        def list_lakehouses(self, ws):
            if ws == "ws-fin":
                raise RuntimeError("capacity paused")
            return super().list_lakehouses(ws)

    def files(ws, lh, directory):
        if lh == "lh-flat":
            raise RuntimeError("403 from OneLake")
        return _files(tmp_path)(ws, lh, directory)

    with caplog.at_level("WARNING", logger="fabric_desktop.xray"):
        out = xray.Scanner(Patchy(), files, _health(tmp_path), lambda _: None).scan()
    assert "Finance" in out["skipped"] and "Sales / LH_Flat" in out["skipped"]
    logged = [r.getMessage() for r in caplog.records]
    assert any("Finance" in m and "capacity paused" in m for m in logged), logged
    assert any("LH_Flat" in m and "403 from OneLake" in m for m in logged), logged


def test_cost_findings_point_at_local_notebooks():
    report = {"days": 30, "by_item": [
        {"kind": "Notebook run", "name": "Daily load", "env": "", "cost": 42.5, "fabric_runs": 30, "cu_hours": 236.0},
        {"kind": "Notebook run", "name": "Elsewhere", "env": "", "cost": 1.2, "fabric_runs": 3, "cu_hours": 6.6},
        {"kind": "Metadata run", "name": "silver.x", "env": "prod", "cost": 9.0, "fabric_runs": 3, "cu_hours": 50.0}]}
    out = xray.cost_findings(report, {"Daily load"})
    assert [(f["notebook"], f["action"], f["severity"]) for f in out] == [("Daily load", "run_local", "medium"),
                                                                        ("Elsewhere", "cost", "low")]


def test_adopted_entity_loads_the_original_table(tmp_path, monkeypatch):
    source = tmp_path / "lake" / "Tables" / "dbo" / "customers"
    write_deltalake(str(source), pl.DataFrame({"CustomerID": [1, 2], "Name": ["a", "b"],
                                               "ModifiedDate": ["2026-01-01", "2026-01-02"]}).to_arrow())
    svc = Service(root=tmp_path / "app")
    svc.meta_init(str(tmp_path / "proj"), "3")
    project = svc._project()
    schema = pl.read_delta(str(source)).schema
    assert guess_keys(list(schema)) == ["CustomerID"]
    text = adopted_entity_text("bronze.crm_customers", "chris", str(source), "Sales / LH_Raw / dbo.customers",
                               dict(schema), ["CustomerID"])
    (project.entities_dir / "bronze.crm_customers.toml").write_text(text, encoding="utf-8")
    assert svc.meta_project()["errors"] == []
    [res] = svc.meta_run(["bronze.crm_customers"])
    assert res["status"] == "ok" and res["rows_written"] == 2
    assert svc.meta_entity("bronze.crm_customers")["source"] == "Sales / LH_Raw / dbo.customers"
    # New rows only, by the timestamp it found.
    write_deltalake(str(source), pl.DataFrame({"CustomerID": [3], "Name": ["c"], "ModifiedDate": ["2026-02-01"]}).to_arrow(),
                    mode="append")
    [again] = svc.meta_run(["bronze.crm_customers"])
    assert again["status"] == "ok" and again["rows_read"] == 1


def test_adopt_writes_entities_and_skips_what_exists(tmp_path, monkeypatch):
    _lake(tmp_path / "lake")
    svc = Service(root=tmp_path / "app")
    svc.meta_init(str(tmp_path / "proj"), "3")
    lake = tmp_path / "lake"

    def source_table(self, ws, lh, schema, table):
        from deltalake import DeltaTable
        uri = str(lake / ws / lh / "Tables" / (f"{schema}/{table}" if schema else table))
        return uri, DeltaTable(uri)

    monkeypatch.setattr(Service, "_source_table", source_table)
    monkeypatch.setattr(Service, "auth", property(lambda self: type("A", (), {
        "signed_in_user": lambda _s: "chris@kratosbi.com", "storage_token": lambda _s: "tok"})()))
    import fabric_desktop.xray_service as xs
    monkeypatch.setattr(xs, "FileStore", lambda _t: type("F", (), {"list": staticmethod(_files(lake))})())
    plan = svc.xray_adopt_plan("ws-sales", "lh-raw", "LH_Raw", "Sales")
    by = {t["table"]: t for t in plan["tables"]}
    assert plan["layer"] == "bronze" and by["orders"]["keys"] == ["order_id"] and by["orders"]["name"] == "bronze.orders"
    assert by["orders"]["exists"]  # the sample project already has bronze.orders
    assert not by["dumped_csv"]["ok"]
    out = svc.xray_adopt("ws-sales", "lh-raw", "LH_Raw", "Sales",
                         [{"schema": "dbo", "table": "orders", "name": "bronze.raw_orders"},
                          {"schema": "dbo", "table": "customers"}])
    assert out["adopted"] == ["bronze.raw_orders"] and out["problems"] == ["bronze.customers already exists"]
    p = svc.meta_project()
    assert p["errors"] == [] and "bronze.raw_orders" in {e["name"] for e in p["entities"]}
    again = {t["table"]: t for t in svc.xray_adopt_plan("ws-sales", "lh-raw", "LH_Raw", "Sales")["tables"]}
    assert again["orders"]["why"] == "Already adopted as bronze.raw_orders"


def test_scan_waits_while_paused(tmp_path):
    import threading
    _lake(tmp_path)
    gate = threading.Event()  # starts paused
    out = {}
    worker = threading.Thread(target=lambda: out.update(xray.Scanner(FakeFabric(), _files(tmp_path), _health(tmp_path),
                                                                     gate=gate).scan()))
    worker.start()
    worker.join(0.5)
    assert worker.is_alive() and not out  # paused before the first workspace
    gate.set()
    worker.join(30)
    assert out["tables"] == 5


def test_home_scans_by_itself_only_once(tmp_path, monkeypatch):
    svc = Service(root=tmp_path / "app")
    started = []
    monkeypatch.setattr(svc, "_xray_run", lambda *a: started.append(a))

    class Auth:
        def fabric_token(self):
            return "f"

        def storage_token(self):
            return "s"
    monkeypatch.setattr(Service, "auth", property(lambda self: Auth()), raising=False)
    assert svc.xray(auto=True)["running"] is True
    svc._xray_state = {"running": False, "error": "RuntimeError: offline"}  # the scan failed: nothing saved
    assert svc.xray(auto=True)["running"] is False  # a second Home visit doesn't scan again
    assert len(started) == 1
    assert svc.xray(refresh=True)["running"] is True  # the button does
    assert svc.xray_pause(True)["paused"] is True
    assert not svc._xray_gate.is_set()
    assert svc.xray_pause(False)["paused"] is False and svc._xray_gate.is_set()


def test_vacuum_finding_counts_versions_since_the_last_vacuum():
    t = {"workspace_id": "w", "workspace_name": "W", "lakehouse_id": "l", "lakehouse_name": "LH", "schema": "dbo", "table": "orders"}
    now = dt.datetime.now(dt.timezone.utc)
    base = {"exists": True, "advice": [], "files": 1, "bytes": 1, "last_write": now.isoformat()}
    [never] = xray.table_findings(t, base | {"versions_since_vacuum": 40}, now)
    assert never["action"] == "vacuum" and "never been vacuumed (40 versions)" in never["title"]
    [behind] = xray.table_findings(t, base | {"versions_since_vacuum": 35, "last_vacuum": "2026-01-01T00:00:00+00:00"}, now)
    assert behind["title"] == "dbo.orders has 35 versions since its last vacuum"
    assert xray.table_findings(t, base | {"versions_since_vacuum": 29}, now) == []


def test_kept_days_reads_the_tables_own_retention():
    assert maintenance.kept_days({"delta.deletedFileRetentionDuration": "interval 30 days"}) == 30
    assert maintenance.kept_days({"delta.deletedFileRetentionDuration": "interval 1 weeks"}) == 7
    assert maintenance.kept_days({"delta.deletedFileRetentionDuration": "interval 200 hours"}) == 9  # rounded up
    assert maintenance.kept_days({}) is None and maintenance.kept_days(None) is None


@pytest.fixture
def vacuum_svc(tmp_path, monkeypatch):
    """A table with old versions in a lakehouse that the silver layer (30 days of history) owns in Dev."""
    uri = tmp_path / "lake" / "ws-dev" / "lh-silver" / "Tables" / "dbo" / "orders"
    for i in range(3):
        write_deltalake(str(uri), pl.DataFrame({"id": [i]}).to_arrow(), mode="overwrite")
    old = dt.datetime.now().timestamp() - 40 * 86400  # the replaced files were removed 40 days ago
    log = uri / "_delta_log"
    for f in log.glob("*.json"):
        text = f.read_text()
        f.write_text(re.sub(r'"deletionTimestamp":\d+', f'"deletionTimestamp":{int(old * 1000)}', text))
    svc = Service(root=tmp_path / "app")
    svc.meta_init(str(tmp_path / "proj"), "3")
    project = svc._project()
    layers = [l.to_dict() for l in project.layers]
    layers[1]["retention_days"] = 30
    svc.meta_set_layers(layers)
    project = svc._project()
    envs = {k: dict(v) for k, v in project.environments.items()}
    envs["dev"] = {"target": "onelake", "workspace_id": "ws-dev"}
    svc._write_environments(project, envs)
    monkeypatch.setattr(Service, "_xray_table", lambda self, ws, lh, schema, table: (str(uri), None))
    jobs = []

    class Jobs:
        def run_table_maintenance(self, *a, **k):
            jobs.append((a, k))
            return "job-1"
    monkeypatch.setattr(Service, "fabric_jobs", property(lambda self: Jobs()), raising=False)
    monkeypatch.setattr(Service, "auth", property(lambda self: type("A", (), {"storage_token": lambda _s: "tok"})()), raising=False)
    import fabric_desktop.xray_service as xs
    lake = tmp_path / "lake"

    def files(ws, lh, directory, recursive=False):
        base = lake / ws / lh
        found = (base / directory).rglob("*") if recursive else (base / directory).iterdir()
        return [{"name": p.name, "path": p.relative_to(base).as_posix(), "is_dir": p.is_dir(),
                 "size": 0 if p.is_dir() else p.stat().st_size} for p in found]
    monkeypatch.setattr(xs, "FileStore", lambda _t: type("F", (), {"list": staticmethod(files)})())
    return svc, uri, jobs


def test_vacuum_keeps_the_history_the_layer_promises(vacuum_svc, monkeypatch):
    svc, uri, jobs = vacuum_svc
    monkeypatch.setattr(svc, "_wl_token", lambda _s: "token", raising=False)  # Optimize's risk check: not blocked
    assert svc._xray_keep_days("ws-dev", "LH_Silver", None) == (30, "Keep history on the silver layer")
    assert svc._xray_keep_days("ws-other", "LH_Silver", None)[0] == 7  # not a metadata workspace
    assert svc._xray_keep_days("ws-other", "LH_X", 45) == (45, "the table's own retention setting")
    out = svc.xray_vacuum("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver", "Dev", finding_id="vacuum|x")
    (args, kw), = jobs
    assert kw == {"vacuum_retention": "30.01:00:00", "optimize": False}
    assert out["keep_days"] == 30 and out["job_id"] == "job-1" and out["name"] == "Vacuum LH_Silver dbo.orders"
    assert svc._settings()["xray_submitted"]["vacuum|x"]["job_id"] == "job-1"  # submitted, not fixed
    svc.xray_optimize("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver", "Dev")
    assert jobs[1][1] == {"vacuum_retention": "30.01:00:00"}  # Optimize's vacuum keeps it too


def test_vacuum_preview_counts_what_it_would_delete(vacuum_svc):
    svc, uri, _ = vacuum_svc
    keep30 = svc.xray_vacuum_preview("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver")
    assert keep30["files"] == 2 and keep30["bytes"] > 0 and keep30["keep_days"] == 30  # removed 40 days ago
    assert len(list(uri.glob("*.parquet"))) == 3  # a dry run: nothing deleted


def test_vacuum_keeps_the_layers_history_in_its_designation_workspace(vacuum_svc):
    """A layer in a Serve workspace (not the environment's own) keeps its days too."""
    svc, uri, jobs = vacuum_svc
    project = svc._project()
    layers = [l.to_dict() for l in project.layers]
    layers[1]["workspace"] = "serve"
    svc.meta_set_layers(layers)
    project = svc._project()
    envs = {k: dict(v) for k, v in project.environments.items()}
    envs["dev"]["workspaces"] = {"serve": {"id": "ws-serve", "name": "Sales Serve - Dev"}}
    svc._write_environments(project, envs)
    assert svc._xray_keep_days("ws-serve", "LH_Silver", None) == (30, "Keep history on the silver layer")
    assert svc._xray_keep_days("ws-dev", "LH_Silver", None)[0] == 30  # where it may have lived before: kept too
    assert svc._xray_keep_days("ws-other", "LH_Silver", None)[0] == 7


def test_optimize_check_reads_only_and_carries_the_risk_check(vacuum_svc, monkeypatch):
    """QA-032: Optimize asks first, with the risk check for the table's workspace; nothing starts."""
    from fabric_desktop import app_settings
    svc, uri, jobs = vacuum_svc
    monkeypatch.setattr(svc, "_wl_token", lambda _s: "token", raising=False)
    monkeypatch.setattr(svc, "_risk_capacity", lambda *a: None, raising=False)
    out = svc.xray_optimize_check("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver", "Dev")
    assert out["keep_days"] == 30 and jobs == []
    assert out["risk"]["workload"] == "LH_Silver dbo.orders" and out["risk"]["where"] == "fabric"
    assert out["risk"]["result"] == "unknown"  # the capacity wasn't read
    monkeypatch.setattr(app_settings, "value", lambda k: False if k == "allow_fabric_runs" else None)
    assert svc.xray_optimize_check("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver")["risk"]["result"] == "blocked"
    monkeypatch.setattr(svc, "_risk_capacity", lambda *a: (_ for _ in ()).throw(RuntimeError("no network")), raising=False)
    broken = svc.xray_optimize_check("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver")["risk"]
    assert broken["result"] == "unknown" and "no network" in broken["summary"]


def _optimize_capacity(status, cached=False):
    """A capacity reading shaped like _risk_capacity's (capacity.observe_env + freshness)."""
    return {"access": "ok", "status": status, "workspace_id": "ws-dev", "workspace_name": "Dev", "capacity_id": "cap1",
            "capacity": {"name": "fabcapdev", "sku": "F8"}, "age_seconds": 60, "stale": False, "cached": cached,
            "observed_at": "2026-11-01T00:00:00+00:00"}


def test_optimize_checks_the_risk_again_and_records_going_ahead_past_warnings(vacuum_svc, monkeypatch):
    """#1453: Optimize past warnings is recorded like risk_acknowledge; blocked and changed are refused."""
    from fabric_desktop import app_settings
    svc, uri, jobs = vacuum_svc
    args = ("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver", "Dev")
    acks = svc.root / "risk_acknowledgements.jsonl"
    monkeypatch.setattr(svc, "_wl_token", lambda _s: "token", raising=False)
    # risk_acknowledge can't re-check it: the X-ray id isn't a workload id, so the UI can't use it.
    monkeypatch.setattr(svc, "_risk_capacity", lambda *a: _optimize_capacity("running"), raising=False)
    passed = svc.xray_optimize_check(*args)["risk"]
    assert passed["result"] == "pass"
    with pytest.raises(ValueError, match="isn't a workload id"):
        svc.risk_acknowledge(passed["workload_id"], "run_in_fabric", passed["id"])
    svc.xray_optimize(*args)  # a pass needs no risk id and records nothing
    assert len(jobs) == 1 and not acks.exists()

    monkeypatch.setattr(svc, "_risk_capacity", lambda *a: _optimize_capacity("not_running", cached=True), raising=False)
    warned = svc.xray_optimize_check(*args)["risk"]
    assert warned["result"] == "warning"
    with pytest.raises(ValueError, match="Check the risks first"):
        svc.xray_optimize(*args)  # no risk id: going ahead past warnings must name the ones seen
    with pytest.raises(ValueError, match="findings changed"):
        svc.xray_optimize(*args, risk_id=passed["id"])  # an assessment that isn't the current one
    assert len(jobs) == 1 and not acks.exists()
    real_write = svc._risk_ack_write

    def broken_write(_record):
        raise OSError("disk full")
    monkeypatch.setattr(svc, "_risk_ack_write", broken_write)
    with pytest.raises(OSError, match="disk full"):  # the record is written first: a failed write starts nothing
        svc.xray_optimize(*args, risk_id=warned["id"])
    assert len(jobs) == 1
    monkeypatch.setattr(svc, "_risk_ack_write", real_write)
    svc.xray_optimize(*args, risk_id=warned["id"])
    assert len(jobs) == 2
    (rec,) = svc.risk_acknowledgements()
    assert rec["risk_id"] == warned["id"] and rec["workload_id"] == "xray:ws-dev/lh-silver/dbo.orders"
    assert rec["workload"] == "LH_Silver dbo.orders" and rec["action"] == "run_in_fabric" and rec["env"] == ""
    assert rec["result"] == "warning" and rec["warnings"] == ["fabcapdev was paused when last read"]
    assert set(rec) == {"at", "risk_id", "workload_id", "workload", "action", "env", "result", "user", "warnings", "note"}

    monkeypatch.setattr(app_settings, "value", lambda k: False if k == "allow_fabric_runs" else None)
    blocked = svc.xray_optimize_check(*args)["risk"]
    assert blocked["result"] == "blocked"
    with pytest.raises(PermissionError, match="can't be started"):
        svc.xray_optimize(*args, risk_id=blocked["id"])
    assert len(jobs) == 2 and len(svc.risk_acknowledgements()) == 1
