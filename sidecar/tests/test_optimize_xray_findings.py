"""X-ray scans record their findings in the central lakehouse (KF-DE-040), once per problem."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from fabric_desktop import app_settings, xray, xray_service
from fabric_desktop.optimize import schema, setup
from fabric_desktop.optimize.lakehouse import LocalLakehouse
from fabric_desktop.service import Service

NOW = dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.timezone.utc)


def scan_result() -> dict:
    """What a scan of one workspace finds: a table, an empty lakehouse, a guest and a cost."""
    where = {"workspace_id": "ws1", "workspace_name": "Sales", "lakehouse_id": "lh1", "lakehouse_name": "LH_Raw"}
    findings = [
        where | {"schema": "dbo", "table": "orders", "kind": "small_files", "severity": "high",
                 "title": "dbo.orders has 250 small files", "detail": "Compact it.", "action": "optimize",
                 "files": 250, "bytes": 1000},
        {"workspace_id": "ws1", "workspace_name": "Sales", "lakehouse_id": "lh2", "lakehouse_name": "LH_Empty",
         "kind": "empty", "severity": "low", "title": "LH_Empty is empty", "detail": "Delete it.", "action": "open"},
        {"kind": "access", "severity": "medium", "title": "Guest Pat has access to 1 workspace", "detail": "x",
         "action": "access", "workspace_id": "ws1", "person": "Pat", "check": "guest"},
        {"kind": "access", "severity": "medium", "title": "App Pat is Admin in 1 workspace", "detail": "x",
         "action": "access", "workspace_id": "ws1", "person": "Pat", "check": "admin_app"},
        {"kind": "cost", "severity": "low", "title": "nb cost about $1.00", "detail": "x", "action": "cost",
         "notebook": "nb", "cost": 1.0},
    ]
    return {"scanned": NOW.isoformat(), "workspaces": 1, "lakehouses": 2, "tables": 1, "partial": False,
            "skipped": [], "bytes": 1000, "findings": findings,
            "lakehouse_rows": [where | {"tables": 1, "bytes": 1000, "files": 250, "issues": 1},
                               {"workspace_id": "ws1", "workspace_name": "Sales", "lakehouse_id": "lh2",
                                "lakehouse_name": "LH_Empty", "tables": 0, "bytes": 0, "files": 0, "issues": 1}]}


class FakeScanner:
    def __init__(self, *a, **k):
        pass

    def scan(self):
        return scan_result()


class FakeFabric:
    def list_workspaces(self):
        return [{"id": "ws1", "displayName": "Sales"}, {"id": "central", "displayName": "Platform"}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    monkeypatch.setattr(xray_service.xray, "Scanner", FakeScanner)
    monkeypatch.setattr(Service, "fabric", property(lambda self: FakeFabric()))
    monkeypatch.setattr(Service, "cost_report", lambda self, days: (_ for _ in ()).throw(RuntimeError("no run logs")))
    lake = LocalLakehouse(tmp_path / "onelake" / "central" / "lh-central", True)
    tokens = []

    def optimize_lake(self, ws, lh, schemas=None, token=None):
        tokens.append(token)
        return lake

    monkeypatch.setattr(Service, "_optimize_lake", optimize_lake)

    def pc(name: str, connect: bool = True) -> Service:
        s = Service(root=tmp_path / name)
        if connect:
            setup.initialize(lake, s.optimize_installation, approve=True)
            s.optimize_registry.add({"workspace_id": "central", "workspace_name": "Platform",
                                     "lakehouse_id": "lh-central", "lakehouse_name": "FabricDesktop_Central",
                                     "schemas": True, "access": "write"})
        return s

    return pc, lake, tokens


def rows(lake, name):
    df = lake.rows(schema.table(name))
    return 0 if df is None else len(df)


def scan(svc: Service) -> dict:
    svc._xray_run("fabric-token", "storage-token")
    return svc._xray_saved()


def test_a_scan_records_its_findings_once(env):
    pc, lake, tokens = env
    svc = pc("a")
    result = scan(svc)
    assert result["central"] == {"recorded": 4, "lakehouse_name": "FabricDesktop_Central"}
    assert len(result["findings"]) == 5  # the scan result itself is unchanged
    assert tokens and all(t is not None and t() == "storage-token" for t in tokens)  # no MSAL on the scan thread
    found = svc.optimize_findings()
    assert len(found) == 4  # cost findings name a notebook only by name, so they stay on this PC
    by_rule = {f["rule_id"]: f for f in found}
    assert set(by_rule) == {"xray.small_files", "xray.empty", "xray.access.guest", "xray.access.admin_app"}
    small = by_rule["xray.small_files"]
    assert small["artifact_key"] == "ws1/lh1" and small["target"] == "dbo.orders" and small["severity"] == "high"
    assert json.loads(small["evidence"]) == {"bytes": 1000, "files": 250}
    assert by_rule["xray.access.guest"]["artifact_key"] == "ws1/ws1" and by_rule["xray.access.guest"]["target"] == "Pat"
    run = lake.rows(schema.table("analysis_runs")).to_dicts()[0]
    assert (run["status"], run["scope"], run["rule_set_version"], run["findings"], run["artifacts_scanned"]) == (
        "complete", "estate", "xray-1", 4, 2)


def test_a_repeat_scan_adds_no_new_finding(env):
    pc, lake, _ = env
    svc = pc("a")
    scan(svc)
    scan(svc)
    found = svc.optimize_findings()
    assert len(found) == 4 and {f["occurrences"] for f in found} == {2}
    assert rows(lake, "analysis_runs") == 2


def test_a_second_installation_finding_the_same_problems_dedupes(env):
    pc, _, _ = env
    a, b = pc("a"), pc("b")
    scan(a)
    scan(b)
    found = b.optimize_findings()
    assert len(found) == 4 and {f["installations"] for f in found} == {2}


def test_not_connected_writes_nothing(env):
    pc, lake, tokens = env
    svc = pc("a", connect=False)
    result = scan(svc)
    assert "central" not in result and len(result["findings"]) == 5
    assert tokens == [] and rows(lake, "findings") == 0


def test_policy_off_writes_nothing(env, tmp_path):
    pc, lake, tokens = env
    svc = pc("a")
    (tmp_path / "policy.json").write_text(json.dumps({"allow_optimize_lakehouse": False}))
    result = scan(svc)
    assert "central" not in result and tokens == [] and rows(lake, "findings") == 0


def test_a_failed_save_leaves_the_scan_with_a_note(env, monkeypatch, caplog):
    pc, lake, _ = env
    svc = pc("a")

    def broken(*a, **k):
        raise OSError("OneLake said 503 for token=abc.def.ghi")

    monkeypatch.setattr(xray_service.xray, "central_rows", broken)
    result = scan(svc)
    assert result["central"]["recorded"] == 0
    assert result["central"]["error"].startswith("Findings weren't saved to the central lakehouse: OSError")
    assert len(result["findings"]) == 5 and result["counts"]["small_files"] == 1
    assert svc._xray_state == {"running": False}  # the scan itself didn't fail
    assert rows(lake, "findings") == 0
    assert "weren't saved" in caplog.text


def test_a_read_only_lakehouse_is_not_written(env):
    pc, lake, _ = env
    svc = pc("a")
    svc.optimize_registry.add({**svc.optimize_registry.active(), "access": "read"})
    result = scan(svc)
    assert "read-only" in result["central"]["error"] and rows(lake, "findings") == 0


def test_central_rows_mark_a_partial_scan():
    run, arts, found = xray.central_rows(scan_result() | {"partial": True}, "r1", NOW)
    assert run["status"] == "partial" and run["run_id"] == "r1" and len(arts) == 2 and len(found) == 4
